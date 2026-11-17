"""`receipts heal`: let Claude repair stale and suspect memories.

For each memory that needs attention (parents before the memories derived
from them), Claude sees the memory, why the graph flagged it, every receipt
as remembered and as it is now, and the memories it builds on, and returns
a structured verdict: reaffirm, revise (with corrected text and receipts),
retract, or unsure. Verdicts are applied with the same functions the MCP
tools use, so the memory graph keeps the full history.

Meant for CI after merges as much as for a terminal: memory that maintains
itself instead of rotting. Needs `pip install receipts-memory[heal]` and an
Anthropic credential (ANTHROPIC_API_KEY or an `ant auth login` profile).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import memory
from .codegraph import read_text
from .graph import find_quote, sync
from .render import Painter, badge
from .store import Store

DEFAULT_MODEL = "claude-opus-5-5"

SYSTEM = """\
You maintain the long-term memory of a coding agent. Each memory is a short claim about a codebase, backed by \
receipts: the exact code it cites. The code has changed since the memory was written, and a graph analysis \
flagged this memory as stale (its own receipt changed) or suspect (code it depends on, or a memory it builds on, \
changed). Decide what to do with it, using only the code shown:

- reaffirm: every specific claim in the text is still true of the current code, as written.
- revise: the memory is still useful but some specifics are now wrong or incomplete. Write the corrected memory: \
one self-contained claim, as concise and specific as the original, in the same voice. Only state what the \
current code shows. Give receipts for the new text when the old ones no longer prove it (symbols as \
path::Name, or an exact quote copied verbatim from a file shown).
- retract: the memory no longer describes anything in the code and has no useful corrected form.
- unsure: the code shown is not enough to decide. Prefer this to guessing.

