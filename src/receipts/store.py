"""The Relate store behind Receipts.

One ``.rl`` file per repository (``<repo>/.receipts/memory.rl``). Relate is an
embedded, single-process engine, so every operation (an MCP tool call, a hook,
a CLI command) opens the store under an exclusive file lock, runs inside one
explicit transaction, and closes it again. Opening takes a few milliseconds,
which is what makes this pattern viable.
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import relate

try:  # POSIX
    import fcntl
    msvcrt = None
except ImportError:  # Windows
    fcntl = None
    import msvcrt

SCHEMA_VERSION = 1


def _try_lock(fh, shared: bool) -> bool:
    """Non-blocking file lock. Windows has no shared locks, so readers lock exclusively there."""
    try:
        if fcntl is not None:
            fcntl.flock(fh, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        else:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except (BlockingIOError, PermissionError, OSError):
        return False


def _unlock(fh) -> None:
    if fcntl is not None:
        fcntl.flock(fh, fcntl.LOCK_UN)
    else:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

SCHEMA = [
    "CREATE CONSTRAINT fact_id IF NOT EXISTS FOR (f:Fact) REQUIRE f.id IS UNIQUE",
    "CREATE CONSTRAINT file_path IF NOT EXISTS FOR (f:File) REQUIRE f.path IS UNIQUE",
    "CREATE CONSTRAINT symbol_key IF NOT EXISTS FOR (s:Symbol) REQUIRE s.key IS UNIQUE",
    "CREATE CONSTRAINT source_key IF NOT EXISTS FOR (s:Source) REQUIRE s.key IS UNIQUE",
    "CREATE CONSTRAINT change_id IF NOT EXISTS FOR (c:Change) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT sync_id IF NOT EXISTS FOR (s:Sync) REQUIRE s.id IS UNIQUE",
    "CREATE FULLTEXT INDEX fact_text IF NOT EXISTS FOR (f:Fact) ON EACH [f.text, f.search_text]",
]


def now_iso() -> str:
    override = os.environ.get("RECEIPTS_NOW")  # deterministic demos and tests
    if override:
        return override
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def find_repo_root(start: str | os.PathLike | None = None) -> Path:
    """The git top-level of ``start`` (or cwd), else ``start`` itself."""
    env = os.environ.get("RECEIPTS_REPO")
    if env:
        return Path(env).resolve()
    base = Path(start or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
    try:
        out = subprocess.run(
            ["git", "-C", str(base), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    return base


def default_store_path(repo: Path) -> Path:
    env = os.environ.get("RECEIPTS_STORE")
    if env:
        return Path(env).resolve()
    return repo / ".receipts" / "memory.rl"


class StoreBusy(RuntimeError):
    pass


class Store:
    """``with Store(path) as s: s.q("MATCH ...")`` runs in one transaction."""

    def __init__(self, path: str | os.PathLike, *, read_only: bool = False, lock_timeout: float = 20.0):
        self.path = Path(path)
        self.read_only = read_only
        self.lock_timeout = lock_timeout
        self._lock_fh = None
        self.db = None

    # -- lifecycle ---------------------------------------------------------- #

    def __enter__(self) -> "Store":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._acquire_lock()
        try:
            if self.read_only and not self.path.exists():
                # A read on a store that was never written: give an empty one.
                self.db = relate.connect(":memory:")
            else:
                self.db = relate.connect(str(self.path), read_only=self.read_only)
            if not self.read_only:
                self._ensure_schema()
                self.db.autocommit = False
        except Exception:
            self._release_lock()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self.db is not None and not self.read_only:
                if exc_type is None:
                    self.db.commit()
                else:
                    self.db.rollback()
        finally:
            try:
                if self.db is not None:
                    self.db.close()
            finally:
                self.db = None
                self._release_lock()

    def _acquire_lock(self) -> None:
        lock_path = self.path.with_name(self.path.name + ".lock")
        self._lock_fh = open(lock_path, "a+")
        deadline = time.monotonic() + self.lock_timeout
        while not _try_lock(self._lock_fh, self.read_only):
            if time.monotonic() > deadline:
                self._lock_fh.close()
                self._lock_fh = None
                raise StoreBusy(f"store is busy: {self.path}")
            time.sleep(0.02)

    def _release_lock(self) -> None:
        if self._lock_fh is not None:
            try:
                _unlock(self._lock_fh)
            finally:
                self._lock_fh.close()
                self._lock_fh = None

    def _ensure_schema(self) -> None:
        rows = list(self.db.execute("MATCH (m:Meta {key: 'receipts'}) RETURN m.schema AS v"))
        if rows and rows[0].get("v") == SCHEMA_VERSION:
            return
        for stmt in SCHEMA:
            self.db.execute(stmt)
        self.db.execute(
            "MERGE (m:Meta {key: 'receipts'}) "
            "ON CREATE SET m.next_fact = 1, m.next_change = 1, m.next_sync = 1 "
            "SET m.schema = $v",
            {"v": SCHEMA_VERSION},
        )

    # -- queries ------------------------------------------------------------ #

    def q(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute(cypher, params)]

    def one(self, cypher: str, **params: Any) -> dict[str, Any] | None:
        rows = self.q(cypher, **params)
        return rows[0] if rows else None

    def checkpoint(self) -> None:
        """Commit what we have and keep going in a fresh transaction.

        Relate 1.0.2 panics when a statement deletes a node or relationship
        created earlier in the same open transaction, so call this before any
        delete that might touch something this operation just created.
        """
        if not self.read_only:
            self.db.commit()

    def next_ids(self, counter: str, n: int) -> int:
        """Reserve ``n`` consecutive numbers from a counter; returns the first."""
        row = self.one(
            f"MATCH (m:Meta {{key: 'receipts'}}) SET m.{counter} = coalesce(m.{counter}, 1) + $n "
            f"RETURN m.{counter} - $n AS first", n=n)
        return int(row["first"])

    def next_id(self, counter: str, prefix: str) -> str:
        row = self.one(
            f"MATCH (m:Meta {{key: 'receipts'}}) SET m.{counter} = coalesce(m.{counter}, 1) + 1 "
            f"RETURN m.{counter} - 1 AS n"
        )
        return f"{prefix}{row['n']}"
