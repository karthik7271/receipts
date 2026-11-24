"""Text rendering shared by the CLI (with colour), MCP tools and hooks (plain)."""

from __future__ import annotations

import os
import sys

BADGE = {
    "fresh": ("✓ fresh", "32"),
    "stale": ("✗ STALE", "31;1"),
    "suspect": ("? SUSPECT", "33;1"),
    "superseded": ("↷ superseded", "90"),
    "retracted": ("⌫ retracted", "90"),
}


def use_color(stream=None) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    stream = stream or sys.stdout
    return stream.isatty() and os.environ.get("TERM") != "dumb"


class Painter:
    def __init__(self, color: bool):
        self.color = color

    def __call__(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def dim(self, t: str) -> str:
        return self(t, "2")

    def bold(self, t: str) -> str:
        return self(t, "1")


def badge(status: str, p: Painter) -> str:
    label, code = BADGE.get(status, (status, "0"))
    return p(label, code)


def fact_line(f: dict, p: Painter) -> str:
    return f"{p.bold(f['id'])} {badge(f['status'], p)}  {f['text']}"


def fact_block(f: dict, p: Painter, *, indent: str = "  ", why: bool = True) -> str:
    lines = [fact_line(f, p)]
    if why and f.get("why"):
        lines.append(f"{indent}{p.dim('why recalled: ' + f['why'])}")
    for r in f.get("reasons") or []:
        lines.append(f"{indent}{p('⚠ ' + r, '33')}")
    for c in f.get("citations") or []:
        mark = p("✗", "31") if c["broken"] else p("✓", "32")
        extra = ""
        if c.get("followed"):
            extra = p.dim(f"  (followed rename from {', '.join(c['followed'])})")
        if c["mode"] == "external":
            mark = p("•", "36")
            extra = p.dim("  (not checkable against code)")
        lines.append(f"{indent}{mark} receipt: {c['label']}{extra}")
    if f.get("derived_from"):
        lines.append(f"{indent}{p.dim('builds on ' + ', '.join(f['derived_from']))}")
    if f.get("supports"):
        lines.append(f"{indent}{p.dim('supports ' + ', '.join(f['supports']))}")
    if f.get("superseded_by"):
        lines.append(f"{indent}{p.dim('superseded by ' + f['superseded_by'])}")
    return "\n".join(lines)


def facts_list(facts: list[dict], p: Painter, empty: str = "No memories found.") -> str:
    if not facts:
        return empty
    return "\n\n".join(fact_block(f, p) for f in facts)


def advice_for(facts: list[dict]) -> str:
    bad = [f for f in facts if f["status"] in ("stale", "suspect")]
    if not bad:
        return ""
    ids = ", ".join(f["id"] for f in bad)
    return (f"\n\n{ids} may be out of date. Do not rely on them as written: check the code "
            "(use `verify` to see what changed), then `reaffirm` (still true), `revise` (corrected text) "
            "or `retract`.")


def sync_summary(report, p: Painter, *, max_changes: int = 12) -> str:
    if not report.sync_id:
        return p.dim("Code graph already up to date.") + _transitions(report, p)
    order = {"moved": 0, "signature": 1, "deleted": 2, "types": 3, "body": 4}
    sym_changes = sorted((c for c in report.changes if c["kind"] != "file"),
                         key=lambda c: (order.get(c["kind"], 9), "test" in c.get("key", "")))
    lines = [f"{p.bold(report.sync_id)} scanned {report.files_scanned} files, "
             f"{len(report.files_changed)} changed, {len(sym_changes)} symbol changes"]
    icons = {"body": "~", "signature": "Δ", "deleted": "−", "moved": "→", "types": "·"}
    for c in sym_changes[:max_changes]:
        lines.append(f"  {p(icons.get(c['kind'], '·'), '36')} {c['detail']}")
    if len(sym_changes) > max_changes:
        lines.append(p.dim(f"  … {len(sym_changes) - max_changes} more"))
    return "\n".join(lines) + _transitions(report, p)


def _transitions(report, p: Painter) -> str:
    if not report.transitions:
        return ""
    out = ["", p.bold("Memory status changes:")]
    for t in sorted(report.transitions, key=lambda x: int(x["id"][1:])):
        out.append(f"  {p.bold(t['id'])} {badge(t['before'] or 'new', p)} → {badge(t['after'], p)}")
        for r in t["reasons"]:
            out.append(f"      {p.dim(r)}")
    return "\n".join(out)


def status_block(st: dict, p: Painter) -> str:
    c = st["counts"]
    total = sum(c.values())
    parts = [f"{c.get(k, 0)} {k}" for k in ("fresh", "stale", "suspect", "superseded", "retracted") if c.get(k)]
    g = st.get("graph") or {}
    lines = [f"{p.bold('Receipts')}: {total} memories ({', '.join(parts) or 'none yet'})",
             p.dim(f"code graph: {g.get('files', 0)} files, {g.get('symbols', 0)} symbols, {g.get('calls', 0)} call edges")]
    if st.get("last_sync"):
        ls = st["last_sync"]
        lines.append(p.dim(f"last sync {ls['id']} at {ls['at']}" + (f" (commit {ls['commit']})" if ls.get("commit") else "")))
    if st["attention"]:
        lines.append("")
        lines.append(p.bold("Needs attention:"))
        for f in st["attention"]:
            lines.append(f"  {p.bold(f['id'])} {badge(f['status'], p)}  {f['text']}")
            for r in f["reasons"] or []:
                lines.append(f"      {p.dim(r)}")
    return "\n".join(lines)


def impact_block(imp: dict, p: Painter) -> str:
    lines = [f"Changing {p.bold(imp['target'])} puts these memories at risk:"]
    sections = [("cite it directly", imp["direct"]), ("cite code that calls it", imp["via_callers"]),
                ("are derived from those", imp["derived"])]
    any_rows = False
    for title, rows in sections:
        if not rows:
            continue
        any_rows = True
        lines.append(f"  {p.dim(title)}:")
        for r in rows:
            lines.append(f"    {p.bold(r['id'])} {badge(r['status'], p)}  {r['text']}  {p.dim('via ' + r['via'])}")
    if not any_rows:
        lines.append(p.dim("  none: no memory depends on it."))
    else:
        lines.append(p.dim("  After the change, reaffirm or revise these."))
    return "\n".join(lines)


def verify_block(v: dict, p: Painter) -> str:
    lines = [fact_block(v, p, why=False)]
    for b in v.get("broken_by") or []:
        lines.append(p.dim(f"  broken by {b['id']}: {b['detail']}" + (f" (commit {b['commit']})" if b.get("commit") else "")))
    for d in v.get("diffs") or []:
        lines.append("")
        lines.append(p.bold(f"  what changed in {d['target']}:"))
        for line in d["diff"].splitlines():
            code = "32" if line.startswith("+") and not line.startswith("+++") else \
                   "31" if line.startswith("-") and not line.startswith("---") else "2"
            lines.append("    " + p(line, code))
    if not v.get("diffs") and v["status"] == "fresh":
        lines.append(p.dim("  every receipt matches the code"))
    for h in v.get("history") or []:
        lines.append(p.dim(f"  {h['at']}: {h['action']} by {h['by']}" + (f": {h['note']}" if h.get("note") else "")))
    return "\n".join(lines)