A memory that is merely suspect is often still true: check it against the current code and reaffirm when it holds."""

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["reaffirm", "revise", "retract", "unsure"]},
        "reason": {"type": "string", "description": "One or two sentences citing what in the code decided it"},
        "revised_text": {"type": "string", "description": "The corrected memory when verdict is revise, else empty"},
        "new_receipts": {
            "type": "array",
            "description": "Receipts for revised_text when the old ones no longer prove it; else empty",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["symbol", "quote"]},
                    "target": {"type": "string", "description": "path::Name for a symbol, or a file path for a quote"},
                    "quote": {"type": "string", "description": "Exact text from the file (quote receipts only)"},
                },
                "required": ["kind", "target", "quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["verdict", "reason", "revised_text", "new_receipts"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------- #
# Building the case for one memory
# --------------------------------------------------------------------------- #


def _receipt_context(store: Store, root: Path, fid: str) -> list[dict]:
    out = []
    for c in store.q(
        "MATCH (:Fact {id: $id})-[c:CITES]->(e) RETURN c.mode AS mode, c.snapshot AS snap, c.quote AS quote, "
        "coalesce(e.key, e.path, e.label) AS target, e.source AS source, e.deleted AS deleted, c.followed AS followed",
        id=fid):
        item = {"receipt": c["target"], "kind": c["mode"]}
        if c["followed"]:
            item["followed_rename_from"] = c["followed"]
        if c["mode"] == "symbol":
            item["remembered_code"] = c["snap"] or ""
            item["current_code"] = "(deleted)" if c["deleted"] else (c["source"] or "")
        elif c["mode"] in ("quote", "file"):
            text = read_text(root, c["target"])
            if c["mode"] == "quote":
                item["quote"] = c["quote"]
                item["remembered_context"] = c["snap"] or ""
                if text is None:
                    item["current_file"] = "(deleted)"
                else:
                    line = find_quote(text, c["quote"])
                    item["quote_still_present"] = line is not None
                    item["current_file"] = text[:6000]
            else:
                item["remembered_file"] = (c["snap"] or "")[:4000]
                item["current_file"] = "(deleted)" if text is None else text[:6000]
        else:
            item["note"] = "vouched for by a person; cannot be checked against code"
        out.append(item)
    return out


def build_case(store: Store, root: Path, fid: str) -> dict[str, Any]:
    f = memory.get_fact(store, fid)
    parents = store.q("MATCH (:Fact {id: $id})-[:DERIVED_FROM]->(p:Fact) "
                      "RETURN p.id AS id, p.text AS text, p.status AS status, p.superseded_by AS replaced_by",
                      id=fid)
    for p in parents:
        if p["replaced_by"]:
            p["replaced_by_text"] = memory.get_fact(store, memory.current_version(store, p["id"]))["text"]
    return {
        "memory_id": fid, "kind": f["kind"], "text": f["text"], "status": f["status"],
        "flagged_because": f["reasons"], "receipts": _receipt_context(store, root, fid),
        "builds_on": parents,
    }


def ask_claude(client, model: str, case: dict, effort: str) -> dict:
    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": "Memory to check:\n\n" + json.dumps(case, indent=1)}],
    )
    if response.stop_reason == "refusal":
        return {"verdict": "unsure", "reason": "the model declined this request", "revised_text": "",
                "new_receipts": []}
    if response.stop_reason == "max_tokens":
        return {"verdict": "unsure", "reason": "response was cut off", "revised_text": "", "new_receipts": []}
    text = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(text)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def _ordered_attention(store: Store, only: list[str] | None) -> list[str]:
    """Stale/suspect memories, parents before the memories derived from them."""
    ids = [r["id"] for r in store.q("MATCH (f:Fact) WHERE f.status IN ['stale', 'suspect'] RETURN f.id AS id")]
    if only:
        ids = [i for i in ids if i in set(only)]
    depth = {r["id"]: r["d"] for r in store.q(
        "MATCH (f:Fact) WHERE f.id IN $ids "
        "OPTIONAL MATCH p = (f)-[:DERIVED_FROM]->+(:Fact) "
        "RETURN f.id AS id, coalesce(max(length(p)), 0) AS d", ids=ids)}
    return sorted(ids, key=lambda i: (depth.get(i, 0), int(i[1:])))


def _to_cites(receipts: list[dict]) -> list:
    out = []
    for r in receipts:
        if r.get("kind") == "quote" and r.get("quote"):
            out.append({"path": r["target"], "quote": r["quote"]})
        elif r.get("target"):
            out.append({"symbol": r["target"]})
    return out


def apply_verdict(store: Store, root: Path, fid: str, v: dict) -> tuple[str, dict | None]:
    note = f"heal: {v.get('reason', '')}".strip()
    try:
        if v["verdict"] == "reaffirm":
            return "reaffirmed", memory.reaffirm(store, root, fid, note=note, by="heal")
        if v["verdict"] == "revise" and v.get("revised_text"):
            cites = _to_cites(v.get("new_receipts") or []) or None
            return "revised", memory.revise(store, root, fid, v["revised_text"], cites=cites, note=note, by="heal")
        if v["verdict"] == "retract":
            return "retracted", memory.retract(store, fid, reason=note, by="heal")
    except memory.MemoryError_ as e:
        return f"left as is ({e})", None
    return "left for a human", None


def heal(repo: Path, store_path: Path, *, model: str = DEFAULT_MODEL, apply: bool = True,
         painter: Painter | None = None, only: list[str] | None = None, effort: str = "high",
         client=None) -> list[dict]:
    p = painter or Painter(False)
    if client is None:
        try:
            import anthropic
        except ImportError:
            raise SystemExit("`receipts heal` needs the Anthropic SDK: pip install 'receipts-memory[heal]'")
        client = anthropic.Anthropic()

    with Store(store_path) as s:
        sync(s, repo)
        queue = _ordered_attention(s, only)
    if not queue:
        print("Nothing to heal: every memory matches the code.")
        return []

    print(f"Checking {len(queue)} memories with {model}" + ("" if apply else " (dry run)") + "\n")
    results = []
    for fid in queue:
        with Store(store_path) as s:
            f = memory.get_fact(s, fid)
            if f is None or f["status"] not in ("stale", "suspect"):
                continue  # an earlier repair already settled it
            case = build_case(s, repo, fid)
            verdict = ask_claude(client, model, case, effort)
            outcome, new = ("dry run", None)
            if apply:
                outcome, new = apply_verdict(s, repo, fid, verdict)
        print(f"{p.bold(fid)} {badge(f['status'], p)}  {f['text']}")
        print(f"   {p('→ ' + verdict['verdict'], '35;1')}: {verdict['reason']}")
        if verdict["verdict"] == "revise" and verdict.get("revised_text"):
            print(f"   {p.dim('new text:')} {verdict['revised_text']}")
        print(p.dim(f"   {outcome}" + (f" → {new['id']} ({new['status']})" if new and new.get('id') != fid else "")))
        print()
        results.append({"id": fid, **verdict, "outcome": outcome})
    return results
