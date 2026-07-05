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

WHY: 스킬은 파일시스템에 사는 첫-클래스 아티팩트다. 마켓 artifact와 동일 포맷이라
plugin 제공 스킬과 호환된다 (CONTRACTS §2.2). SQLite 에 넣지 않는 이유는 사용자가
직접 편집할 수 있게 하기 위함이다 (RUNTIME_DESIGN §3).

frontmatter 파싱에 PyYAML을 사용한다. PyYAML이 없으면 최소 파서(정규식)로 폴백한다.
의존성은 optional로 선언되어 있으므로 지연 import로 처리한다 (CONVENTIONS §2).
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...core.errors import NotFoundError, ValidationError
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


# ── frontmatter 파싱 ──────────────────────────────────────────────────────────

_FM_DELIM = re.compile(r"^---\s*$", re.MULTILINE)


def _parse_yaml(text: str) -> dict[str, Any]:
    """YAML 문자열 → dict. PyYAML 없으면 최소 파서로 폴백."""
    try:
        import yaml  # optional dep; lazy import (CONVENTIONS §2)
        return yaml.safe_load(text) or {}
    except ImportError:
        return _minimal_yaml_parse(text)


def _dump_yaml(data: dict[str, Any]) -> str:
    """dict → YAML 문자열. PyYAML 없으면 최소 직렬화로 폴백."""
    try:
        import yaml
        return yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    except ImportError:
        return _minimal_yaml_dump(data)


def _minimal_yaml_parse(text: str) -> dict[str, Any]:
    """PyYAML 없이 쓸 수 있는 최소 파서. key: value 형식만 처리한다.
    NOTE(agent): 중첩 YAML, 멀티라인, 앵커 등은 처리 불가. MVP에서 frontmatter는
    단순 평탄 구조만 쓰므로 충분하다고 판단 (CONVENTIONS §7 fail-closed).
    """
    result: dict[str, Any] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        # 리스트 형식: [a, b, c]
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1]
            result[key] = [v.strip().strip("'\"") for v in inner.split(",") if v.strip()]
        elif val.isdigit():
            result[key] = int(val)
        elif val.lower() in ("true", "false"):
            result[key] = val.lower() == "true"
        elif val in ("null", "~", ""):
            result[key] = None
        else:
            result[key] = val.strip("'\"")
    return result


def _minimal_yaml_dump(data: dict[str, Any]) -> str:
    """PyYAML 없이 쓸 수 있는 최소 직렬화."""
    lines = []
    for k, v in data.items():
        if v is None:
            lines.append(f"{k}: null")
        elif isinstance(v, list):
            items = ", ".join(str(i) for i in v)
            lines.append(f"{k}: [{items}]")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        else:
            lines.append(f"{k}: {v}")
    return "\n".join(lines) + "\n"


def parse_skill_md(text: str) -> SkillData:
    """SKILL.md 전체 텍스트 → SkillData.

    첫 번째 ---...--- 블록을 frontmatter로 인식한다. 없으면 ValidationError.
    """
    parts = _FM_DELIM.split(text, maxsplit=2)
    if len(parts) < 3:
        raise ValidationError("SKILL.md must start with YAML frontmatter delimited by ---")
    # parts[0] = '' (frontmatter 앞), parts[1] = frontmatter, parts[2] = body
    fm_raw, body = parts[1], parts[2]
    fm = _parse_yaml(fm_raw)

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
    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

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
    )


def serialize_skill_md(skill: SkillData) -> str:
    """SkillData → SKILL.md 전체 텍스트."""
    fm: dict[str, Any] = {
        "name": skill.name,
        "version": skill.version,
        "description": skill.description,
        "state": skill.state,
    }
    if skill.tags:
        fm["tags"] = skill.tags
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
