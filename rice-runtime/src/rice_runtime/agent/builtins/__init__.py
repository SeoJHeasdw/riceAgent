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
"""

from __future__ import annotations

from ...core.paths import RicePaths
from ...core.types import ToolSpec
from ..tool_dispatch import BuiltinTool
from . import file_tools


def build_builtin_tools(paths: RicePaths) -> tuple[tuple[ToolSpec, ...], dict[str, BuiltinTool]]:
    """Assemble every builtin tool. New builtin modules register here (one place)."""
    specs: list[ToolSpec] = []
    handlers: dict[str, BuiltinTool] = {}
    for spec, handler in file_tools.tools(paths):
        specs.append(spec)
        handlers[spec.name] = handler
    return tuple(specs), handlers
