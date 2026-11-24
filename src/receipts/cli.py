"""Command line interface: ``receipts <command>``."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sys
from pathlib import Path

from . import __version__, memory
from .graph import sync
from .render import (Painter, badge, fact_block, facts_list, impact_block, status_block, sync_summary, use_color,
                     verify_block)
from .store import Store, default_store_path, find_repo_root


def parse_cite_arg(s: str):
    if s.startswith("note:"):
        return {"note": s[5:].strip()}
    if s.startswith("url:"):
        return {"url": s[4:].strip()}
    if "#" in s:
        path, quote = s.split("#", 1)
        return {"path": path, "quote": quote}
    return s


PACKAGE_SOURCE = os.environ.get("RECEIPTS_SOURCE", "git+https://github.com/karthik7271/receipts")
EPHEMERAL_MARKERS = ("archive-v0", "/.cache/uv/", "\\uv\\cache\\", "/uv/cache/", "builds-v0")


def receipts_command() -> list[str]:
    """The command hooks and MCP hosts should run later.

    Under `uvx` the current interpreter lives in uv's cache and can be cleaned
    up at any time, so point hosts at `uvx --from <source> receipts` instead of
    at this temporary path.
    """
    exe = shutil.which("receipts")
    here = exe or sys.executable
    if any(m in here for m in EPHEMERAL_MARKERS) and shutil.which("uvx"):
        return ["uvx", "--from", PACKAGE_SOURCE, "receipts"]
    if exe:
        return [os.path.abspath(exe)]
    return [sys.executable, "-m", "receipts"]


# --------------------------------------------------------------------------- #


def cmd_demo(a, p):
    from .demo import run
    run(Path(a.dir).resolve(), fast=a.fast)


def cmd_sync(a, p):
    with Store(a.store) as s:
        rep = sync(s, a.repo, only=a.paths or None)
    print(sync_summary(rep, p))


def cmd_init(a, p):
    with Store(a.store) as s:
        rep = sync(s, a.repo)
        st = memory.status(s)
    print(sync_summary(rep, p))
    print(status_block(st, p))
    print(p.dim(f"store: {a.store}"))


def cmd_remember(a, p):
    with Store(a.store) as s:
        f = memory.remember(s, a.repo, a.text, [parse_cite_arg(c) for c in a.cite], kind=a.kind,
                            derived_from=a.derived_from or [], tags=a.tag or [], author="user")
    print(fact_block(f, p, why=False))
    for w in f.get("warnings") or []:
        print(p("  tip: ", "33") + w)
    for r in f.get("related") or []:
        print(p.dim(f"  also cites the same code: {r['id']} [{r['status']}] {r['text']}"))


def cmd_recall(a, p):
    with Store(a.store) as s:
        facts = memory.recall(s, a.repo, " ".join(a.query), target=a.target or "", limit=a.limit,
                              include_inactive=a.all)
    if a.json:
        print(json.dumps(facts, indent=2))
    else:
        print(facts_list(facts, p))


def cmd_verify(a, p):
    with Store(a.store) as s:
        print(verify_block(memory.verify(s, a.repo, a.id), p))


def cmd_reaffirm(a, p):
    with Store(a.store) as s:
        f = memory.reaffirm(s, a.repo, a.id, note=a.note or "", by="user")
    print(fact_block(f, p, why=False))


def cmd_revise(a, p):
    with Store(a.store) as s:
        f = memory.revise(s, a.repo, a.id, a.text, cites=[parse_cite_arg(c) for c in a.cite] or None,
                          note=a.note or "", by="user")
    print(f"{a.id} → {f['id']}")
    print(fact_block(f, p, why=False))


def cmd_retract(a, p):
    with Store(a.store) as s:
        f = memory.retract(s, a.id, reason=a.reason or "", by="user")
    print(fact_block(f, p, why=False))


def cmd_impact(a, p):
    with Store(a.store) as s:
        print(impact_block(memory.impact(s, a.repo, a.target), p))


def cmd_status(a, p):
    with Store(a.store) as s:
        if not a.no_sync:
            sync(s, a.repo)
        st = memory.status(s)
    if a.json:
        print(json.dumps(st, indent=2, default=str))
    else:
        print(status_block(st, p))


def cmd_list(a, p):
    with Store(a.store, read_only=True) as s:
        ids = [r["id"] for r in s.q("MATCH (f:Fact) WHERE $st IS NULL OR f.status = $st RETURN f.id AS id",
                                    st=a.status)]
        facts = sorted((memory.get_fact(s, i) for i in ids), key=lambda f: int(f["id"][1:]))
    print(facts_list(facts, p, empty="No memories yet."))


def cmd_why(a, p):
    """The graph behind one memory: receipts, what it builds on, what it replaced."""
    with Store(a.store, read_only=True) as s:
        seen: set[str] = set()

        def tree(fid: str, prefix: str, depth: int) -> list[str]:
            f = memory.get_fact(s, fid)
            if f is None:
                return []
            lines = []
            kids: list[tuple[str, object]] = []
            for c in f["citations"]:
                mark = p("✗", "31") if c["broken"] else (p("•", "36") if c["mode"] == "external" else p("✓", "32"))
                fol = p.dim(f" (followed rename from {', '.join(c['followed'])})") if c.get("followed") else ""
                kids.append(("text", f"{mark} receipt {c['label']}{fol}"))
            for r in f["reasons"]:
                kids.append(("text", p("⚠ " + r, "33")))
            for d in f["derived_from"]:
                kids.append(("fact", d))
            old = s.q("MATCH (:Fact {id: $id})-[:SUPERSEDES]->(o:Fact) RETURN o.id AS id", id=fid)
            for o in old:
                kids.append(("old", o["id"]))
            for i, (kind, val) in enumerate(kids):
                last = i == len(kids) - 1
                branch, cont = ("└─ ", "   ") if last else ("├─ ", "│  ")
                if kind == "text":
                    lines.append(prefix + branch + str(val))
                    continue
                g = memory.get_fact(s, str(val))
                tag = "builds on" if kind == "fact" else "replaced"
                lines.append(prefix + branch + p.dim(tag + " ") + f"{p.bold(g['id'])} {badge(g['status'], p)}  "
                             f"{g['text']}")
                if depth < 4 and val not in seen:
                    seen.add(str(val))
                    lines += tree(str(val), prefix + cont, depth + 1)
            return lines

        root = memory.get_fact(s, a.id)
        if root is None:
            sys.exit(f"no memory {a.id}")
        print(f"{p.bold(root['id'])} {badge(root['status'], p)}  {root['text']}")
        print("\n".join(tree(a.id, "", 0)))
        hist = s.q("MATCH (:Fact {id: $id})-[:VERIFIED]->(v:Verification) RETURN v.action AS action, v.note AS note, "
                   "v.at AS at ORDER BY v.at", id=a.id)
        for h in hist:
            print(p.dim(f"   {h['at']}: {h['action']}" + (f": {h['note']}" if h["note"] else "")))


def cmd_query(a, p):
    with Store(a.store, read_only=True) as s:
        rows = s.q(a.cypher)
    if a.json or not rows:
        print(json.dumps(rows, indent=2, default=str))
        return
    cols = list(rows[0].keys())
    widths = {c: min(60, max(len(c), *(len(str(r.get(c))) for r in rows))) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    print("  ".join("─" * widths[c] for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c))[:60].ljust(widths[c]) for c in cols))


def cmd_export(a, p):
    from .export import claude_md, to_cypher, to_json
    with Store(a.store, read_only=True) as s:
        if a.format == "cypher":
            out = to_cypher(s)
        elif a.format == "json":
            out = json.dumps(to_json(s), indent=1, default=str)
        else:
            out = claude_md(s)
    if a.out:
        Path(a.out).write_text(out)
        print(p.dim(f"wrote {a.out}"), file=sys.stderr)
    else:
        sys.stdout.write(out)


def _merge_json(path: Path, update) -> None:
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text() or "{}")
        except json.JSONDecodeError:
            sys.exit(f"{path} is not valid JSON; fix it and re-run")
    update(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def cmd_install(a, p):
    """Wire Receipts into Claude Code for this repo: MCP server + hooks."""
    repo: Path = a.repo
    base = receipts_command()
    mcp_entry = {"command": base[0], "args": [*base[1:], "mcp"]}

    def add_mcp(d):
        d.setdefault("mcpServers", {})["receipts"] = mcp_entry
    _merge_json(repo / ".mcp.json", add_mcp)

    def hook(kind):
        return {"type": "command", "command": shlex.join([*base, "hook", kind]), "timeout": 30}

    def add_hooks(d):
        hooks = d.setdefault("hooks", {})
        for event, matcher, kind in (("SessionStart", None, "session-start"),
                                     ("PostToolUse", "Edit|Write|MultiEdit|NotebookEdit|Bash|Read", "post-tool-use")):
            groups = [g for g in hooks.get(event, [])
                      if not any("receipts" in h.get("command", "") and " hook " in h.get("command", "")
                                 for h in g.get("hooks", []))]
            group = {"hooks": [hook(kind)]}
            if matcher:
                group["matcher"] = matcher
            hooks[event] = [*groups, group]
    _merge_json(repo / ".claude" / "settings.json", add_hooks)

    gi = repo / ".gitignore"
    lines = gi.read_text().splitlines() if gi.exists() else []
    for entry in (".receipts/*.lock", ".receipts/hook.log"):
        if entry not in lines:
            lines.append(entry)
    gi.write_text("\n".join(lines) + "\n")

    with Store(a.store) as s:
        rep = sync(s, repo)
    print(p.bold("Receipts is wired into Claude Code for ") + str(repo))
    print(f"  {p('✓', '32')} MCP server  → .mcp.json  (tools: recall, remember, verify, reaffirm, revise, retract, "
          "impact, memory_status)")
    print(f"  {p('✓', '32')} hooks       → .claude/settings.json  (SessionStart, PostToolUse)")
    print(f"  {p('✓', '32')} code graph  → {a.store}  ({rep.files_scanned} files scanned)")
    print(p.dim("  Commit .receipts/memory.rl to share memory with your team, or add it to .gitignore to keep it local."))
    print(p.dim("  Start `claude` in this repo and approve the 'receipts' MCP server when asked."))


def cmd_hook(a, p):
    from .hooks import run
    sys.exit(run(a.kind))


def cmd_mcp(a, p):
    from .mcp_server import main
    main(a.repo, a.store)


def cmd_ui(a, p):
    from .viewer_server import serve
    serve(a.store, port=a.port, open_browser=not a.no_open)


def cmd_heal(a, p):
    from .heal import heal
    heal(a.repo, a.store, model=a.model, apply=not a.dry_run, painter=p, only=a.ids or None, effort=a.effort)


# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="receipts", description="Agent memory with receipts, built on Relate.")
    ap.add_argument("--version", action="version", version=f"receipts {__version__}")
    ap.add_argument("--repo", help="repository root (default: git top-level of cwd)")
    ap.add_argument("--store", help="Relate store path (default: <repo>/.receipts/memory.rl)")
    ap.add_argument("--no-color", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("demo", help="run the 60-second story on a sample repo (no API key needed)")
    s.add_argument("--dir", default="receipts-demo", help="where to create the demo repo")
    s.add_argument("--fast", action="store_true", help="no pauses")
    s.set_defaults(fn=cmd_demo, needs_repo=False)

    s = sub.add_parser("init", help="scan the repo into the code graph")
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("sync", help="re-scan changed files and recompute memory status")
    s.add_argument("paths", nargs="*")
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("remember", help="save a memory with receipts")
    s.add_argument("text")
    s.add_argument("--cite", "-c", action="append", required=True,
                   help="symbol ('path.py::Name', 'Class.method'), path, 'path#exact quote', 'note:...' or 'url:...'")
    s.add_argument("--kind", "-k", default="fact", choices=memory.KINDS)
    s.add_argument("--from", dest="derived_from", action="append", help="memory id this builds on")
    s.add_argument("--tag", action="append")
    s.set_defaults(fn=cmd_remember)

    s = sub.add_parser("recall", help="find memories by question and/or code target")
    s.add_argument("query", nargs="*")
    s.add_argument("--target", "-t")
    s.add_argument("--limit", "-n", type=int, default=8)
    s.add_argument("--all", action="store_true", help="include superseded and retracted")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_recall)

    for name, fn, help_ in (("verify", cmd_verify, "show a memory's receipts diffed against today's code"),):
        s = sub.add_parser(name, help=help_)
        s.add_argument("id")
        s.set_defaults(fn=fn)
    s = sub.add_parser("reaffirm", help="memory still holds: re-anchor receipts to today's code")
    s.add_argument("id")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_reaffirm)
    s = sub.add_parser("revise", help="replace a memory with corrected text")
    s.add_argument("id")
    s.add_argument("text")
    s.add_argument("--cite", "-c", action="append", default=[])
    s.add_argument("--note")
    s.set_defaults(fn=cmd_revise)
    s = sub.add_parser("retract", help="mark a memory as wrong")
    s.add_argument("id")
    s.add_argument("--reason")
    s.set_defaults(fn=cmd_retract)
    s = sub.add_parser("impact", help="which memories depend on this code?")
    s.add_argument("target")
    s.set_defaults(fn=cmd_impact)
    s = sub.add_parser("status", help="summary and memories needing attention")
    s.add_argument("--no-sync", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("list", help="list memories")
    s.add_argument("--status", choices=["fresh", "stale", "suspect", "superseded", "retracted"])
    s.set_defaults(fn=cmd_list)
    s = sub.add_parser("why", help="the graph behind one memory")
    s.add_argument("id")
    s.set_defaults(fn=cmd_why)
    s = sub.add_parser("query", help="run a read-only Cypher query against the memory graph")
    s.add_argument("cypher")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_query)
    s = sub.add_parser("export", help="export as Cypher, JSON, or a CLAUDE.md section of verified memories")
    s.add_argument("--format", choices=["claude-md", "cypher", "json"], default="claude-md")
    s.add_argument("--out", "-o")
    s.set_defaults(fn=cmd_export)
    s = sub.add_parser("install", help="wire Receipts into Claude Code for this repo (MCP + hooks)")
    s.set_defaults(fn=cmd_install)
    s = sub.add_parser("hook", help="(called by Claude Code) run a hook")
    s.add_argument("kind", choices=["session-start", "post-tool-use"])
    s.set_defaults(fn=cmd_hook)
    s = sub.add_parser("mcp", help="(called by MCP hosts) serve the memory tools over stdio")
    s.set_defaults(fn=cmd_mcp)
    s = sub.add_parser("ui", help="explore the memory graph in your browser (Relate WASM)")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-open", action="store_true")
    s.set_defaults(fn=cmd_ui)
    s = sub.add_parser("heal", help="let Claude re-check stale/suspect memories against the code (needs ANTHROPIC_API_KEY)")
    s.add_argument("ids", nargs="*")
    s.add_argument("--model", default=os.environ.get("RECEIPTS_MODEL", "claude-opus-5-5"))
    s.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    s.add_argument("--dry-run", action="store_true", help="show verdicts without applying them")
    s.set_defaults(fn=cmd_heal)
    return ap


def main(argv: list[str] | None = None) -> None:
    ap = build_parser()
    a = ap.parse_args(argv)
    p = Painter(use_color() and not a.no_color)
    if getattr(a, "needs_repo", True):
        a.repo = Path(a.repo).resolve() if a.repo else find_repo_root()
        a.store = Path(a.store).resolve() if a.store else default_store_path(a.repo)
    try:
        a.fn(a, p)
    except memory.MemoryError_ as e:
        print(p("error: ", "31;1") + str(e), file=sys.stderr)
        sys.exit(2)
    except BrokenPipeError:
        pass


if __name__ == "__main__":
    main()
