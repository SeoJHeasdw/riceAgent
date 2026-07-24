"""Schema baseline and migration-runner regression tests."""

from __future__ import annotations

import sqlite3

import pytest

from rice_runtime.core.storage import sqlite as sqlite_storage
from rice_runtime.core.storage.sqlite import Database


EXPECTED_COLUMNS = {
    "messages": {"metadata"},
    "memories": {"status", "derived_from_external", "source_message_ids"},
    "installed_artifacts": {"enabled", "risk_flags"},
    "provider_models": {"extra"},
    "connectors": {"error_code", "error_detail"},
    "connector_credentials": {"tools_json", "pending_tools_json"},
    "learning_jobs": {"evidence", "attempts"},
}

EXPECTED_TABLES = {
    "attachments",
    "background_tasks",
    "connector_credentials",
    "connector_tool_permissions",
    "connectors",
    "custom_connector_descriptors",
    "installed_artifacts",
    "learning_episodes",
    "learning_events",
    "learning_jobs",
    "memories",
    "memories_fts",
    "messages",
    "messages_fts",
    "oauth_pending_states",
    "provider_models",
    "schema_migrations",
    "sessions",
    "skill_candidates",
    "skill_suggestions",
}


def test_fresh_database_uses_single_complete_baseline(tmp_path):
    db = Database(tmp_path / "state.db").connect()

    versions = db.query_all("SELECT version FROM schema_migrations ORDER BY version")
    assert [row["version"] for row in versions] == ["0001_init"]

    tables = {
        row["name"]
        for row in db.query_all(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "AND name NOT LIKE 'messages_fts_%' "
            "AND name NOT LIKE 'memories_fts_%'"
        )
    }
    assert tables == EXPECTED_TABLES

    for table, expected in EXPECTED_COLUMNS.items():
        actual = {row["name"] for row in db.query_all(f"PRAGMA table_info({table})")}
        assert expected <= actual

    db.close()


def test_failed_migration_rolls_back_and_can_retry(tmp_path, monkeypatch):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    migration = migrations / "0001_test.sql"
    migration.write_text(
        "CREATE TABLE partial_change (id INTEGER);\n"
        "THIS IS INVALID SQL;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sqlite_storage, "MIGRATIONS_DIR", migrations)

    path = tmp_path / "state.db"
    db = Database(path)
    with pytest.raises(sqlite3.OperationalError):
        db.connect()

    inspection = sqlite3.connect(path)
    partial = inspection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'partial_change'"
    ).fetchone()
    applied = inspection.execute(
        "SELECT 1 FROM schema_migrations WHERE version = '0001_test'"
    ).fetchone()
    inspection.close()
    assert partial is None
    assert applied is None

    migration.write_text(
        "CREATE TABLE complete_change (id INTEGER);\n",
        encoding="utf-8",
    )
    db.connect()
    assert db.query_one(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'complete_change'"
    )
    assert db.query_one(
        "SELECT 1 FROM schema_migrations WHERE version = '0001_test'"
    )
    db.close()
