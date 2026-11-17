"""What an agent does with memory: remember, recall, verify, maintain.

Every operation runs against one open ``Store`` (one transaction) and first
refreshes the slice of the code graph it is about to rely on, so a recall
never returns a receipt that was checked against yesterday's code.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .codegraph import read_text, sha
from .graph import SNAPSHOT_MAX, find_quote, recompute_status, sync, tick
from .store import Store, now_iso

KINDS = ("fact", "convention", "decision", "gotcha", "howto", "preference")
INACTIVE = ("retracted", "superseded")


class MemoryError_(ValueError):
    """A request the agent can fix (bad citation, unknown id...)."""


# --------------------------------------------------------------------------- #
# Citations
# --------------------------------------------------------------------------- #


@dataclass
class Citation:
    mode: str                     # symbol | quote | file | external
    target: str                   # symbol key, file path, or external key
    label: str
    hash: str = ""
    sig_hash: str = ""
    quote: str = ""
    line: int | None = None
    snapshot: str = ""
    extra: dict = field(default_factory=dict)


def parse_cite(raw: Any) -> dict:
    """Accept ``{"symbol": ...}``, ``{"path": ..., "quote": ...}``, ``{"url": ...}``,
    ``{"note": ...}`` or a string shorthand: ``"pricing.py::apply_discount"``,
    ``"apply_discount"`` (a symbol name) or ``"config/settings.yaml"`` (a path)."""
    if isinstance(raw, dict):
        return {k: v for k, v in raw.items() if v not in (None, "")}
    s = str(raw).strip()
    if "::" in s:
        return {"symbol": s}
    if "/" in s or re.search(r"\.[A-Za-z0-9]{1,6}$", s):
        return {"path": s}
    return {"symbol": s}


def _rel(root: Path, path: str) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            return p.resolve().relative_to(root).as_posix()
        except ValueError:
            return p.as_posix()
    return p.as_posix().lstrip("./") if not path.startswith("../") else p.as_posix()


def resolve_symbol(store: Store, name: str, path: str | None = None) -> list[dict]:
    """Find live symbols by key, qualname or bare name (optionally within a path)."""
    rows = store.q(
        "MATCH (s:Symbol) WHERE s.deleted = false AND (s.key = $n OR s.qualname = $n OR s.name = $n) "
        "AND ($path IS NULL OR s.path = $path OR s.path ENDS WITH $path) "
        "RETURN s.key AS key, s.qualname AS qualname, s.path AS path, s.kind AS kind, s.body_hash AS body_hash, "
        "s.sig_hash AS sig_hash, s.source AS source, s.start AS start, s.signature AS signature "
        "ORDER BY size(s.key)", n=name, path=path)
    exact = [r for r in rows if r["key"] == name]
    if exact:
        return exact
    if "::" in name:
        p, q = name.split("::", 1)
        return resolve_symbol(store, q, p)
    qual = [r for r in rows if r["qualname"] == name]
    return qual or rows


def resolve_cite(store: Store, root: Path, raw: Any) -> Citation:
    c = parse_cite(raw)
    if "symbol" in c:
        path = _rel(root, c["path"]) if c.get("path") else None
        matches = resolve_symbol(store, str(c["symbol"]), path)
        if not matches:
            hint = store.q(
                "MATCH (s:Symbol) WHERE s.deleted = false AND toLower(s.name) CONTAINS toLower($n) "
                "RETURN s.key AS key LIMIT 5", n=str(c["symbol"]).split("::")[-1].split(".")[-1])
            raise MemoryError_(f"no symbol '{c['symbol']}' in this repo"
                               + (f"; did you mean: {', '.join(h['key'] for h in hint)}" if hint else ""))
        if len(matches) > 1:
            raise MemoryError_(f"'{c['symbol']}' is ambiguous: {', '.join(m['key'] for m in matches[:6])}. "
                               "Cite it as <path>::<name>.")
        m = matches[0]
        return Citation("symbol", m["key"], m["key"], hash=m["body_hash"], sig_hash=m["sig_hash"],
                        snapshot=(m["source"] or "")[:SNAPSHOT_MAX], line=m["start"])
    if "path" in c:
        rel = _rel(root, str(c["path"]))
        text = read_text(root, rel)
        if text is None:
            raise MemoryError_(f"file not found: {rel}")
        if c.get("quote"):
            quote = str(c["quote"]).strip("\n")
            line = find_quote(text, quote)
            if line is None:
                raise MemoryError_(f"quote not found in {rel}: {quote[:80]!r}. Quote the file verbatim.")
            lines = text.splitlines()
            n = quote.count("\n") + 1
            snap = "\n".join(lines[max(0, line - 3): line - 1 + n + 2])
            return Citation("quote", rel, f"{rel}:{line}", hash=sha(quote), quote=quote, line=line, snapshot=snap)
        return Citation("file", rel, rel, hash=sha(text), snapshot=text[:SNAPSHOT_MAX])
    if "url" in c or "note" in c or "commit" in c:
        kind = "url" if "url" in c else ("commit" if "commit" in c else "note")
        val = str(c.get("url") or c.get("commit") or c.get("note"))
        return Citation("external", f"{kind}:{val[:200]}", val[:120], extra={"kind": kind})
    raise MemoryError_(f"unrecognised citation: {raw!r}")


def _sync_for(store: Store, root: Path, cites: list[Any]) -> None:
    """Refresh just the files a set of raw citations points at."""
    paths: set[str] = set()
    for raw in cites:
        c = parse_cite(raw)
        if c.get("path"):
            paths.add(_rel(root, str(c["path"])))
        sym = str(c.get("symbol", ""))
        if "::" in sym:
            paths.add(sym.split("::", 1)[0])
    if paths:
        sync(store, root, only=sorted(paths), recompute=False)


# --------------------------------------------------------------------------- #
# Writing memories
# --------------------------------------------------------------------------- #


def _create_fact(store: Store, root: Path, text: str, cites: list[Any], kind: str, derived_from: list[str],
                 tags: list[str], author: str, session: str | None) -> tuple[str, list[Citation]]:
    text = (text or "").strip()
    if not text:
        raise MemoryError_("a memory needs text")
    if kind not in KINDS:
        raise MemoryError_(f"kind must be one of {', '.join(KINDS)}")
    if not cites:
        raise MemoryError_("a memory needs at least one receipt: cite a symbol, a quoted line, a file, "
                           "or {'note': '...'} for something only the user can vouch for")
    citations = [resolve_cite(store, root, c) for c in cites]
    for d in derived_from:
        if not store.one("MATCH (f:Fact {id: $id}) RETURN f.id AS id", id=d):
            raise MemoryError_(f"derived_from: no memory {d}")

    t = tick(store)
    fid = store.next_id("next_fact", "M")
    names = " ".join(c.label.split("::")[-1] for c in citations if c.mode == "symbol")
    store.q(
        "CREATE (f:Fact {id: $id, text: $text, kind: $kind, tags: $tags, search_text: $st, status: 'fresh', "
        "reasons: [], created: $at, tick: $t, author: $author, session: $session})",
        id=fid, text=text, kind=kind, tags=tags, st=" ".join([*tags, names]), at=now_iso(), t=t,
        author=author, session=session)
    for c in citations:
        props = {"mode": c.mode, "hash": c.hash, "sig_hash": c.sig_hash, "quote": c.quote, "line": c.line,
                 "snapshot": c.snapshot, "tick": t, "at": now_iso(), "ok": True}
        if c.mode == "symbol":
            store.q("MATCH (f:Fact {id: $id}), (s:Symbol {key: $k}) CREATE (f)-[c:CITES]->(s) SET c += $p",
                    id=fid, k=c.target, p=props)
        elif c.mode in ("quote", "file"):
            store.q("MATCH (f:Fact {id: $id}), (x:File {path: $k}) CREATE (f)-[c:CITES]->(x) SET c += $p",
                    id=fid, k=c.target, p=props)
        else:
            store.q("MERGE (s:Source {key: $k}) ON CREATE SET s.kind = $kind, s.label = $label "
                    "WITH s MATCH (f:Fact {id: $id}) CREATE (f)-[c:CITES]->(s) SET c += $p",
                    k=c.target, kind=c.extra.get("kind", "note"), label=c.label, id=fid, p=props)
    for d in derived_from:
        store.q("MATCH (f:Fact {id: $a}), (g:Fact {id: $b}) CREATE (f)-[:DERIVED_FROM {tick: $t}]->(g)",
                a=fid, b=d, t=t)
    return fid, citations


def remember(store: Store, root: Path, text: str, cites: list[Any], *, kind: str = "fact",
             derived_from: list[str] | None = None, tags: list[str] | None = None,
             author: str = "agent", session: str | None = None) -> dict:
    _sync_for(store, root, cites)
    fid, _ = _create_fact(store, root, text, cites, kind, derived_from or [], tags or [], author, session)
    recompute_status(store)
    out = get_fact(store, fid)
    out["related"] = _related_existing(store, fid)
    return out


def _related_existing(store: Store, fid: str) -> list[dict]:
    """Live memories citing the same evidence: candidates for duplicates/conflicts."""
    return store.q(
        "MATCH (f:Fact {id: $id})-[:CITES]->(e)<-[:CITES]-(g:Fact) "
        "WHERE g.id <> f.id AND NOT g.status IN $inactive "
        "RETURN DISTINCT g.id AS id, g.text AS text, g.status AS status ORDER BY id",
        id=fid, inactive=list(INACTIVE))


def _verification(store: Store, fid: str, action: str, note: str, by: str) -> None:
    store.q("MATCH (f:Fact {id: $id}) CREATE (f)-[:VERIFIED]->(:Verification {action: $a, note: $n, by: $by, "
            "at: $at})", id=fid, a=action, n=note, by=by, at=now_iso())


def reaffirm(store: Store, root: Path, fid: str, *, note: str = "", by: str = "agent") -> dict:
    """The agent re-checked the memory against today's code and it still holds."""
    f = _require(store, fid)
    if f["status"] in INACTIVE:
        raise MemoryError_(f"{fid} is {f['status']}; remember a new fact instead")
    cites = store.q(
        "MATCH (:Fact {id: $id})-[c:CITES]->(e) RETURN c.mode AS mode, c.quote AS quote, "
        "coalesce(e.key, e.path) AS target, e.deleted AS deleted, labels(e) AS labels", id=fid)
    _sync_for(store, root, [({"path": c["target"]} if c["mode"] in ("quote", "file") else
                             {"symbol": c["target"]}) for c in cites if c["mode"] != "external"])
    t = tick(store)
    for c in cites:
        if c["mode"] == "symbol":
            row = store.one("MATCH (s:Symbol {key: $k}) RETURN s.deleted AS deleted, s.body_hash AS h, "
                            "s.sig_hash AS sh, s.source AS src", k=c["target"])
            if row is None or row["deleted"]:
                raise MemoryError_(f"{fid} cites {c['target']}, which no longer exists. Use revise with a new "
                                   "receipt, or retract.")
            store.q("MATCH (:Fact {id: $id})-[c:CITES]->(:Symbol {key: $k}) SET c.hash = $h, c.sig_hash = $sh, "
                    "c.snapshot = $src, c.tick = $t, c.ok = true", id=fid, k=c["target"], h=row["h"],
                    sh=row["sh"], src=(row["src"] or "")[:SNAPSHOT_MAX], t=t)
        elif c["mode"] in ("quote", "file"):
            text = read_text(root, c["target"])
            if text is None:
                raise MemoryError_(f"{fid} cites {c['target']}, which no longer exists. Use revise or retract.")
            if c["mode"] == "quote":
                line = find_quote(text, c["quote"])
                if line is None:
                    raise MemoryError_(f"{fid} quotes text that is gone from {c['target']}. Use revise with a "
                                       "new quote, or retract.")
                store.q("MATCH (:Fact {id: $id})-[c:CITES {mode: 'quote'}]->(:File {path: $k}) WHERE c.quote = $q "
                        "SET c.ok = true, c.line = $line, c.tick = $t", id=fid, k=c["target"], q=c["quote"],
                        line=line, t=t)
            else:
                store.q("MATCH (:Fact {id: $id})-[c:CITES {mode: 'file'}]->(:File {path: $k}) "
                        "SET c.hash = $h, c.snapshot = $snap, c.tick = $t", id=fid, k=c["target"], h=sha(text),
                        snap=text[:SNAPSHOT_MAX], t=t)
    store.checkpoint()
    # Re-point derivations at the newest version of each superseded parent.
    for r in store.q("MATCH (:Fact {id: $id})-[:DERIVED_FROM]->(p:Fact {status: 'superseded'}) RETURN p.id AS id",
                     id=fid):
        newest = current_version(store, r["id"])
        if newest != r["id"]:
            store.q("MATCH (f:Fact {id: $id})-[r:DERIVED_FROM]->(old:Fact {id: $old}) MATCH (n:Fact {id: $new}) "
                    "MERGE (f)-[:DERIVED_FROM {tick: $t}]->(n) DELETE r", id=fid, old=r["id"], new=newest, t=t)
    store.q("MATCH (f:Fact {id: $id})-[r:INVALIDATED_BY]->() DELETE r", id=fid)
    _verification(store, fid, "reaffirmed", note, by)
    recompute_status(store)
    out = get_fact(store, fid)
    if out["status"] != "fresh":
        out["warning"] = "still " + out["status"] + ": " + "; ".join(out["reasons"])
    return out


