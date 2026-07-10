"""Builtin tools — in-process tools the agent can call directly (no connector/
plugin needed). Ported in spirit from the Hermes tool system
(HERMES_TOOL_SYSTEM_REFERENCE.md) but adapted to RICE's seam:

  Hermes: auto-discovered `registry.register(...)`, `(args, **kw) -> json str`
  RICE:   composition_root assembles `(specs, handlers)` explicitly (invariant 2),
          handlers are `async (dict) -> object`, risky tools set
          `requires_approval=True` to ride the shared HITL gate.

`build_builtin_tools(paths)` returns the catalog (ToolSpecs advertised to the LLM)
and the name→handler map (`BuiltinTool`), which the composition root injects into
the Orchestrator + ToolDispatcher.

Registered builtin modules (add new ones here — one place):
  file_tools    — read_file, write_file, edit_file, list_dir, search_files
                  (+ xlsx/docx/ipynb/pptx extraction)
  office        — create_presentation/edit_presentation (.pptx 생성·슬라이드 단위
                  편집) + create_workbook (.xlsx 생성, 수식 로컬 평가 포함) +
                  create_document (.docx); 전부 requires_approval=True
  terminal_tool — terminal (shell command execution; requires_approval=True)
  web_tools     — fetch_url (public web page → readable text; SSRF-guarded)
  vision_tools  — vision_analyze (이미지를 도구 결과에 첨부 → provider 이미지
                  블록으로 모델이 직접 본다; 비전 모델 필요)
  subagent_tools — delegate_subagent (parallel focused worker threads)
  skill_tools   — skill_search + skill_view (스킬 인덱스 검색 · 전문 온디맨드
                  로딩) + propose_skill (미드턴 스킬 승격 제안); learning 서비스가
                  주입됐을 때만 등록된다

`build_subagent_toolset(paths, learning=None)`는 sub-agent 워커에게 주는 격리
부분집합이다 — read-only(read_file/list_dir/search_files/fetch_url)에, learning이
배선되면 스킬 온디맨드 로딩(skill_search/skill_view)이 더해진다. 일반 쓰기·셸·
재위임은 뺀다(워커는 승인 게이트 밖에서 돈다); 산출물 쓰기는 별도의 thread 격리
`write_scratch`(file_tools.scratch_tool_factory)가 담당한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core.paths import RicePaths
from ...core.types import ToolSpec
from ..tool_dispatch import BuiltinTool
from . import file_tools, office, skill_tools, subagent_tools, terminal_tool, vision_tools, web_tools
from .file_tools import scratch_tool_factory  # re-export — composition root가 러너에 주입  # noqa: F401

if TYPE_CHECKING:
    from ...learning.interfaces import ILearningService
    from ..subagents import SubAgentRunner

_SUBAGENT_TOOL_NAMES = ("read_file", "list_dir", "search_files", "fetch_url")
# learning 배선 시 워커에 추가되는 스킬 도구 — propose_skill은 제외한다(사용자
# 가시 효과를 스테이징하므로 메인 스트림의 카드 흐름 밖에서 돌면 안 된다).
_SUBAGENT_SKILL_TOOLS = ("skill_search", "skill_view")


def build_builtin_tools(
    paths: RicePaths,
    learning: "ILearningService | None" = None,
    subagents: "SubAgentRunner | None" = None,
) -> tuple[tuple[ToolSpec, ...], dict[str, BuiltinTool]]:
    """Assemble every builtin tool. New builtin modules register here (one place).

    `learning`은 propose_skill(미드턴 승격 제안)의 스테이징 대상 — None이면(기존
    테스트/독립 사용) 그 도구만 빠지고 나머지는 동일하다."""
    specs: list[ToolSpec] = []
    handlers: dict[str, BuiltinTool] = {}
    for module in (file_tools, office, terminal_tool, vision_tools):
        for spec, handler in module.tools(paths):
            specs.append(spec)
            handlers[spec.name] = handler
    for spec, handler in web_tools.tools():
        specs.append(spec)
        handlers[spec.name] = handler
    if subagents is not None:
        for spec, handler in subagent_tools.tools(subagents):
            specs.append(spec)
            handlers[spec.name] = handler
    if learning is not None:
        for spec, handler in skill_tools.tools(learning):
            specs.append(spec)
            handlers[spec.name] = handler
    return tuple(specs), handlers


def build_subagent_toolset(
    paths: RicePaths, learning: "ILearningService | None" = None
) -> tuple[tuple[ToolSpec, BuiltinTool], ...]:
    """Sub-agent 워커의 격리 도구 목록 — 메인 카탈로그와 같은 핸들러 구현을
    재사용하되 부작용 있는 도구(write/edit/terminal/…)와 재위임은 제외한다.
    `learning`이 있으면 스킬 온디맨드 로딩(skill_search/skill_view)을 더한다 —
    메인 루프의 하이브리드 스킬 주입과 같은 핸들러를 워커도 쓴다."""
    candidates = list(file_tools.tools(paths)) + list(web_tools.tools())
    kept = [(spec, handler) for spec, handler in candidates if spec.name in _SUBAGENT_TOOL_NAMES]
    if learning is not None:
        kept.extend(
            (spec, handler)
            for spec, handler in skill_tools.tools(learning)
            if spec.name in _SUBAGENT_SKILL_TOOLS
        )
    return tuple(kept)
