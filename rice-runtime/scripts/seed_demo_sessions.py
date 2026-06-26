"""Seed demo chat history into state.db.

Inserts the (formerly mock) demo conversations as *real* sessions + messages so
the ChatScreen history sidebar is populated from the DB — there is no in-app chat
mock anymore. Content lives in `demo_sessions.json` (extracted once from the old
`learningData.ts` THREADS).

Idempotent: every row it writes uses a `seed_…` id and is deleted up-front, so
re-running refreshes the demo set without touching your real conversations.

    python scripts/seed_demo_sessions.py            # → ~/.rice/state.db
    python scripts/seed_demo_sessions.py --db /path/to/state.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODEL = "qwen3.5:9b-mlx"
HERE = Path(__file__).resolve().parent

# How far back each sidebar group sits, plus a per-conversation spread so the
# DESC-by-started_at ordering looks like a natural history.
GROUP_BASE = {
    "오늘": timedelta(hours=2),
    "지난 7일": timedelta(days=2),
    "이전": timedelta(days=35),
}


def _fmt(dt: datetime) -> str:
    """Match sqlite's datetime('now') text shape (UTC, no tz suffix)."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def seed(db_path: Path, data_path: Path) -> None:
    conversations = json.loads(data_path.read_text(encoding="utf-8"))
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        # wipe any previous seed (messages cascade via FK, but be explicit so FTS
        # delete-triggers fire for the message rows too).
        seed_ids = [f"seed_{c['id']}" for c in conversations]
        qmarks = ",".join("?" * len(seed_ids))
        conn.execute(f"DELETE FROM messages WHERE session_id IN ({qmarks})", seed_ids)
        conn.execute(f"DELETE FROM sessions WHERE id IN ({qmarks})", seed_ids)

        now = datetime.now(timezone.utc)
        per_group_count: dict[str, int] = {}
        total_msgs = 0
        for conv in conversations:
            group = conv["group"]
            idx = per_group_count.get(group, 0)
            per_group_count[group] = idx + 1
            # newer items first within a group: subtract less for earlier idx
            started = now - GROUP_BASE.get(group, timedelta(days=1)) - timedelta(hours=idx * 9)

            sid = f"seed_{conv['id']}"
            conn.execute(
                "INSERT INTO sessions(id, title, model, started_at) VALUES (?, ?, ?, ?)",
                (sid, conv["title"], MODEL, _fmt(started)),
            )
            base_ms = int(started.timestamp() * 1000)
            for i, turn in enumerate(conv["turns"]):
                conn.execute(
                    "INSERT INTO messages(id, session_id, role, content, ts) VALUES (?, ?, ?, ?, ?)",
                    (f"{sid}_m{i}", sid, turn["role"], turn["content"], base_ms + i * 60_000),
                )
                total_msgs += 1
        conn.commit()
        print(f"seeded {len(conversations)} sessions, {total_msgs} messages → {db_path}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="seed_demo_sessions")
    parser.add_argument("--db", type=Path, default=Path.home() / ".rice" / "state.db")
    parser.add_argument("--data", type=Path, default=HERE / "demo_sessions.json")
    args = parser.parse_args()
    if not args.db.exists():
        raise SystemExit(f"state.db not found at {args.db} — run the runtime once to create it.")
    seed(args.db, args.data)


if __name__ == "__main__":
    main()