def current_version(store: Store, fid: str) -> str:
    """Follow SUPERSEDES edges from ``fid`` to the newest version of that memory."""
    row = store.one(
        "MATCH p = (f:Fact {id: $id})<-[:SUPERSEDES*0..20]-(n:Fact) "
        "WHERE NOT EXISTS { (n)<-[:SUPERSEDES]-(:Fact) } RETURN n.id AS id ORDER BY length(p) DESC LIMIT 1",
        id=fid)
    return row["id"] if row else fid


def revise(store: Store, root: Path, fid: str, text: str, *, cites: list[Any] | None = None, note: str = "",
           derived_from: list[str] | None = None, by: str = "agent") -> dict:
    """Replace a memory with a corrected one; the old one is kept as history."""
    old = _require(store, fid)
    if old["status"] in INACTIVE:
        raise MemoryError_(f"{fid} is already {old['status']}")
    if not cites:
        # Re-anchor every receipt that still exists to today's code; drop the
        # ones that are gone (deleted symbols, quotes no longer in the file).
        cites = []
        for c in old["citations"]:
            if c["deleted"] or (c["mode"] == "quote" and c["broken"]):
                continue
            cites.append({"symbol": c["target"]} if c["mode"] == "symbol" else
                         {"path": c["target"], "quote": c["quote"]} if c["mode"] == "quote" else
                         {"path": c["target"]} if c["mode"] == "file" else
                         {"note": c["label"]})
        if not cites:
            raise MemoryError_(f"none of {fid}'s receipts exist any more; pass new cites")
    _sync_for(store, root, cites)
    if derived_from is None:
        derived_from = [r["id"] for r in store.q(
            "MATCH (:Fact {id: $id})-[:DERIVED_FROM]->(p:Fact) RETURN p.id AS id", id=fid)]
    parents = sorted({current_version(store, p) for p in derived_from})
    new_id, _ = _create_fact(store, root, text, cites, old["kind"], parents, old["tags"], by, None)
    store.q("MATCH (n:Fact {id: $n}), (o:Fact {id: $o}) CREATE (n)-[:SUPERSEDES {at: $at}]->(o) "
            "SET o.status = 'superseded', o.superseded_by = $n", n=new_id, o=fid, at=now_iso())
    _verification(store, fid, "superseded", note or f"replaced by {new_id}", by)
    recompute_status(store)
    out = get_fact(store, new_id)
    out["supersedes"] = fid
    return out


