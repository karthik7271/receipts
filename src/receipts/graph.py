"""Keep the code graph in Relate in step with the working tree, and let the
graph decide which memories are still true.

The interesting part is ``recompute_status``. Staleness is not a per-fact
check. It *propagates* through the graph:

* **stale**: a fact's own receipt changed (symbol body/signature changed or
  deleted, quoted text gone, cited file edited).
* **suspect / upstream drift**: a cited symbol *calls* (1–2 hops) a symbol
  whose signature changed after the fact was recorded.
* **suspect / derived**: the fact was derived from a fact that is stale,
  suspect, retracted or superseded, however many hops up the chain.

All three are Cypher traversals over CITES, CALLS, DERIVED_FROM and Change.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .codegraph import (HASH_VERSION, FileInfo, SymbolInfo, hash_snippet, list_files, module_index, read_text,
                        resolve_imports, scan_repo, scan_text, sha, stat_file)
from .store import Store, now_iso

SNAPSHOT_MAX = 4000
BATCH = 500


def _batched(store: Store, cypher: str, rows: list, *, size: int | None = None, **params) -> None:
    """Run an UNWIND statement in chunks, committing between chunks of a large job."""
    if not rows:
        return
    size = min(size or BATCH, BATCH)
    for i in range(0, len(rows), size):
        store.q(cypher, rows=rows[i:i + size], **params)
        if len(rows) > size:
            store.checkpoint()


@dataclass
class SyncReport:
    sync_id: str | None = None
    files_scanned: int = 0
    files_changed: list[str] = field(default_factory=list)
    changes: list[dict] = field(default_factory=list)
    moved: list[tuple[str, str]] = field(default_factory=list)
    transitions: list[dict] = field(default_factory=list)

    @property
    def newly_invalid(self) -> list[dict]:
        return [t for t in self.transitions if t["after"] in ("stale", "suspect") and t["before"] == "fresh"]


def git_head(root: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def tick(store: Store) -> int:
    row = store.one("MATCH (m:Meta {key: 'receipts'}) SET m.tick = coalesce(m.tick, 0) + 1 RETURN m.tick AS t")
    return int(row["t"])


# --------------------------------------------------------------------------- #
# Sync
# --------------------------------------------------------------------------- #


def sync(store: Store, root: Path, only: list[str] | None = None, *, recompute: bool = True) -> SyncReport:
    """Bring the stored code graph up to date with ``root``.

    ``only`` limits the scan to some repo-relative paths (used after an edit,
    or to refresh just the files a recall touches). A full scan also notices
    deleted files.
    """
    report = SyncReport()
    _maybe_migrate(store, root)
    stored = {r["path"]: r for r in store.q(
        "MATCH (f:File) RETURN f.path AS path, f.hash AS hash, f.deleted AS deleted, f.size AS size, "
        "f.mtime AS mtime")}

    if only is None:
        candidates = list_files(root)
        present = set(candidates)
        removed = [p for p, r in stored.items() if not r["deleted"] and p not in present]
    else:
        wanted = sorted({p for p in only if p})
        candidates = [p for p in wanted if (root / p).is_file()]
        removed = [p for p in wanted if p not in candidates and p in stored and not stored[p]["deleted"]]
    report.files_scanned = len(candidates)

    # Cheap checks first, so a sync of a large unchanged repo costs a stat() per
    # file: same size and mtime means unchanged; same content hash means
    # unchanged (only the timestamp moved). Only real changes get parsed.
    changed: dict[str, FileInfo] = {}
    touched_only: list[dict] = []
    for rel in candidates:
        st = stat_file(root, rel)
        if st is None:
            continue
        old = stored.get(rel)
        live = old is not None and not old["deleted"]
        if live and old["size"] == st[0] and old["mtime"] == st[1]:
            continue
        text = read_text(root, rel)
        if text is None:
            continue
        if live and old["hash"] == sha(text):
            touched_only.append({"path": rel, "size": st[0], "mtime": st[1]})
            continue
        fi = scan_text(rel, text)
        fi.size, fi.mtime = st
        changed[rel] = fi
    _batched(store, "UNWIND $rows AS r MATCH (f:File {path: r.path}) SET f.size = r.size, f.mtime = r.mtime",
             touched_only)
    if not changed and not removed:
        if recompute:
            report.transitions = recompute_status(store)
        return report

    t = tick(store)
    at = now_iso()
    report.sync_id = store.next_id("next_sync", "S")
    report.files_changed = sorted([*changed, *removed])
    commit = git_head(root)
    store.q("CREATE (s:Sync {id: $id, at: $at, tick: $t, commit: $c, files_changed: $n})",
            id=report.sync_id, at=at, t=t, c=commit, n=len(report.files_changed))

    touched = sorted([*changed, *removed])
    old_syms = {r["key"]: r for r in store.q(
        "MATCH (s:Symbol) WHERE s.path IN $paths AND s.deleted = false "
        "RETURN s.key AS key, s.kind AS kind, s.qualname AS qualname, s.path AS path, "
        "s.sig_hash AS sig_hash, s.body_hash AS body_hash, s.types_hash AS types_hash", paths=touched)}
    new_syms: dict[str, SymbolInfo] = {s.key: s for fi in changed.values() for s in fi.symbols}

    # ---- diff symbols ------------------------------------------------------ #
    changes: list[dict] = []
    for key, s in new_syms.items():
        old = old_syms.get(key)
        if old is None:
            continue
        if old["sig_hash"] != s.sig_hash:
            changes.append({"kind": "signature", "key": key, "detail": f"{s.qualname}: signature is now {s.signature}"})
            continue
        if old["body_hash"] != s.body_hash:
            changes.append({"kind": "body", "key": key, "detail": f"{s.qualname}: implementation changed"})
        if old.get("types_hash") is not None and old["types_hash"] != s.types_hash:
            changes.append({"kind": "types", "key": key, "detail": f"{s.qualname}: type hints are now {s.signature}"})

    vanished = [k for k in old_syms if k not in new_syms]
    fresh_keys = [k for k in new_syms if k not in old_syms]
    by_body: dict[tuple[str, str], list[str]] = {}
    for k in fresh_keys:
        s = new_syms[k]
        if len(s.source) >= 40:
            by_body.setdefault((s.kind, s.body_hash), []).append(k)
    moved: list[tuple[str, str]] = []
    for k in vanished:
        old = old_syms[k]
        cands = by_body.get((old["kind"], old["body_hash"]), [])
        if not cands:
            # Short symbols (constants, one-liners) only count as moved when
            # the name survives too.
            cands = [n for n in fresh_keys if new_syms[n].kind == old["kind"]
                     and new_syms[n].body_hash == old["body_hash"] and new_syms[n].qualname == old["qualname"]]
        if len(cands) == 1:
            moved.append((k, cands[0]))
            changes.append({"kind": "moved", "key": k, "detail": f"{old['qualname']} moved to {cands[0]}"})
        else:
            changes.append({"kind": "deleted", "key": k, "detail": f"{old['qualname']} was deleted"})
    report.moved = moved
    # File-level changes only matter for files we knew before (a first scan is not a change).
    for p in changed:
        if p in stored:
            changes.append({"kind": "file", "path": p, "detail": f"{p} changed"})
    for p in removed:
        changes.append({"kind": "file", "path": p, "detail": f"{p} was deleted"})

    # ---- phase A: upsert files, symbols, changes ---------------------------- #
    # Written in committed batches: Relate caps a single commit at 64 MiB, and
    # a large repository's first scan is far bigger than that.
    _batched(store, "UNWIND $rows AS r MERGE (f:File {path: r.path}) ON CREATE SET f.hash = '', f.deleted = false",
             [{"path": p} for p in changed])
    if removed:
        store.q("MATCH (f:File) WHERE f.path IN $paths SET f.deleted = true, f.tick = $t", paths=removed, t=t)
    _batched(store,
             "UNWIND $rows AS r MERGE (s:Symbol {key: r.key}) "
             "SET s.name = r.name, s.qualname = r.qualname, s.kind = r.kind, s.path = r.path, "
             "    s.start = r.start, s.end = r.end, s.signature = r.signature, s.sig_hash = r.sig_hash, "
             "    s.body_hash = r.body_hash, s.types_hash = r.types_hash, s.source = r.source, s.deleted = false, "
             "    s.tick = $t "
             "WITH s, r MATCH (f:File {path: r.path}) MERGE (f)-[:DEFINES]->(s)",
             [{"key": s.key, "name": s.name, "qualname": s.qualname, "kind": s.kind, "path": s.path,
               "start": s.start, "end": s.end, "signature": s.signature, "sig_hash": s.sig_hash,
               "body_hash": s.body_hash, "types_hash": s.types_hash, "source": s.source[:SNAPSHOT_MAX]}
              for s in new_syms.values()],
             size=200, t=t)
    if vanished:
        _batched(store, "UNWIND $rows AS k MATCH (s:Symbol {key: k}) SET s.deleted = true, s.deleted_tick = $t",
                 vanished, t=t)
    if moved:
        store.q("UNWIND $pairs AS p MATCH (a:Symbol {key: p[0]}), (b:Symbol {key: p[1]}) MERGE (a)-[:MOVED_TO]->(b)",
                pairs=[list(m) for m in moved])

    first = store.next_ids("next_change", len(changes))
    change_rows = [{**c, "id": f"C{first + i}"} for i, c in enumerate(changes)]
    report.changes = change_rows
    _batched(store,
             "UNWIND $rows AS r CREATE (c:Change {id: r.id, kind: r.kind, detail: r.detail, at: $at, tick: $t, "
             "commit: $commit, sync: $sync}) "
             "WITH c, r MATCH (s:Sync {id: $sync}) CREATE (s)-[:RECORDED]->(c)",
             change_rows, at=at, t=t, commit=commit, sync=report.sync_id)
    _batched(store, "UNWIND $rows AS r MATCH (c:Change {id: r.id}), (s:Symbol {key: r.key}) CREATE (c)-[:ON]->(s)",
             [r for r in change_rows if r.get("key")])
    _batched(store, "UNWIND $rows AS r MATCH (c:Change {id: r.id}), (f:File {path: r.path}) CREATE (c)-[:ON]->(f)",
             [r for r in change_rows if r.get("path")])

    _check_quotes(store, changed, removed)

    # File fingerprints go last: if anything above fails, the next sync sees
    # the old fingerprint and simply redoes these files.
    _batched(store,
             "UNWIND $rows AS r MATCH (f:File {path: r.path}) "
             "SET f.hash = r.hash, f.lang = r.lang, f.lines = r.lines, f.size = r.size, f.mtime = r.mtime, "
             "    f.deleted = false, f.tick = $t, f.updated = $at",
             [{"path": fi.path, "hash": fi.hash, "lang": fi.lang, "lines": fi.lines, "size": fi.size,
               "mtime": fi.mtime} for fi in changed.values()], t=t, at=at)

    # Relate 1.0.2 panics when a statement deletes something created earlier
    # in the same open transaction, so commit before the delete phase.
    store.checkpoint()

    # ---- phase B: rebuild edges for changed files, follow renames ---------- #
    _rebuild_edges(store, changed, removed, vanished)
    if moved:
        store.q(
            "MATCH (f:Fact)-[c:CITES]->(old:Symbol)-[:MOVED_TO]->(new:Symbol) "
            "WHERE old.deleted = true AND new.deleted = false "
            "CREATE (f)-[c2:CITES]->(new) SET c2 += properties(c), "
            "  c2.followed = coalesce(c.followed, []) + [old.key] "
            "DELETE c")
        store.q(
            "MATCH (caller:Symbol)-[r:CALLS]->(old:Symbol)-[:MOVED_TO]->(new:Symbol) "
            "WHERE old.deleted = true AND new.deleted = false "
            "MERGE (caller)-[:CALLS]->(new) DELETE r")

    if recompute:
        report.transitions = recompute_status(store)
    return report


def _maybe_migrate(store: Store, root: Path) -> None:
    """Bring a store built with an older hashing scheme up to HASH_VERSION.

    Symbols are re-hashed from the working tree; each symbol receipt is
    re-hashed from the snapshot saved when it was cited, so a memory that was
    stale stays stale and a fresh one stays fresh. No Change records are
    written: the code didn't change, only how we fingerprint it.
    """
    row = store.one("MATCH (m:Meta {key: 'receipts'}) RETURN m.hash_version AS v")
    if row is None or row["v"] == HASH_VERSION:
        return
    if store.one("MATCH (s:Symbol) RETURN s.key AS k LIMIT 1"):
        cites = store.q("MATCH (f:Fact)-[c:CITES {mode: 'symbol'}]->(s:Symbol) "
                        "RETURN f.id AS fid, s.key AS key, c.snapshot AS snap, c.hash = s.body_hash AS was_fresh")
        paths = [r["p"] for r in store.q("MATCH (s:Symbol) WHERE s.deleted = false RETURN DISTINCT s.path AS p")]
        scanned = scan_repo(root, [p for p in paths if (root / p).is_file()])
        now = {s.key: s for fi in scanned.values() for s in fi.symbols}
        _batched(store, "UNWIND $rows AS r MATCH (s:Symbol {key: r.key}) "
                        "SET s.sig_hash = r.sig, s.body_hash = r.body, s.types_hash = r.types",
                 [{"key": k, "sig": s.sig_hash, "body": s.body_hash, "types": s.types_hash} for k, s in now.items()])
        updates = []
        for c in cites:
            h = hash_snippet(c["snap"])
            if h is None and c["was_fresh"] and c["key"] in now:
                h = (now[c["key"]].sig_hash, now[c["key"]].body_hash)
            if h:
                updates.append({"fid": c["fid"], "key": c["key"], "sig": h[0], "body": h[1]})
        _batched(store, "UNWIND $rows AS r MATCH (:Fact {id: r.fid})-[c:CITES {mode: 'symbol'}]->(:Symbol {key: r.key}) "
                        "SET c.hash = r.body, c.sig_hash = r.sig", updates)
    store.q("MATCH (m:Meta {key: 'receipts'}) SET m.hash_version = $v", v=HASH_VERSION)


def _check_quotes(store: Store, changed: dict[str, FileInfo], removed: list[str]) -> None:
    """Quote-anchored receipts: is the quoted text still in the file?"""
    rows = store.q(
        "MATCH (f:Fact)-[c:CITES {mode: 'quote'}]->(x:File) WHERE x.path IN $paths "
        "RETURN f.id AS fid, x.path AS path, c.quote AS quote",
        paths=[*changed, *removed])
    updates = []
    for r in rows:
        fi = changed.get(r["path"])
        ok, line = False, None
        if fi is not None:
            line = find_quote(fi.text, r["quote"])
            ok = line is not None
        updates.append({"fid": r["fid"], "path": r["path"], "quote": r["quote"], "ok": ok, "line": line})
    store.q(
        "UNWIND $rows AS r MATCH (f:Fact {id: r.fid})-[c:CITES {mode: 'quote'}]->(x:File {path: r.path}) "
        "WHERE c.quote = r.quote SET c.ok = r.ok, c.line = coalesce(r.line, c.line)",
        rows=updates)


_WORD = re.compile(r"[A-Za-z0-9_]")


def _bounded_find(hay: str, needle: str) -> int:
    """Index of ``needle`` in ``hay`` on token boundaries, else -1.

    ``tax_rate: 0.08`` must *not* match inside ``tax_rate: 0.0825``.
    """
    start = 0
    while True:
        i = hay.find(needle, start)
        if i < 0:
            return -1
        j = i + len(needle)
        left_ok = i == 0 or not (_WORD.match(needle[0]) and _WORD.match(hay[i - 1]))
        right_ok = j >= len(hay) or not (_WORD.match(needle[-1]) and _WORD.match(hay[j]))
        if left_ok and right_ok:
            return i
        start = i + 1


def find_quote(text: str, quote: str) -> int | None:
    """1-based line where ``quote`` starts, ignoring whitespace differences."""
    if not quote:
        return None
    idx = _bounded_find(text, quote)
    if idx < 0:
        norm = " ".join(quote.split())
        lines = text.splitlines()
        for i in range(len(lines)):
            window = " ".join(" ".join(lines[i:i + max(1, quote.count("\n") + 1)]).split())
            if norm and _bounded_find(window, norm) >= 0:
                return i + 1
        return None
    return text.count("\n", 0, idx) + 1


def _rebuild_edges(store: Store, changed: dict[str, FileInfo], removed: list[str], vanished: list[str]) -> None:
    paths = sorted([*changed, *removed])
    _batched(store, "MATCH (s:Symbol)-[r:CALLS]->() WHERE s.path IN $rows DELETE r", paths)
    _batched(store, "MATCH (f:File)-[r:IMPORTS]->() WHERE f.path IN $rows DELETE r", paths)
    if not changed:
        return

    all_paths = [r["path"] for r in store.q("MATCH (f:File) WHERE f.deleted = false RETURN f.path AS path")]
    mods = module_index(all_paths)
    path_set = set(all_paths)
    imports: dict[str, list[str]] = {}
    import_rows = []
    for fi in changed.values():
        targets = resolve_imports(fi, mods, path_set)
        imports[fi.path] = targets
        import_rows.extend({"a": fi.path, "b": t} for t in targets)
    _batched(store, "UNWIND $rows AS r MATCH (a:File {path: r.a}), (b:File {path: r.b}) MERGE (a)-[:IMPORTS]->(b)",
             import_rows)

    index: dict[str, list[tuple[str, str, str]]] = {}
    for r in store.q("MATCH (s:Symbol) WHERE s.deleted = false "
                     "RETURN s.key AS key, s.name AS name, s.path AS path, s.qualname AS qualname"):
        index.setdefault(r["name"], []).append((r["key"], r["path"], r["qualname"]))

    call_rows = []
    for fi in changed.values():
        imported = set(imports.get(fi.path, []))
        for s in fi.symbols:
            cls = s.qualname.split(".")[0] if "." in s.qualname else None
            for name in s.calls:
                cands = [c for c in index.get(name, []) if c[0] != s.key]
                if not cands:
                    continue
                pick = None
                same_class = [c for c in cands if cls and c[1] == fi.path and c[2].startswith(cls + ".")]
                same_file = [c for c in cands if c[1] == fi.path]
                via_import = [c for c in cands if c[1] in imported]
                for group in (same_class, same_file, via_import, cands):
                    if len(group) == 1:
                        pick = group[0]
                        break
                    if group:
                        break
                if pick:
                    call_rows.append({"a": s.key, "b": pick[0]})
    _batched(store, "UNWIND $rows AS r MATCH (a:Symbol {key: r.a}), (b:Symbol {key: r.b}) MERGE (a)-[:CALLS]->(b)",
             call_rows)


# --------------------------------------------------------------------------- #
# Truth maintenance
# --------------------------------------------------------------------------- #

LIVE = ["fresh", "stale", "suspect"]


def recompute_status(store: Store) -> list[dict]:
    """Re-derive every live fact's status from the graph. Returns transitions."""
    before = {r["id"]: r["status"] for r in store.q("MATCH (f:Fact) RETURN f.id AS id, f.status AS status")}
    store.q("MATCH (f:Fact) WHERE f.status IN $live SET f.status = 'fresh', f.reasons = []", live=LIVE)

    # 1. Direct: the receipt itself no longer matches the code.
    store.q("""
        MATCH (f:Fact {status: 'fresh'})-[c:CITES {mode: 'symbol'}]->(s:Symbol)
        WHERE s.deleted = true OR s.body_hash <> c.hash
        WITH f, collect(CASE
            WHEN s.deleted = true THEN s.qualname + ' was deleted'
            WHEN s.sig_hash <> c.sig_hash THEN s.qualname + ' changed signature'
            ELSE s.qualname + ' changed' END) AS why
        SET f.status = 'stale', f.reasons = f.reasons + why""")
    store.q("""
        MATCH (f:Fact)-[c:CITES {mode: 'quote'}]->(x:File)
        WHERE f.status IN ['fresh', 'stale'] AND (x.deleted = true OR c.ok = false)
        WITH f, collect('quoted text is gone from ' + x.path) AS why
        SET f.status = 'stale', f.reasons = f.reasons + why""")
    store.q("""
        MATCH (f:Fact)-[c:CITES {mode: 'file'}]->(x:File)
        WHERE f.status IN ['fresh', 'stale'] AND (x.deleted = true OR x.hash <> c.hash)
        WITH f, collect(x.path + CASE WHEN x.deleted THEN ' was deleted' ELSE ' changed' END) AS why
        SET f.status = 'stale', f.reasons = f.reasons + why""")

    # 2. Upstream drift: code the receipt depends on changed its contract
    #    (parameters or type hints), up to two calls away.
    store.q("""
        MATCH (f:Fact {status: 'fresh'})-[c:CITES {mode: 'symbol'}]->(s:Symbol)-[:CALLS]->{1,2}(t:Symbol)
        OPTIONAL MATCH (t)<-[:ON]-(ch:Change) WHERE ch.kind IN ['signature', 'types'] AND ch.tick > c.tick
        WITH f, s, t, c, collect(ch.kind) AS kinds
        WHERE size(kinds) > 0 OR (t.deleted = true AND t.deleted_tick > c.tick)
        WITH f, collect(DISTINCT s.qualname + ' depends on ' + t.qualname + CASE
            WHEN t.deleted = true THEN ', which was deleted'
            WHEN 'signature' IN kinds THEN ', whose signature changed'
            ELSE ', whose type hints changed' END) AS why
        SET f.status = 'suspect', f.reasons = f.reasons + why""")

    # 3. Derived: anything built on a fact that is no longer good, any depth.
    store.q("""
        MATCH (f:Fact)-[:DERIVED_FROM]->+(b:Fact)
        WHERE f.status IN $live AND b.status IN ['stale', 'suspect', 'retracted', 'superseded']
        WITH f, collect(DISTINCT b.id + ' (' + b.status + ')') AS bad
        SET f.status = CASE WHEN f.status = 'fresh' THEN 'suspect' ELSE f.status END,
            f.reasons = f.reasons + ['builds on ' + reduce(s = '', x IN bad |
                s + CASE WHEN s = '' THEN '' ELSE ', ' END + x)]""", live=LIVE)

    # Link invalid facts to the concrete changes that broke them.
    store.q("""
        MATCH (f:Fact)-[c:CITES]->(e)<-[:ON]-(ch:Change)
        WHERE f.status = 'stale' AND ch.tick > c.tick AND NOT ch.kind IN ['moved', 'types']
        MERGE (f)-[:INVALIDATED_BY]->(ch)""")

    after = {r["id"]: (r["status"], r["reasons"]) for r in store.q(
        "MATCH (f:Fact) RETURN f.id AS id, f.status AS status, f.reasons AS reasons")}
    return [{"id": k, "before": before.get(k), "after": v[0], "reasons": v[1] or []}
            for k, v in after.items() if before.get(k) != v[0]]
