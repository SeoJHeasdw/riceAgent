"""Builtin sub-agent delegation tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core.types import ToolSpec
from ..interfaces import TurnView
from ..tool_dispatch import BuiltinTool

if TYPE_CHECKING:
    from ..subagents import SubAgentRunner


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
        )
        return result.to_dict()

    delegate_subagent.needs_session = True  # type: ignore[attr-defined]
    # 진행 중 턴의 라이브 컨텍스트(이번 턴의 도구 관찰 + 활성 계획) — transcript는
    # 턴 종료에야 영속화되므로 이 마커 없이는 워커가 이번 턴을 못 본다.
    delegate_subagent.needs_turn_view = True  # type: ignore[attr-defined]

    spec = ToolSpec(
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
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional whitelist narrowing the sub-agent's tools for this task "
                        "(e.g. ['fetch_url'] for web-only research). Omit for the full set."
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
    return [(spec, delegate_subagent)]