def retract(store: Store, fid: str, *, reason: str = "", by: str = "agent") -> dict:
    f = _require(store, fid)
    if f["status"] == "retracted":
        return f
    store.q("MATCH (f:Fact {id: $id}) SET f.status = 'retracted', f.retracted_reason = $r", id=fid, r=reason)
    _verification(store, fid, "retracted", reason, by)
    recompute_status(store)
    return get_fact(store, fid)


# --------------------------------------------------------------------------- #
# Reading memories
# --------------------------------------------------------------------------- #


def _require(store: Store, fid: str) -> dict:
    out = get_fact(store, fid)
    if out is None:
        raise MemoryError_(f"no memory {fid}")
    return out


def get_fact(store: Store, fid: str) -> dict | None:
    f = store.one("MATCH (f:Fact {id: $id}) RETURN f.id AS id, f.text AS text, f.kind AS kind, f.status AS status, "
                  "f.reasons AS reasons, f.tags AS tags, f.created AS created, f.author AS author, "
                  "f.superseded_by AS superseded_by, f.retracted_reason AS retracted_reason", id=fid)
    if f is None:
        return None
    f["reasons"] = f.get("reasons") or []
    f["tags"] = f.get("tags") or []
    f["citations"] = []
    for c in store.q(
        "MATCH (:Fact {id: $id})-[c:CITES]->(e) "
        "RETURN c.mode AS mode, c.hash AS hash, c.quote AS quote, c.line AS line, c.ok AS ok, c.followed AS followed, "
        "coalesce(e.key, e.path, e.key) AS target, e.label AS label, e.deleted AS deleted, "
        "e.body_hash AS body_hash, e.hash AS file_hash, e.start AS start, e.path AS path", id=fid):
        broken = False
        if c["mode"] == "symbol":
            broken = bool(c["deleted"]) or c["body_hash"] != c["hash"]
        elif c["mode"] == "quote":
            broken = bool(c["deleted"]) or c["ok"] is False
        elif c["mode"] == "file":
            broken = bool(c["deleted"]) or c["file_hash"] != c["hash"]
        label = c["label"] or c["target"]
        if c["mode"] == "symbol":
            label = f"{c['target']} (line {c['start']})" if c["start"] else c["target"]
        elif c["mode"] == "quote":
            label = f"{c['target']}:{c['line']} \"{_clip(c['quote'], 60)}\""
        f["citations"].append({"mode": c["mode"], "target": c["target"], "label": label, "broken": broken,
                               "deleted": bool(c["deleted"]), "followed": c["followed"] or [],
                               "quote": c["quote"] or ""})
    f["derived_from"] = [r["id"] for r in store.q(
        "MATCH (:Fact {id: $id})-[:DERIVED_FROM]->(p:Fact) RETURN p.id AS id ORDER BY id", id=fid)]
    f["supports"] = [r["id"] for r in store.q(
        "MATCH (:Fact {id: $id})<-[:DERIVED_FROM]-(c:Fact) RETURN c.id AS id ORDER BY id", id=fid)]
    return f


