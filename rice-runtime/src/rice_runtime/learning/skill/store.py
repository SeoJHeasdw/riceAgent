"""skill/store.py — SKILL.md 파일 시스템 스토어.

책임(single): ~/.rice/skills/<name>.md 와 그 히스토리(.history/v<N>.md)를 읽고
쓴다. 파싱/직렬화, frontmatter 보존, 버전 히스토리 목록이 여기 있다.

SKILL.md 포맷 (LEARNING_LOOP §2):
  ---
  name: kebab-case-name      # 필수, 고유
  version: 1                 # 필수, 정수
  description: 한 줄 설명    # 필수
  state: active              # 필수: active|stale|archived
  tags: [tag1, tag2]         # 선택
  source: session:<id>       # 선택
  created_at: ISO8601        # 선택
  updated_at: ISO8601        # 선택
  ---
  본문 Markdown

디렉토리 레이아웃 (RicePaths §paths.skill_history):
  ~/.rice/skills/<name>.md               ← 현재 버전
  ~/.rice/skills/<name>/.history/v1.md   ← 버전 히스토리
  ~/.rice/skills/<name>/references/...   ← 선택적 번들 파일
  ~/.rice/skills/<name>/scripts/...      ← 선택적 번들 파일

WHY: 스킬은 파일시스템에 사는 첫-클래스 아티팩트다. 마켓 artifact와 동일 포맷이라
plugin 제공 스킬과 호환된다 (CONTRACTS §2.2). SQLite 에 넣지 않는 이유는 사용자가
직접 편집할 수 있게 하기 위함이다 (RUNTIME_DESIGN §3).

frontmatter 파싱/직렬화 메커니즘은 core/frontmatter.py 가 소유한다(2026-07-19
승격 — execution/plugin/loader.py 와의 의도적 파서 중복 해소). 여기는 SKILL.md
필드 의미론(name 필수, state enum, 타입 강제)만 남는다.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.errors import NotFoundError, ValidationError
from ...core.frontmatter import dump_yaml as _dump_yaml
from ...core.frontmatter import split_frontmatter
from ...core.paths import RicePaths
from ...core.types import now_iso


# ── 내부 스킬 표현 (파일 I/O 전용 — 외부에는 SkillSummary/SkillDetail로 변환) ──
@dataclass
class SkillData:
    """파일에서 파싱한 스킬 데이터. 변경 가능해야 하므로 frozen=False."""

    name: str
    version: int
    description: str
    state: str  # active | stale | archived
    body: str
    tags: list[str]
    source: str | None
    created_at: str | None
    updated_at: str | None
    # Phase B(curator 통합): 병합으로 archived됐을 때 흡수한 umbrella skill 이름.
    # 순수 정리(prune, 병합 대상 없음)로 archived됐으면 None. 기존 파서가 미지
    # frontmatter 키를 관대하게 처리하므로 순수 추가(마이그레이션 불필요).
    absorbed_into: str | None = None
    # **선언된(declared) 도구** — manifest/마켓 메타데이터/작성자가 frontmatter 로
    # 명시한 도구 목록. 분석 사이드카의 mentioned(본문 감지)·usage 의 observed
    # (실행 로그)와 출처가 다른 제3의 단서 소스다 (analysis.py 계약). 설명 전용 —
    # 권한/실행 정책과 무관하다. 선택 필드라 기존 파일과 하위호환.
    tools: list[str] = field(default_factory=list)
    # UI "비활성" 상태 영속화: state=active이지만 사용자가 주입을 꺼둔 스킬.
    # True(기본) = 주입 허용, False = 주입 중단. 기존 파일과 하위호환(없으면 True).
    enabled: bool = True


# ── frontmatter 파싱 (메커니즘은 core/frontmatter.py — 여기는 필드 의미론) ────


def parse_skill_md(text: str) -> SkillData:
    """SKILL.md 전체 텍스트 → SkillData.

    첫 번째 ---...--- 블록을 frontmatter로 인식한다. 없으면 ValidationError.
    """
    try:
        parsed = split_frontmatter(text)
    except Exception as exc:  # PyYAML parser errors → stable domain error
        raise ValidationError(f"SKILL.md frontmatter is not valid YAML: {exc}") from exc
    if parsed is None:
        raise ValidationError("SKILL.md must start with YAML frontmatter delimited by ---")
    fm, body = parsed

    name = fm.get("name")
    if not name or not isinstance(name, str):
        raise ValidationError("SKILL.md frontmatter missing required field: name")
    version = fm.get("version", 1)
    if not isinstance(version, int):
        try:
            version = int(version)
        except (ValueError, TypeError):
            raise ValidationError(f"SKILL.md version must be an integer, got {version!r}")
    description = fm.get("description", "")
    state = fm.get("state", "active")
    if state not in ("active", "stale", "archived"):
        raise ValidationError(f"Unknown skill state {state!r}")
    enabled_raw = fm.get("enabled", True)
    enabled = bool(enabled_raw) if not isinstance(enabled_raw, bool) else enabled_raw
    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    tools = fm.get("tools") or []
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",") if t.strip()]

    return SkillData(
        name=str(name),
        version=version,
        description=str(description),
        state=str(state),
        body=body.lstrip("\n"),
        tags=list(tags),
        source=fm.get("source"),
        created_at=fm.get("created_at"),
        updated_at=fm.get("updated_at"),
        absorbed_into=fm.get("absorbed_into"),
        tools=[str(t) for t in tools],
        enabled=enabled,
    )


def serialize_skill_md(skill: SkillData) -> str:
    """SkillData → SKILL.md 전체 텍스트."""
    fm: dict[str, Any] = {
        "name": skill.name,
        "version": skill.version,
        "description": skill.description,
        "state": skill.state,
    }
    if not skill.enabled:
        fm["enabled"] = False
    if skill.tags:
        fm["tags"] = skill.tags
    if skill.tools:
        fm["tools"] = skill.tools
    if skill.source is not None:
        fm["source"] = skill.source
    if skill.created_at is not None:
        fm["created_at"] = skill.created_at
    if skill.updated_at is not None:
        fm["updated_at"] = skill.updated_at
    if skill.absorbed_into is not None:
        fm["absorbed_into"] = skill.absorbed_into

    return f"---\n{_dump_yaml(fm)}---\n{skill.body}"


# ── SkillStore ────────────────────────────────────────────────────────────────

class SkillStore:
    """~/.rice/skills/ 위의 SKILL.md 파일 CRUD (LEARNING_LOOP §2·§3)."""

    def __init__(self, paths: RicePaths) -> None:
        self._paths = paths

    def _skill_path(self, name: str) -> Path:
        return self._paths.skills / f"{name}.md"

    def _history_dir(self, name: str) -> Path:
        return self._paths.skill_history(name)

    def _bundle_dir(self, name: str) -> Path:
        """Bundled references/scripts for a learned skill.

        The canonical definition stays at ``<name>.md`` for backwards-compatible
        injection, while repository files live under ``<name>/`` beside its
        hidden history directory. The API presents both as one virtual tree with
        ``SKILL.md`` at the root.
        """
        return self._paths.skills / name

    def _resource_path(self, name: str, rel_path: str) -> Path:
        rel = Path(rel_path)
        if rel.is_absolute() or not rel.parts or any(part in ("", ".", "..") for part in rel.parts):
            raise ValidationError(f"Invalid skill resource path: {rel_path!r}")
        if rel.parts[0] == ".history" or rel.name.lower() == "skill.md":
            raise ValidationError(f"Reserved skill resource path: {rel_path!r}")
        base = self._bundle_dir(name).resolve()
        target = (base / rel).resolve()
        if base not in target.parents:
            raise ValidationError(f"Skill resource path escapes bundle: {rel_path!r}")
        return target

    # ── 읽기 ─────────────────────────────────────────────────────────────────

    def read(self, name: str) -> SkillData:
        """이름으로 스킬을 읽는다. 없으면 NotFoundError."""
        path = self._skill_path(name)
        if not path.exists():
            raise NotFoundError(f"Skill {name!r} not found at {path}")
        return parse_skill_md(path.read_text(encoding="utf-8"))

    def list(self) -> list[SkillData]:
        """모든 스킬을 반환한다 (이름 정렬). 파싱 오류는 건너뛴다(fail-safe)."""
        skills_dir = self._paths.skills
        if not skills_dir.exists():
            return []
        result = []
        for path in sorted(skills_dir.glob("*.md")):
            try:
                result.append(parse_skill_md(path.read_text(encoding="utf-8")))
            except Exception:
                # 손상된 SKILL.md(사용자 직접 편집 등)는 무음으로 건너뛴다 — 한 파일
                # 오류가 전체 목록을 막지 않게(fail-safe). (Exception이 ValidationError 포함)
                pass
        return result

    def read_history(self, name: str) -> list[tuple[int, str, str]]:
        """(version, updated_at, body) 튜플 목록을 반환한다. 버전 오름차순."""
        hist_dir = self._history_dir(name)
        if not hist_dir.exists():
            return []
        result = []
        for path in sorted(hist_dir.glob("v*.md")):
            try:
                # 파일을 한 번만 읽어 파싱/반환에 재사용한다 — 두 번 읽으면 그 사이
                # 파일이 바뀔 때 버전과 본문이 어긋날 수 있다(버그#11).
                text = path.read_text(encoding="utf-8")
                data = parse_skill_md(text)
                updated = data.updated_at or data.created_at or ""
                result.append((data.version, updated, text))
            except Exception:
                pass
        return result

    def list_files(self, name: str) -> list[tuple[str, str]]:
        """Return the virtual repository tree: canonical SKILL.md + text resources."""
        skill = self.read(name)
        files: list[tuple[str, str]] = [("SKILL.md", serialize_skill_md(skill))]
        bundle = self._bundle_dir(name)
        if not bundle.is_dir():
            return files
        for path in sorted(bundle.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(bundle)
            if ".history" in rel.parts or any(part.startswith(".") for part in rel.parts):
                continue
            try:
                data = path.read_bytes()
                if b"\x00" in data[:1024]:
                    continue
                files.append((rel.as_posix(), data.decode("utf-8")))
            except (OSError, UnicodeDecodeError):
                continue
        return files

    def read_resource(self, name: str, rel_path: str) -> str:
        self.read(name)  # validate skill existence first
        path = self._resource_path(name, rel_path)
        if not path.is_file():
            raise NotFoundError(f"Resource {rel_path!r} not found for skill {name!r}")
        return path.read_text(encoding="utf-8")

    # ── 쓰기 ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _atomic_write_text(path: Path, text: str) -> None:
        """temp write → rename. 쓰기 도중 크래시가 나도 파일은 이전 전체본 아니면
        새 전체본이다 — write_text 는 truncate 후 쓰므로 반쪽 SKILL.md 를 남길 수
        있고, 그 반쪽은 다음 read()에서 파싱 오류(= 승인 적용 실패)가 된다."""
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def write(self, skill: SkillData) -> None:
        """스킬을 파일시스템에 원자적으로 직렬화한다. 디렉토리가 없으면 생성한다."""
        self._paths.skills.mkdir(parents=True, exist_ok=True)
        self._atomic_write_text(self._skill_path(skill.name), serialize_skill_md(skill))

    def write_resource(self, name: str, rel_path: str, content: str) -> None:
        """Atomically create or replace a UTF-8 file inside a skill bundle."""
        self.read(name)
        path = self._resource_path(name, rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_text(path, content)

    def archive_resources_to_history(self, name: str, version: int) -> None:
        """Snapshot non-hidden bundle files alongside a SKILL.md history revision."""
        bundle = self._bundle_dir(name)
        snapshot = self._history_dir(name) / f"v{version}.files"
        shutil.rmtree(snapshot, ignore_errors=True)
        # An empty directory is an intentional snapshot too: rolling back to a
        # pre-resource version must remove files added later.
        snapshot.mkdir(parents=True, exist_ok=True)
        if not bundle.is_dir():
            return
        for path in sorted(bundle.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(bundle)
            if ".history" in rel.parts or any(part.startswith(".") for part in rel.parts):
                continue
            dest = snapshot / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)

    def restore_resources_from_history(self, name: str, version: int) -> None:
        """Restore a resource snapshot if that historical revision has one."""
        snapshot = self._history_dir(name) / f"v{version}.files"
        if not snapshot.is_dir():
            return
        bundle = self._bundle_dir(name)
        for path in list(bundle.rglob("*")) if bundle.exists() else []:
            if path.is_file() and ".history" not in path.relative_to(bundle).parts:
                path.unlink()
        for path in sorted(snapshot.rglob("*")):
            if path.is_file():
                dest = bundle / path.relative_to(snapshot)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)

    def delete(self, name: str) -> None:
        """스킬 본문 파일과 히스토리 디렉토리를 삭제한다 (DELETE /skills/{id}).

        usage 사이드카(.usage.json)는 UsageStore.delete 가 담당한다.
        존재하지 않아도 조용히 통과(idempotent)."""
        self._skill_path(name).unlink(missing_ok=True)
        shutil.rmtree(self._paths.skills / name, ignore_errors=True)  # <name>/.history 포함

    def archive_to_history(self, name: str, version: int, full_text: str) -> None:
        """현재 버전을 .history/v<N>.md 로 보관한다 (apply_patch·rollback에서 호출).

        `full_text`는 frontmatter를 포함한 **SKILL.md 전체 텍스트**여야 한다(본문만이
        아니다). read_history/rollback이 이 파일을 parse_skill_md로 다시 파싱하므로
        frontmatter가 없으면 복원/조회가 깨진다. 모든 호출부가 전체 텍스트를 넘긴다."""
        hist_dir = self._history_dir(name)
        hist_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write_text(hist_dir / f"v{version}.md", full_text)
