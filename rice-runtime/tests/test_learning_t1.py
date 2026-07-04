"""test_learning_t1.py — T1 수용 기준 검증 (memory/store.py, memory/search.py).

수용 기준 (learning.md T1):
  1. facts/preferences 추가 → list_memories()로 조회된다.
  2. FTS5 검색이 키워드로 매칭된다.
  3. prefetch가 메모리를 system_prompt_additions에 반영하고 memories 튜플을 반환한다.

격리: 실제 ~/.rice 는 건드리지 않는다. RicePaths.from_env(tempfile.mkdtemp())로 임시
루트를 사용한다 (CONVENTIONS §6).

외부 의존성 없음 — 순수 SQLite 로직 테스트.
"""

from __future__ import annotations

import tempfile

import pytest

from rice_runtime.core.config import LearningConfig
from rice_runtime.core.errors import ValidationError
from rice_runtime.core.paths import RicePaths
from rice_runtime.core.storage.sqlite import Database
from rice_runtime.core.types import MemoryItem
from rice_runtime.learning.memory.search import MemorySearch
from rice_runtime.learning.memory.store import MemoryStore
from rice_runtime.learning.service import LearningService


# ── helpers ───────────────────────────────────────────────────────────────────

def _fresh_db() -> Database:
    """In-memory SQLite with full migrations applied."""
    db = Database(":memory:").connect()
    return db


def _fresh_paths() -> RicePaths:
    tmp = tempfile.mkdtemp(prefix="rice-t1-test-")
    return RicePaths.from_env(tmp).ensure()


def _service(db: Database) -> LearningService:
    # 버그#T1-격리: paths를 안 넘기면 RicePaths.from_env()가 실제 ~/.rice로
    # 폴백한다 — 이 파일 docstring이 주장하는 "실제 ~/.rice 는 건드리지 않는다"가
    # 실제로는 지켜지지 않고 있었다(사용자가 앱을 직접 렌더링해 만든 진짜
    # ~/.rice/skills/caveman.md가 있으면 prefetch 관련 테스트가 그걸 읽어버려
    # 깨짐). t2/t3/t4와 동일하게 임시 경로를 명시로 격리한다.
    return LearningService(db, LearningConfig(), _fresh_paths())


# ── MemoryStore tests (pure logic, no external deps) ──────────────────────────

class TestMemoryStore:
    def test_add_fact_returns_item_with_id(self):
        store = MemoryStore(_fresh_db())
        item = store.add("fact", "Project uses pytest")
        assert item.kind == "fact"
        assert item.content == "Project uses pytest"
        assert item.id.startswith("mem_")

    def test_add_preference_returns_item(self):
        store = MemoryStore(_fresh_db())
        item = store.add("preference", "User prefers concise answers")
        assert item.kind == "preference"

    def test_list_returns_newest_first(self):
        db = _fresh_db()
        store = MemoryStore(db)
        store.add("fact", "A")
        store.add("fact", "B")
        store.add("fact", "C")
        items = store.list()
        assert [i.content for i in items] == ["C", "B", "A"]

    def test_add_invalid_kind_raises_validation_error(self):
        store = MemoryStore(_fresh_db())
        with pytest.raises(ValidationError):
            store.add("procedure", "should fail")

    def test_add_empty_content_raises_validation_error(self):
        store = MemoryStore(_fresh_db())
        with pytest.raises(ValidationError):
            store.add("fact", "   ")

    def test_add_strips_whitespace(self):
        store = MemoryStore(_fresh_db())
        item = store.add("fact", "  trimmed  ")
        assert item.content == "trimmed"

    def test_list_empty_when_no_memories(self):
        store = MemoryStore(_fresh_db())
        assert store.list() == []


# ── MemorySearch tests ────────────────────────────────────────────────────────