def _clip(s: str | None, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


FTS_WORD = re.compile(r"[A-Za-z0-9_]{2,}")
STOP = {"the", "a", "an", "is", "are", "how", "what", "why", "does", "do", "of", "in", "to", "and", "or",
        "for", "on", "with", "we", "our", "it", "this", "that", "be", "should", "can", "when", "where"}


def _fts_query(text: str) -> str:
    words = [w for w in FTS_WORD.findall(text or "") if w.lower() not in STOP]
    return " ".join(words[:12])


def resolve_target(store: Store, root: Path, target: str) -> tuple[list[str], list[str]]:
    """A target is a symbol (key/qualname/name) or a path. Returns (symbol keys, file paths)."""
    target = (target or "").strip()
    if not target:
        return [], []
    rel = _rel(root, target)
    if store.one("MATCH (f:File {path: $p}) RETURN f.path AS p", p=rel):
        keys = [r["key"] for r in store.q("MATCH (:File {path: $p})-[:DEFINES]->(s:Symbol) WHERE s.deleted = false "
                                          "RETURN s.key AS key", p=rel)]
        return keys, [rel]
    syms = resolve_symbol(store, target)
    return [s["key"] for s in syms], []


def recall(store: Store, root: Path, query: str = "", *, target: str = "", limit: int = 8,
           include_inactive: bool = False) -> list[dict]:
    """Memories relevant to a question and/or a piece of code, ranked, with receipts re-checked."""
    scores: dict[str, float] = {}
    why: dict[str, str] = {}

    def bump(fid: str, score: float, reason: str) -> None:
        if score > scores.get(fid, 0.0):
            scores[fid] = score
            why[fid] = reason

    q = _fts_query(query)
    if q:
        for r in store.q("MATCH (f:Fact) SEARCH f IN (FULLTEXT INDEX fact_text FOR $q LIMIT 25) SCORE AS sc "
                         "RETURN f.id AS id, sc", q=q):
            bump(r["id"], min(1.0, 0.35 + float(r["sc"])), "matches your question")

    keys, paths = resolve_target(store, root, target)
    if keys or paths:
        for r in store.q(
            "MATCH (f:Fact)-[:CITES]->(e) WHERE e.key IN $keys OR e.path IN $paths "
            "RETURN DISTINCT f.id AS id, coalesce(e.key, e.path) AS via", keys=keys, paths=paths):
            bump(r["id"], 1.2, f"cites {r['via']}")
        # Connected context: memories about the code around the target.
        for r in store.q(
            "MATCH (t:Symbol) WHERE t.key IN $keys "
            "MATCH (t)-[:CALLS]-(n:Symbol)<-[:CITES]-(f:Fact) "
            "RETURN DISTINCT f.id AS id, n.qualname AS via, "
            "CASE WHEN EXISTS { (t)-[:CALLS]->(n) } THEN 'calls' ELSE 'is called by' END AS rel", keys=keys):
            bump(r["id"], 0.75, f"{target} {r['rel']} {r['via']}, which this cites")
        for r in store.q(
            "MATCH (src:File)-[:IMPORTS]-(nb:File)-[:DEFINES]->(:Symbol)<-[:CITES]-(f:Fact) "
            "WHERE src.path IN $paths OR EXISTS { MATCH (src)-[:DEFINES]->(t:Symbol) WHERE t.key IN $keys } "
            "RETURN DISTINCT f.id AS id, nb.path AS via", keys=keys, paths=paths):
            bump(r["id"], 0.45, f"about {r['via']}, which imports/is imported by the target")

    # One hop along derivations from the strongest hits.
    seeds = sorted(scores, key=scores.get, reverse=True)[:limit]
    for r in store.q(
        "MATCH (f:Fact)-[:DERIVED_FROM]-(g:Fact) WHERE f.id IN $ids RETURN DISTINCT f.id AS seed, g.id AS id",
        ids=seeds):
        bump(r["id"], scores[r["seed"]] * 0.6, f"linked by derivation to {r['seed']}")

    if not scores:
        return []
    # Re-check receipts against the working tree before answering.
    cited_paths = [r["p"] for r in store.q(
        "MATCH (f:Fact)-[:CITES]->(e) WHERE f.id IN $ids RETURN DISTINCT coalesce(e.path, '') AS p",
        ids=list(scores)) if r["p"]]
    if cited_paths:
        sync(store, root, only=cited_paths, recompute=True)

    weight = {"fresh": 1.0, "suspect": 0.85, "stale": 0.8, "superseded": 0.3, "retracted": 0.2}
    out = []
    for fid in scores:
        f = get_fact(store, fid)
        if f is None or (f["status"] in INACTIVE and not include_inactive):
            continue
        f["score"] = round(scores[fid] * weight.get(f["status"], 0.5), 3)
        f["why"] = why[fid]
        out.append(f)
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:limit]


