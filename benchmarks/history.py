"""How noisy is each kind of receipt on real release history?

For every Python function, method, class and constant at an older release
(as if a memory cited each one), count what would be flagged at the newer
release by three kinds of receipt:

  file      the memory cites the file it lives in (what notes and most
            "memory" tools amount to)
  text      the memory cites the symbol's exact source text
  receipts  Receipts' semantic receipt: AST hash without names or docstrings,
            with renames and moves followed

Receipts' numbers come from the real pipeline: both releases are synced into
a Relate store and read back from the Change nodes. It also reports how far
"upstream drift" (suspect via a 2-hop signature change) fans out.

Usage:  uv run python benchmarks/history.py <git clone> <tag> <tag> [<tag> ...]
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path
from statistics import median

from receipts.codegraph import scan_repo
from receipts.graph import sync
from receipts.store import Store


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def python_files(repo: Path) -> list[str]:
    return [p for p in git(repo, "ls-files", "*.py").splitlines()
            if not any(part in p for part in ("tests/", "test_", "docs/", "examples/"))]


def compare(repo: Path, a: str, b: str) -> dict:
    git(repo, "checkout", "-q", a)
    files_a = python_files(repo)
    scan_a = scan_repo(repo, files_a)
    with tempfile.TemporaryDirectory() as tmp:
        store_path = Path(tmp) / "m.rl"
        with Store(store_path) as s:
            sync(s, repo, only=files_a)
        git(repo, "checkout", "-q", b)
        files_b = python_files(repo)
        scan_b = scan_repo(repo, files_b)
        t0 = time.perf_counter()
        with Store(store_path) as s:
            report = sync(s, repo, only=sorted(set(files_a) | set(files_b)))
            sync_ms = (time.perf_counter() - t0) * 1000
            changed = {c["key"] for c in report.changes if c["kind"] in ("body", "signature")}
            deleted = {c["key"] for c in report.changes if c["kind"] == "deleted"}
            moved = dict(report.moved)
            drift = s.q(
                "MATCH (s:Symbol)-[:CALLS]->{1,2}(t:Symbol)<-[:ON]-(ch:Change {sync: $sid}) "
                "WHERE ch.kind IN ['signature', 'types'] AND s.deleted = false AND NOT EXISTS { (s)<-[:ON]-(:Change {sync: $sid}) } "
                "RETURN t.key AS changed, count(DISTINCT s) AS suspects", sid=report.sync_id)

    universe = {s.key: (s, fi) for fi in scan_a.values() for s in fi.symbols}
    new_syms = {s.key: s for fi in scan_b.values() for s in fi.symbols}
    file_flag = text_flag = rec_flag = cosmetic = 0
    for key, (sym, fi) in universe.items():
        fb = scan_b.get(fi.path)
        if fb is None or fb.hash != fi.hash:
            file_flag += 1
        nb = new_syms.get(key)
        text_changed = nb is None or nb.source != sym.source
        if text_changed:
            text_flag += 1
        semantic = key in changed or key in deleted
        if semantic:
            rec_flag += 1
        elif text_changed and key not in moved:
            cosmetic += 1
    n = len(universe)
    fan = [r["suspects"] for r in drift]
    return {
        "pair": f"{a} → {b}", "symbols": n, "file": file_flag, "text": text_flag, "receipts": rec_flag,
        "cosmetic": cosmetic, "moved": len(moved), "deleted": len(deleted),
        "sig_changes": len(fan), "fan_median": median(fan) if fan else 0, "fan_max": max(fan) if fan else 0,
        "sync_ms": sync_ms,
    }


def pct(x: int, n: int) -> str:
    return f"{x} ({100 * x / n:.0f}%)" if n else "0"


def main() -> None:
    repo, tags = Path(sys.argv[1]).resolve(), sys.argv[2:]
    head = git(repo, "rev-parse", "HEAD").strip()
    rows = []
    try:
        for a, b in zip(tags, tags[1:]):
            r = compare(repo, a, b)
            rows.append(r)
            print(r, file=sys.stderr)
    finally:
        git(repo, "checkout", "-q", head)
    name = repo.name
    print(f"\n**{name}**\n")
    print("| Releases | Symbols cited | Flagged: file receipt | Flagged: text receipt | Flagged: Receipts "
          "| Cosmetic edits ignored | Renames followed | Contract changes (suspects each, median / max) | Sync |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        n = r["symbols"]
        print(f"| {r['pair']} | {n} | {pct(r['file'], n)} | {pct(r['text'], n)} | {pct(r['receipts'], n)} "
              f"| {r['cosmetic']} | {r['moved']} | {r['sig_changes']} ({r['fan_median']:g} / {r['fan_max']}) "
              f"| {r['sync_ms'] / 1000:.1f} s |")


if __name__ == "__main__":
    main()