class TestMemorySearch:
    def _populated(self) -> tuple[Database, MemoryStore, MemorySearch]:
        db = _fresh_db()
        store = MemoryStore(db)
        search = MemorySearch(db)
        store.add("fact", "Project uses pytest for testing")
        store.add("fact", "Project language is Python")
        store.add("fact", "Database backend is SQLite")
        store.add("preference", "User prefers concise answers")
        return db, store, search

    def test_recent_returns_facts_only(self):
        _, _, search = self._populated()
        items = search.recent(10)
        assert all(i.kind == "fact" for i in items)

    def test_recent_respects_n_limit(self):
        _, _, search = self._populated()
        items = search.recent(2)
        assert len(items) == 2

    def test_fts_search_matches_keyword(self):
        _, _, search = self._populated()
        results = search.fts_search("pytest", k=5)
        assert any("pytest" in r.content for r in results)

    def test_fts_search_empty_query_returns_empty(self):
        _, _, search = self._populated()
        assert search.fts_search("", k=5) == []

    def test_fts_search_invalid_query_returns_empty_not_crash(self):
        """Malformed FTS5 query must not crash prefetch (fail-closed)."""
        _, _, search = self._populated()
        # bare '*' is invalid FTS5 syntax
        result = search.fts_search("*", k=5)
        assert result == []

    def test_fts_search_no_match_returns_empty(self):
        _, _, search = self._populated()
        assert search.fts_search("kubernetes_zxqw_nonexistent", k=5) == []

    def test_fts_search_k_limits_results(self):
        db = _fresh_db()
        store = MemoryStore(db)
        search = MemorySearch(db)
        for i in range(10):
            store.add("fact", f"fact number {i} about python")
        results = search.fts_search("python", k=3)
        assert len(results) <= 3


# ── LearningService integration tests (T1 acceptance criteria) ────────────────

class TestLearningServiceT1:
    """End-to-end through the service facade: add → list → prefetch."""

    def setup_method(self):
        self._db = _fresh_db()
        self._svc = _service(self._db)

    def test_list_memories_initially_empty(self):
        assert self._svc.list_memories() == []

    def test_add_via_store_then_list_via_service(self):
        # Service facade should see what the store wrote (same DB).
        MemoryStore(self._db).add("fact", "Project uses SQLite")
        memories = self._svc.list_memories()
        assert len(memories) == 1
        assert memories[0].content == "Project uses SQLite"

    def test_prefetch_includes_guidance(self):
        result = self._svc.prefetch("sess_1", "hello")
        assert "propose_skill" in result.system_prompt_additions

    def test_prefetch_no_memories_returns_empty_tuple(self):
        result = self._svc.prefetch("sess_1", "hello")
        assert result.memories == ()
        # 빌트인 스킬은 항상 주입 후보다 — 사용자 스킬이 없음을 확인한다.
        assert all(s.skill_id == "business-planning" for s in result.skills)

    def test_prefetch_includes_facts_after_add(self):
        MemoryStore(self._db).add("fact", "Project uses pytest")
        result = self._svc.prefetch("sess_1", "testing")
        assert len(result.memories) >= 1
        assert any("pytest" in m.content for m in result.memories)

    def test_prefetch_system_prompt_contains_fact_block(self):
        MemoryStore(self._db).add("fact", "Project language is Python")
        result = self._svc.prefetch("sess_1", "code")
        assert "Known facts" in result.system_prompt_additions
        assert "Python" in result.system_prompt_additions

    def test_prefetch_deduplicates_recent_and_fts(self):
        """A memory matching both recent-N and FTS-K appears only once."""
        store = MemoryStore(self._db)
        store.add("fact", "Project uses pytest for all tests")
        result = self._svc.prefetch("sess_1", "pytest")
        ids = [m.id for m in result.memories]
        assert len(ids) == len(set(ids)), "duplicate memory ids in prefetch result"

    def test_prefetch_fts_matches_user_message_keyword(self):
        """FTS retrieval: keyword in the message finds a matching memory."""
        store = MemoryStore(self._db)
        store.add("fact", "SQLite WAL mode is enabled")
        store.add("fact", "Project uses pytest")
        # query with "SQLite" keyword should pull the first fact via FTS
        result = self._svc.prefetch("sess_1", "SQLite database")
        assert any("SQLite" in m.content for m in result.memories)