def impact(store: Store, root: Path, target: str) -> dict:
    """Which memories would a change to ``target`` put at risk?"""
    if (root / _rel(root, target)).is_file():
        sync(store, root, only=[_rel(root, target)], recompute=True)
    keys, paths = resolve_target(store, root, target)
    if not keys and not paths:
        raise MemoryError_(f"nothing called '{target}' in this repo")
    direct = store.q(
        "MATCH (f:Fact)-[:CITES]->(e) WHERE (e.key IN $keys OR e.path IN $paths) AND NOT f.status IN $inactive "
        "RETURN DISTINCT f.id AS id, f.text AS text, f.status AS status, coalesce(e.key, e.path) AS via ORDER BY id",
        keys=keys, paths=paths, inactive=list(INACTIVE))
    callers = store.q(
        "MATCH (f:Fact)-[:CITES]->(c:Symbol)-[:CALLS]->{1,2}(t:Symbol) "
        "WHERE t.key IN $keys AND NOT f.status IN $inactive AND NOT c.key IN $keys "
        "RETURN DISTINCT f.id AS id, f.text AS text, f.status AS status, c.qualname AS via ORDER BY id",
        keys=keys, inactive=list(INACTIVE))
    seed = [r["id"] for r in direct] + [r["id"] for r in callers]
    derived = store.q(
        "MATCH (d:Fact)-[:DERIVED_FROM]->+(f:Fact) WHERE f.id IN $ids AND NOT d.id IN $ids "
        "AND NOT d.status IN $inactive "
        "RETURN DISTINCT d.id AS id, d.text AS text, d.status AS status, f.id AS via ORDER BY id",
        ids=seed, inactive=list(INACTIVE))
    return {"target": target, "symbols": keys, "paths": paths, "direct": direct, "via_callers": callers,
            "derived": derived}


