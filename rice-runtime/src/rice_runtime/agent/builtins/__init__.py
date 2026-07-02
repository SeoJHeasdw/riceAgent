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
  file_tools    — read_file, write_file, search_files (+ xlsx/docx/ipynb extraction)
  terminal_tool — terminal (shell command execution; requires_approval=True)
  vision_tools  — vision_analyze (image download + base64; requires vision provider)
  skill_tools   — propose_skill (미드턴 스킬 승격 제안, Self-Improvement v3 —
                  learning 서비스가 주입됐을 때만 등록된다)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core.paths import RicePaths
from ...core.types import ToolSpec
from ..tool_dispatch import BuiltinTool
from . import file_tools, skill_tools, terminal_tool, vision_tools

if TYPE_CHECKING:
    from ...learning.interfaces import ILearningService


def build_builtin_tools(
    paths: RicePaths, learning: "ILearningService | None" = None
) -> tuple[tuple[ToolSpec, ...], dict[str, BuiltinTool]]:
    """Assemble every builtin tool. New builtin modules register here (one place).

    `learning`은 propose_skill(미드턴 승격 제안)의 스테이징 대상 — None이면(기존
    테스트/독립 사용) 그 도구만 빠지고 나머지는 동일하다."""
    specs: list[ToolSpec] = []
    handlers: dict[str, BuiltinTool] = {}
    for module in (file_tools, terminal_tool, vision_tools):
        for spec, handler in module.tools(paths):
            specs.append(spec)
            handlers[spec.name] = handler
    if learning is not None:
        for spec, handler in skill_tools.tools(learning):
            specs.append(spec)
            handlers[spec.name] = handler
    return tuple(specs), handlers
