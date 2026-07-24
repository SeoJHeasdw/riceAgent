"""Local storage abstraction (#14, RUNTIME_DIRECTORY) — SQLite in WAL mode.

This is the **only** module that talks to `sqlite3` directly. Everything else
(session store, memory store, …) receives a `Database` and runs queries through it.
That keeps the storage engine swappable and gives one place for connection policy.

Policy decisions baked in here:
  - **WAL journal mode** (RUNTIME_DESIGN §7) — concurrent reads during a write,
    which matters because a turn streams while reading history.
  - **Single connection, single process** (RUNTIME_DESIGN §2: one active session).
    SQLite connections aren't thread-safe across threads by default; FastAPI runs
    handlers on a threadpool, so we guard with a lock and `check_same_thread=False`.
    For the MVP's low concurrency this is correct and simple.
  - **Migrations** run on connect, tracked in `schema_migrations`. Forward-only.
  - `row_factory = sqlite3.Row` so callers get dict-like rows.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class Database:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def connect(self) -> "Database":
        if self._conn is not None:
            return self
        is_memory = self._path == ":memory:"
        if not is_memory:
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        if not is_memory:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
        self._conn = conn
        try:
            self._run_migrations()
        except BaseException:
            conn.close()
            self._conn = None
            raise
        return self

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @property
    def raw(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected — call connect() first")
        return self._conn

    # ── query helpers (all serialized through the lock) ───────────────────────
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.raw.execute(sql, params)
            self.raw.commit()
            return cur

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> sqlite3.Cursor:
        with self._lock:
            cur = self.raw.executemany(sql, seq)
            self.raw.commit()
            return cur

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.raw.execute(sql, params).fetchone()

    def query_all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.raw.execute(sql, params).fetchall()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """여러 문장을 하나의 트랜잭션으로 묶는다 — 전부 커밋되거나 전부 롤백된다.

        `execute()`/`executemany()`는 문장마다 자체 커밋하므로 "DELETE 후 INSERT"
        같은 교체 패턴이 중간에 죽으면 반쪽 상태가 남는다(예: provider_models 교체).
        블록 안에서는 반드시 yield된 connection으로 직접 실행할 것 — self.execute()를
        부르면 그 문장이 조기 커밋되어 원자성이 깨진다.
        """
        with self._lock:
            try:
                yield self.raw
                self.raw.commit()
            except BaseException:
                self.raw.rollback()
                raise

    # ── migrations (forward-only, tracked) ────────────────────────────────────
    def _run_migrations(self) -> None:
        with self._lock:
            self.raw.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "  version TEXT PRIMARY KEY, applied_at TEXT DEFAULT (datetime('now')))"
            )
            self.raw.commit()
            applied = {r["version"] for r in self.raw.execute("SELECT version FROM schema_migrations")}
            for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
                version = sql_file.stem
                if version in applied:
                    continue
                # ``executescript`` commits any pending transaction before it
                # starts. Put BEGIN/marker/COMMIT inside the script so the schema
                # change and its version record are one atomic unit. Migration
                # files must therefore not contain transaction control statements.
                version_literal = version.replace("'", "''")
                script = (
                    "BEGIN IMMEDIATE;\n"
                    f"{sql_file.read_text(encoding='utf-8')}\n"
                    "INSERT INTO schema_migrations(version) "
                    f"VALUES ('{version_literal}');\n"
                    "COMMIT;\n"
                )
                try:
                    self.raw.executescript(script)
                except BaseException:
                    if self.raw.in_transaction:
                        self.raw.rollback()
                    raise
