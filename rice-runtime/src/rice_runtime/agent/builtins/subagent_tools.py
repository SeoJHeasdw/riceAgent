"""Builtin sub-agent delegation tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core.types import ToolSpec
from ..interfaces import TurnView
from ..tool_dispatch import BuiltinTool

if TYPE_CHECKING:
    from ..subagents import SubAgentRunner

# 스펙에 광고하는 페르소나 수/설명 길이 상한 — 도구 설명은 매 턴 프롬프트에
# 실리므로 bounded. 초과분은 이름만이라도 워커 결과(open_questions)로 발견된다.
_PERSONA_LIST_MAX = 8
_PERSONA_DESC_MAX = 110


def _persona_line(name: str, description: str) -> str:
    """페르소나 한 줄 광고 — 설명은 첫 줄/문장으로 줄인다(Claude Code 형식의
    긴 트리거 예시 블록을 도구 스펙에 싣지 않는다)."""
    first = " ".join(description.split())
    for stop in (". ", "! ", "? "):
        cut = first.find(stop)
        if cut != -1:
            first = first[: cut + 1]
            break
    if len(first) > _PERSONA_DESC_MAX:
        first = first[:_PERSONA_DESC_MAX].rstrip() + "…"
    return f"'{name}' — {first}" if first else f"'{name}'"


def delegate_spec(runner: "SubAgentRunner") -> ToolSpec:
    """delegate_subagent 의 ToolSpec — 호출 시점의 활성 플러그인 페르소나를
    `agent` 파라미터 설명에 광고한다. Orchestrator 의 builtin 카탈로그가 매 턴
    이 함수를 다시 불러(composition root 의 라이브 카탈로그 클로저) 플러그인
    설치/활성 토글이 재시작 없이 반영된다."""
    personas = runner.available_personas()
    if personas:
        lines = "; ".join(_persona_line(p.name, p.description) for p in personas[:_PERSONA_LIST_MAX])
        agent_desc = (
            "Optional specialist persona for the worker, provided by installed "
            f"plugins. Available: {lines}. Pass the exact name; omit for the "
            "generic research worker."
        )
    else:
        agent_desc = (
            "Optional specialist persona name from installed plugins' agents. "
            "None are currently available — omit this."
        )
    return ToolSpec(
        name="delegate_subagent",
        description=(
            "Delegate a focused research, analysis, or drafting task to a parallel "
            "sub-agent with its OWN isolated context. The sub-agent can read workspace "
            "files, list directories, search file contents, fetch web pages, load saved "
            "skills, and save long outputs (drafts/tables/extractions) as scratch files "
            "— delegate file/source/document analysis instead of reading many files "
            "yourself, and keep your own context clean. Use multiple calls in one round "
            "for independent workstreams; then synthesize the structured results "
            "(findings, evidence, confidence, open_questions) in your next answer, "
            "weighing low-confidence findings accordingly. `artifacts` in the result "
            "lists files the sub-agent saved — read them with read_file when you need "
            "the full content."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The focused task for the sub-agent."},
                "context": {"type": "string", "description": "Relevant facts, snippets, or constraints to give the sub-agent."},
                "expected_output": {"type": "string", "description": "Preferred report shape or acceptance criteria."},
                "max_chars": {"type": "integer", "description": "Maximum characters to return; default 8000."},
                "agent": {"type": "string", "description": agent_desc},
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional whitelist narrowing the sub-agent's tools for this task "
                        "(e.g. ['web_search', 'fetch_url'] for web-only research). Omit for the full set."
                    ),
                },
                "wait_for_previous": {
                    "type": "boolean",
                    "description": "Set true only if this sub-agent depends on an earlier tool result.",
                },
            },
            "required": ["task"],
        },
        source="builtin",
    )


def tools(runner: "SubAgentRunner") -> list[tuple[ToolSpec, BuiltinTool]]:
    async def delegate_subagent(
        args: dict, session_id: str = "", turn_view: TurnView | None = None
    ) -> object:
        raw_tools = args.get("tools")
        tool_names = tuple(str(t) for t in raw_tools) if isinstance(raw_tools, list) else ()
        result = await runner.run(
            session_id=session_id,
            task=str(args.get("task") or ""),
            context=str(args.get("context") or ""),
            expected_output=str(args.get("expected_output") or ""),
            max_chars=int(args.get("max_chars") or 8_000),
            turn_view=turn_view,
            tool_names=tool_names,
            web_search_enabled=turn_view.web_search_enabled if turn_view is not None else True,
            agent=str(args.get("agent") or ""),
        )
        return result.to_dict()

    delegate_subagent.needs_session = True  # type: ignore[attr-defined]
    # 진행 중 턴의 라이브 컨텍스트(이번 턴의 도구 관찰 + 활성 계획) — transcript는
    # 턴 종료에야 영속화되므로 이 마커 없이는 워커가 이번 턴을 못 본다.
    delegate_subagent.needs_turn_view = True  # type: ignore[attr-defined]

    return [(delegate_spec(runner), delegate_subagent)]