def verify(store: Store, root: Path, fid: str) -> dict:
    """A memory with each receipt diffed against the code as it is now."""
    _require(store, fid)
    paths = [r["p"] for r in store.q("MATCH (:Fact {id: $id})-[:CITES]->(e) RETURN DISTINCT e.path AS p", id=fid)
             if r["p"]]
    if paths:
        sync(store, root, only=paths, recompute=True)
    f = get_fact(store, fid)
    diffs = []
    for c in store.q(
        "MATCH (:Fact {id: $id})-[c:CITES]->(e) RETURN c.mode AS mode, c.snapshot AS snap, c.quote AS quote, "
        "coalesce(e.key, e.path) AS target, e.source AS source, e.path AS path, e.deleted AS deleted", id=fid):
        if c["mode"] == "symbol":
            now = "" if c["deleted"] else (c["source"] or "")
        elif c["mode"] in ("quote", "file"):
            text = read_text(root, c["target"]) or ""
            if c["mode"] == "file":
                now = text[:SNAPSHOT_MAX]
            else:
                line = find_quote(text, c["quote"])
                lines = text.splitlines()
                anchor = line or _nearest_line(text, c["quote"])
                now = "\n".join(lines[max(0, anchor - 3): anchor + 2]) if lines else ""
        else:
            continue
        before = c["snap"] or ""
        if before.strip() == now.strip():
            continue
        diff = "\n".join(difflib.unified_diff(before.splitlines(), now.splitlines(), "remembered", "now",
                                              lineterm="", n=2))
        diffs.append({"target": c["target"], "diff": diff or "(deleted)"})
    f["diffs"] = diffs
    f["history"] = store.q(
        "MATCH (:Fact {id: $id})-[:VERIFIED]->(v:Verification) RETURN v.action AS action, v.note AS note, "
        "v.at AS at, v.by AS by ORDER BY v.at", id=fid)
    f["broken_by"] = store.q(
        "MATCH (:Fact {id: $id})-[:INVALIDATED_BY]->(c:Change) RETURN c.id AS id, c.detail AS detail, "
        "c.commit AS commit, c.at AS at ORDER BY c.tick", id=fid)
    return f


