"""Receipts as an MCP server (stdio, JSON-RPC 2.0, no SDK dependency).

Each tool call opens the Relate store under the lock, does its work in one
transaction and closes it, so the server never holds the single-process store
while hooks or the CLI need it.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any

from . import __version__, memory
from .render import Painter, advice_for, facts_list, fact_block, impact_block, status_block, verify_block
from .store import Store, default_store_path, find_repo_root

PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = """\
Receipts is this repository's long-term memory, shared across sessions. Every memory cites the code that \
proves it, and is re-checked against the working tree whenever you read it.
- Before relying on what you think you know about this codebase, call `recall` (by question and/or target).
- STALE / SUSPECT memories are leads, not facts: `verify` them, then `reaffirm`, `revise` or `retract`.
- When you learn something durable (an invariant, convention, gotcha, decision, how-to), `remember` it with \
receipts. Cite symbols as path::Name, exact lines as {path, quote}.
- Before changing shared code, `impact` shows which memories depend on it, so you can update them in the same change.
"""

CITE_SCHEMA = {
    "oneOf": [
        {"type": "string", "description": "Symbol ('tinyshop/money.py::Money', 'Money.times') or file path"},
        {"type": "object", "properties": {
            "symbol": {"type": "string", "description": "Function/class/method/CONSTANT, optionally path::Name"},
            "path": {"type": "string", "description": "Repo-relative file path"},
            "quote": {"type": "string", "description": "Exact text from `path` that proves the claim"},
            "url": {"type": "string"},
            "note": {"type": "string", "description": "Something only a person can vouch for (never auto-checked)"},
        }},
    ]
}

TOOLS = [
    {
        "name": "recall",
        "description": (
            "Look up what earlier sessions learned about this codebase before re-deriving it or relying on "
            "memory. Search by a natural-language question and/or a code target (file path or symbol). Results "
            "include receipts re-checked against the current code: STALE = cited code changed since the memory "
            "was written; SUSPECT = code it depends on, or a memory it builds on, changed. Also returns memories "
            "about connected code (callers, callees, imports)."),
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What you want to know"},
            "target": {"type": "string", "description": "File path or symbol to get memories about"},
            "limit": {"type": "integer", "default": 8, "minimum": 1, "maximum": 30},
        }},
    },
    {
        "name": "remember",
        "description": (
            "Save a durable fact about this codebase for future sessions (invariant, convention, gotcha, "
            "decision, how-to). Every memory needs receipts: cite the code that proves it so future sessions are "
            "told when that code changes. Cite the narrowest code that proves the claim (a method rather than its "
            "whole class, an exact quoted line rather than a file). Use derived_from when the conclusion builds on "
            "other memories. Skip trivia that is obvious from one read of one file."),
        "inputSchema": {"type": "object", "required": ["text", "cites"], "properties": {
            "text": {"type": "string", "description": "One self-contained claim, specific enough to be wrong"},
            "cites": {"type": "array", "minItems": 1, "items": CITE_SCHEMA},
            "kind": {"type": "string", "enum": list(memory.KINDS), "default": "fact"},
            "derived_from": {"type": "array", "items": {"type": "string"}, "description": "Memory ids, e.g. ['M3']"},
            "tags": {"type": "array", "items": {"type": "string"}},
        }},
    },
    {
        "name": "verify",
        "description": ("Show one memory with every receipt diffed against today's code, plus the change that "
                        "broke it. Use on STALE/SUSPECT memories before deciding to reaffirm, revise or retract."),
        "inputSchema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}},
    },
    {
        "name": "reaffirm",
        "description": ("You checked the current code and the memory is still true as written: re-anchor its "
                        "receipts to today's code. Fails if a cited symbol or quote no longer exists."),
        "inputSchema": {"type": "object", "required": ["id"], "properties": {
            "id": {"type": "string"}, "note": {"type": "string", "description": "What you checked"}}},
    },
    {
        "name": "revise",
        "description": ("Replace an outdated memory with corrected text (and optionally new receipts; by default "
                        "the old receipts are re-anchored). The old version is kept as history; memories built on "
                        "it are flagged for review."),
        "inputSchema": {"type": "object", "required": ["id", "text"], "properties": {
            "id": {"type": "string"}, "text": {"type": "string"},
            "cites": {"type": "array", "items": CITE_SCHEMA},
            "derived_from": {"type": "array", "items": {"type": "string"}},
            "note": {"type": "string"}}},
    },
    {
        "name": "retract",
        "description": "Mark a memory as wrong and no longer useful. Memories built on it are flagged.",
        "inputSchema": {"type": "object", "required": ["id"], "properties": {
            "id": {"type": "string"}, "reason": {"type": "string"}}},
    },
    {
        "name": "impact",
        "description": ("Before changing a function, class, constant or file: which memories depend on it "
                        "directly, through code that calls it, or by derivation."),
        "inputSchema": {"type": "object", "required": ["target"], "properties": {
            "target": {"type": "string", "description": "File path or symbol"}}},
    },
    {
        "name": "memory_status",
        "description": "Counts by status, the last code sync, and every memory that needs attention.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class Server:
    def __init__(self, repo: Path | None = None, store_path: Path | None = None):
        self.repo = repo or find_repo_root()
        self.store_path = store_path or default_store_path(self.repo)
        self.p = Painter(False)

    # ---- tools ------------------------------------------------------------ #

    def call(self, name: str, args: dict[str, Any]) -> str:
        with Store(self.store_path) as s:
            if name == "recall":
                # Agents improvise argument names; accept the obvious ones.
                query = next((str(args[k]) for k in ("query", "question", "q", "text", "topic") if args.get(k)), "")
                target = next((str(args[k]) for k in ("target", "path", "symbol", "file") if args.get(k)), "")
                if not query and not target:
                    return status_block(memory.status(s), self.p) + \
                        "\n\nPass `query` (a question) and/or `target` (a file or symbol) to search."
                facts = memory.recall(s, self.repo, query, target=target, limit=int(args.get("limit") or 8))
                if not facts:
                    return ("No memories match. If you work this out, `remember` it with receipts so the next "
                            "session doesn't have to.")
                return facts_list(facts, self.p) + advice_for(facts)
            if name == "remember":
                f = memory.remember(s, self.repo, args.get("text", ""), args.get("cites") or [],
                                    kind=args.get("kind") or "fact", derived_from=args.get("derived_from") or [],
                                    tags=args.get("tags") or [], author="agent")
                out = "Remembered.\n" + fact_block(f, self.p, why=False)
                for w in f.get("warnings") or []:
                    out += f"\nTip: {w}"
                if f.get("related"):
                    out += "\n\nOther memories cite the same code; make sure they don't overlap or contradict it:\n"
                    out += "\n".join(f"  {r['id']} [{r['status']}] {r['text']}" for r in f["related"])
                return out
            if name == "verify":
                return verify_block(memory.verify(s, self.repo, args["id"]), self.p)
            if name == "reaffirm":
                f = memory.reaffirm(s, self.repo, args["id"], note=args.get("note", ""))
                return ("Reaffirmed.\n" if f["status"] == "fresh" else "") + fact_block(f, self.p, why=False) + \
                    (f"\n{f['warning']}" if f.get("warning") else "")
            if name == "revise":
                f = memory.revise(s, self.repo, args["id"], args.get("text", ""), cites=args.get("cites"),
                                  derived_from=args.get("derived_from"), note=args.get("note", ""))
                return f"Revised {args['id']} → {f['id']}.\n" + fact_block(f, self.p, why=False)
            if name == "retract":
                f = memory.retract(s, args["id"], reason=args.get("reason", ""))
                return "Retracted.\n" + fact_block(f, self.p, why=False)
            if name == "impact":
                return impact_block(memory.impact(s, self.repo, args["target"]), self.p)
            if name == "memory_status":
                return status_block(memory.status(s), self.p)
        raise KeyError(name)

    # ---- protocol --------------------------------------------------------- #

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        mid = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}
        if mid is None:  # notification
            return None
        try:
            if method == "initialize":
                want = params.get("protocolVersion")
                return self._ok(mid, {
                    "protocolVersion": want if want in PROTOCOLS else PROTOCOLS[1],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "receipts", "version": __version__},
                    "instructions": INSTRUCTIONS,
                })
            if method == "ping":
                return self._ok(mid, {})
            if method == "tools/list":
                return self._ok(mid, {"tools": TOOLS})
            if method == "tools/call":
                name = params.get("name", "")
                if name not in {t["name"] for t in TOOLS}:
                    return self._err(mid, -32602, f"unknown tool: {name}")
                try:
                    text = self.call(name, params.get("arguments") or {})
                    return self._ok(mid, {"content": [{"type": "text", "text": text}], "isError": False})
                except memory.MemoryError_ as e:
                    return self._ok(mid, {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
                except KeyError as e:
                    return self._ok(mid, {"content": [{"type": "text", "text": f"Error: missing argument {e}"}],
                                          "isError": True})
            if method in ("resources/list", "prompts/list"):
                return self._ok(mid, {method.split("/")[0]: []})
            return self._err(mid, -32601, f"method not found: {method}")
        except Exception as e:  # never crash the server on one bad call
            traceback.print_exc(file=sys.stderr)
            return self._ok(mid, {"content": [{"type": "text", "text": f"Internal error: {e}"}], "isError": True}) \
                if method == "tools/call" else self._err(mid, -32603, str(e))

    @staticmethod
    def _ok(mid, result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    @staticmethod
    def _err(mid, code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    def serve(self, stdin=None, stdout=None) -> None:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                stdout.write(json.dumps(self._err(None, -32700, "parse error")) + "\n")
                stdout.flush()
                continue
            batch = msg if isinstance(msg, list) else [msg]
            replies = [r for r in (self.handle(m) for m in batch) if r is not None]
            if replies:
                stdout.write(json.dumps(replies if isinstance(msg, list) else replies[0]) + "\n")
                stdout.flush()


def main(repo: Path | None = None, store: Path | None = None) -> None:
    Server(repo, store).serve()
