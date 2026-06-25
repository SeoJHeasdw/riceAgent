"""learning.memory — fact/preference CRUD and FTS5-backed retrieval.

This sub-package is the **persistence layer** for the memory half of the learning
loop (LEARNING_LOOP §6, RUNTIME_DESIGN §3). Two narrow modules:

  store.py  — INSERT/LIST/DELETE for memories (kind: fact | preference).
  search.py — FTS5 keyword search over the memories table.

The `state.db` schema (memories + memories_fts trigger) is already defined in
`core/storage/migrations/0001_init.sql`; nothing here needs to migrate further for
T1. The FTS5 triggers keep `memories_fts` in sync automatically on INSERT/DELETE.
"""