def _nearest_line(text: str, quote: str) -> int:
    """Best-matching line for a quote that is gone (to show what replaced it)."""
    first = (quote or "").splitlines()[0] if quote else ""
    best, best_i = 0.0, 1
    for i, line in enumerate(text.splitlines(), 1):
        r = difflib.SequenceMatcher(None, first.strip(), line.strip()).ratio()
        if r > best:
            best, best_i = r, i
    return best_i


def status(store: Store) -> dict:
    counts = {r["status"]: r["n"] for r in store.q("MATCH (f:Fact) RETURN f.status AS status, count(*) AS n")}
    last = store.one("MATCH (s:Sync) RETURN s.id AS id, s.at AS at, s.commit AS commit ORDER BY s.tick DESC LIMIT 1")
    attention = store.q("MATCH (f:Fact) WHERE f.status IN ['stale', 'suspect'] "
                        "RETURN f.id AS id, f.text AS text, f.status AS status, f.reasons AS reasons ORDER BY f.id")
    graph = store.one("MATCH (s:Symbol) WHERE s.deleted = false WITH count(s) AS symbols "
                      "MATCH (f:File) WHERE f.deleted = false RETURN symbols, count(f) AS files")
    calls = store.one("MATCH ()-[r:CALLS]->() RETURN count(r) AS n")
    return {"counts": counts, "last_sync": last, "attention": attention,
            "graph": {**(graph or {}), "calls": (calls or {}).get("n", 0)}}
