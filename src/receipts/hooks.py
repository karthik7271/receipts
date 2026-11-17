"""Claude Code hooks: keep memory honest while the agent works.

* ``session-start``: full sync; tell the agent what went stale since last time.
* ``post-tool-use``:
    - after Edit/Write/MultiEdit/NotebookEdit: re-sync the file and name the
      memories the edit just invalidated, so the agent fixes them in the
      same change;
    - after a git command that rewrites the tree (pull, checkout, merge...):
      full sync;
    - after Read: surface what is already known about that file.

A hook must never break the agent: every failure is logged to
``.receipts/hook.log`` and the hook exits 0 with no output.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

from . import memory
from .graph import sync
from .render import Painter, badge
from .store import Store, default_store_path, find_repo_root

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
GIT_REWRITE = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?(pull|checkout|switch|merge|rebase|reset|stash|cherry-pick|"
                         r"revert|apply|am|restore)\b")
MAX_LISTED = 10
P = Painter(False)


def _emit(event: str, text: str) -> None:
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))


def _fmt(f: dict) -> str:
    line = f"- {f['id']} [{f['status'].upper()}] {f['text']}"
    reasons = f.get("reasons") or []
    if reasons:
        line += f"  ({'; '.join(reasons)})"
    return line


def session_start(payload: dict, repo: Path, store_path: Path) -> str:
    with Store(store_path) as s:
        report = sync(s, repo)
        st = memory.status(s)
    total = sum(st["counts"].values())
    if total == 0:
        return ("Receipts memory is empty for this repo. When you learn something durable about this codebase "
                "(an invariant, convention, gotcha, decision), save it with the `remember` tool and cite the code "
                "that proves it.")
    live = sum(st["counts"].get(k, 0) for k in ("fresh", "stale", "suspect"))
    lines = [f"Receipts: {live} memories about this repo ({st['counts'].get('fresh', 0)} fresh). "
             "Call `recall` before relying on what you think you know about this codebase."]
    if report.sync_id and report.files_changed:
        lines.append(f"Since the last sync, {len(report.files_changed)} files changed.")
    if st["attention"]:
        lines.append("These memories no longer match the code. Don't trust them as written; `verify` then "
                     "`reaffirm`/`revise`/`retract` when you touch the related code:")
        lines += [_fmt(f) for f in st["attention"][:MAX_LISTED]]
        if len(st["attention"]) > MAX_LISTED:
            lines.append(f"- … and {len(st['attention']) - MAX_LISTED} more (see `memory_status`).")
    return "\n".join(lines)


def _rel(repo: Path, path: str) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        p = repo / p
    try:
        return p.resolve().relative_to(repo).as_posix()
    except ValueError:
        return None


def post_tool_use(payload: dict, repo: Path, store_path: Path) -> str:
    tool = payload.get("tool_name", "")
    tin = payload.get("tool_input") or {}

    if tool in EDIT_TOOLS:
        rel = _rel(repo, tin.get("file_path") or tin.get("notebook_path") or "")
        if not rel or not store_path.exists():
            return ""
        with Store(store_path) as s:
            report = sync(s, repo, only=[rel])
            bad = [memory.get_fact(s, t["id"]) for t in report.newly_invalid]
        if not bad:
            return ""
        lines = [f"Receipts: your edit to {rel} changed code that these memories cite. Before you finish, "
                 "check each one and `reaffirm` (still true), `revise` (update the text) or `retract` it:"]
        lines += [_fmt(f) for f in bad if f]
        return "\n".join(lines)

    if tool == "Bash":
        cmd = str(tin.get("command", ""))
        if not GIT_REWRITE.search(cmd) or not store_path.exists():
            return ""
        with Store(store_path) as s:
            report = sync(s, repo)
            bad = [memory.get_fact(s, t["id"]) for t in report.newly_invalid]
        if not bad:
            return ""
        lines = [f"Receipts: that git command changed {len(report.files_changed)} files, and these memories "
                 "no longer match the code:"]
        lines += [_fmt(f) for f in bad[:MAX_LISTED] if f]
        return "\n".join(lines)

    if tool == "Read":
        rel = _rel(repo, tin.get("file_path", ""))
        if not rel or not store_path.exists() or os.environ.get("RECEIPTS_READ_HINTS") == "0":
            return ""
        with Store(store_path) as s:
            facts = [f for f in memory.recall(s, repo, target=rel, limit=6) if f["why"].startswith("cites ")]
        if not facts:
            return ""
        lines = [f"Receipts: what earlier sessions recorded about {rel}:"]
        lines += [_fmt(f) for f in facts]
        return "\n".join(lines)

    return ""


def run(kind: str) -> int:
    started = time.monotonic()
    raw = sys.stdin.read()
    repo = None
    try:
        payload = json.loads(raw or "{}")
        repo = find_repo_root(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR"))
        store_path = default_store_path(repo)
        if kind == "session-start":
            _emit("SessionStart", session_start(payload, repo, store_path))
        elif kind == "post-tool-use":
            _emit("PostToolUse", post_tool_use(payload, repo, store_path))
        else:
            raise ValueError(f"unknown hook {kind}")
    except Exception:
        try:
            log_dir = (repo or Path.cwd()) / ".receipts"
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_dir / "hook.log", "a") as fh:
                fh.write(f"--- {kind} {time.ctime()} ({time.monotonic() - started:.2f}s)\n")
                fh.write(traceback.format_exc())
        except OSError:
            pass
    return 0
