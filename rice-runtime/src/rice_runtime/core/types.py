"""Shared domain contracts (DTOs) for rice-runtime.

WHY THIS FILE EXISTS
--------------------
The core invariant (RICE_RUNTIME_DIRECTORY.md §5) is: **modules never import each
other directly — only via `interfaces.py` Protocols**. But Protocols still need to
*name* the data that crosses a seam (a tool call, a turn context, an SSE event).

`core/` is the one place every module is allowed to import (it is "공용 인프라 —
모듈 간 직접 import 금지선" / shared infra that sits *below* the no-import line).
So the shared vocabulary lives here. The dependency graph stays a DAG:

    agent ─┐
    learning ─┤
    connector ─┼──> core.types  (leaf, depends on nothing in rice_runtime)
    execution ─┤
    server ────┘

Nothing here knows about FastAPI, SQLite, or any concrete module. These are plain
frozen dataclasses so they are cheap to construct, easy to test, and safe to pass
across the (future) process boundary if a module is ever split out.

WIRE ALIGNMENT
--------------
The SSE event payloads below mirror `rice-app/src/lib/types.ts` (the frontend froze
the wire contract first) and `contracts/sse-events.schema.json`. Keep all three in
lockstep; that triple is the app↔runtime contract (RICE_CONTRACTS.md §1.3).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


# ─────────────────────────────────────────────────────────────────────────────
# Identity / time helpers (kept here so every module mints ids the same way)
# ─────────────────────────────────────────────────────────────────────────────
def new_id(prefix: str = "") -> str:
    """A short, sortable-enough unique id. `prefix` aids log/debug readability."""
    raw = uuid.uuid4().hex[:12]
    return f"{prefix}_{raw}" if prefix else raw


def now_ms() -> int:
    return int(time.time() * 1000)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ─────────────────────────────────────────────────────────────────────────────
# Conversation primitives
# ─────────────────────────────────────────────────────────────────────────────
Role = Literal["user", "assistant", "system", "tool"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str
    id: str = field(default_factory=lambda: new_id("msg"))
    ts: int = field(default_factory=now_ms)
    # Tool-call bookkeeping for assistant/tool turns (provider-normalized).
    tool_calls: tuple[ToolCall, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolSpec:
    """A tool advertised to the LLM. Provider adapters render this into their own
    function-calling schema (Claude `tool_use`, Ollama function-calling, …)."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for the arguments object
    # Where the tool came from — drives dispatch routing + permission checks.
    # "bridge" = tool_search/tool_describe/tool_call (RUNTIME_DESIGN §11 progressive
    # disclosure) — folded-tool catalog lookup + a recursive dispatch, not a plain callable.
    source: Literal["builtin", "connector", "plugin", "bridge"] = "builtin"
    # For connector/plugin tools: the owning instance id (connector_id / plugin_id).
    owner_id: str | None = None
    # Whether a successful call needs HITL approval (RUNTIME_DESIGN §5).
    requires_approval: bool = False


@dataclass(frozen=True)
class ToolCall:
    """A request to run a tool, normalized across providers (RUNTIME_DESIGN §6)."""

    tool_name: str
    arguments: dict[str, Any]
    id: str = field(default_factory=lambda: new_id("call"))


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    ok: bool
    result: Any = None
    error: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Turn context — what learning prefetch produces and the agent loop consumes
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class InjectedSkill:
    """An active skill injected into the system prompt (LEARNING_LOOP §7,
    inject-all-active-under-budget). `body` is the SKILL.md content."""

    skill_id: str
    name: str
    version: int
    body: str


@dataclass(frozen=True)
class MemoryItem:
    kind: Literal["fact", "preference"]
    content: str
    id: str = field(default_factory=lambda: new_id("mem"))


@dataclass(frozen=True)
class TurnContext:
    """The assembled context for one agent turn — the output of
    `ILearningService.prefetch` plus the tool catalog gathered by the orchestrator.

    This is the single value the agent loop needs to start: it is provider- and
    storage-agnostic, so the loop can be tested in isolation."""

    session_id: str
    system_prompt: str
    history: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]
    injected_skills: tuple[InjectedSkill, ...] = ()
    injected_memories: tuple[MemoryItem, ...] = ()
    max_iterations: int = 25  # RUNTIME_DESIGN §2 default


@dataclass(frozen=True)
class TurnOutcome:
    """What happened in a turn — handed to `ILearningService.observe` so the
    learning loop can decide whether to create a skill, write memory, or propose a
    patch (LEARNING_LOOP §1). Kept minimal; learning derives the rest from history."""

    session_id: str
    final_message: str
    history: tuple[Message, ...]
    tool_calls_made: int
    injected_skill_ids: tuple[str, ...]
    aborted: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# HITL approval (shared by tool-exec approval AND skill-patch approval) — the
# two control flows use one mechanism (RUNTIME_DESIGN §5.1).
# ─────────────────────────────────────────────────────────────────────────────
ApprovalKind = Literal["tool_approval", "skill_patch"]
ApprovalDecision = Literal["approve", "reject", "timeout", "cancelled"]


@dataclass(frozen=True)
class ApprovalRequest:
    kind: ApprovalKind
    payload: dict[str, Any]  # the control-event data (sans approval_id)
    approval_id: str = field(default_factory=lambda: new_id("appr"))


@dataclass(frozen=True)
class ApprovalResponse:
    approval_id: str
    decision: ApprovalDecision
    # For skill_patch "approve with edits" — the edited full SKILL.md body.
    edited_body: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# SSE events — the 9 stream events + 2 control events (RICE_CONTRACTS §1.3).
# `type` strings + `data` shapes mirror rice-app/src/lib/types.ts exactly.
# Emitting is done via server/sse/emitter.py; the orchestrator yields these.
# ─────────────────────────────────────────────────────────────────────────────
class EventType(str, Enum):
    # stream events
    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    MESSAGE = "message"
    SKILL_CREATED = "skill_created"
    MEMORY_WRITTEN = "memory_written"
    SKILL_PATCHED = "skill_patched"
    DONE = "done"
    ERROR = "error"
    # control events (carry approval_id — out-of-band HITL, CONTRACTS §1.2)
    TOOL_APPROVAL_REQUIRED = "tool_approval_required"
    SKILL_PATCH_PROPOSED = "skill_patch_proposed"


@dataclass(frozen=True)
class SSEEvent:
    """A single server-sent event. `data` must be JSON-serializable and match the
    shape for `type` in sse-events.schema.json / types.ts."""

    type: EventType
    data: dict[str, Any] = field(default_factory=dict)

    def to_frame(self) -> str:
        """Render as an SSE wire frame: `event: <type>\\n data: <json>\\n\\n`.
        The frontend client (lib/sse.ts) accepts both this and the bare form."""
        import json

        return f"event: {self.type.value}\ndata: {json.dumps(self.data, ensure_ascii=False)}\n\n"


# Convenience constructors so emitters/orchestrator don't hand-build dicts that
# could drift from the schema. Each mirrors the matching *EventData in types.ts.
def ev_thinking(text: str) -> SSEEvent:
    return SSEEvent(EventType.THINKING, {"text": text})


def ev_tool_call(call: ToolCall) -> SSEEvent:
    return SSEEvent(
        EventType.TOOL_CALL,
        {"tool_call_id": call.id, "tool_name": call.tool_name, "arguments": call.arguments},
    )


def ev_tool_result(res: ToolResult) -> SSEEvent:
    return SSEEvent(
        EventType.TOOL_RESULT,
        {"tool_call_id": res.tool_call_id, "ok": res.ok, "result": res.result, "error": res.error},
    )


def ev_message(delta: str | None = None, content: str | None = None) -> SSEEvent:
    data: dict[str, Any] = {}
    if delta is not None:
        data["delta"] = delta
    if content is not None:
        data["content"] = content
    return SSEEvent(EventType.MESSAGE, data)


def ev_skill_created(skill_id: str, title: str, version: int) -> SSEEvent:
    return SSEEvent(EventType.SKILL_CREATED, {"skill_id": skill_id, "title": title, "version": version})


def ev_memory_written(memory_type: Literal["fact", "preference"], content: str) -> SSEEvent:
    return SSEEvent(EventType.MEMORY_WRITTEN, {"memory_type": memory_type, "content": content})


def ev_skill_patched(skill_id: str, version: int) -> SSEEvent:
    return SSEEvent(EventType.SKILL_PATCHED, {"skill_id": skill_id, "version": version})


def ev_done() -> SSEEvent:
    return SSEEvent(EventType.DONE, {})


def ev_error(error_code: str, message: str) -> SSEEvent:
    return SSEEvent(EventType.ERROR, {"error_code": error_code, "message": message})


def ev_tool_approval_required(approval_id: str, tool_name: str, arguments: dict[str, Any], reason: str | None = None) -> SSEEvent:
    data: dict[str, Any] = {"approval_id": approval_id, "tool_name": tool_name, "arguments": arguments}
    if reason is not None:
        data["reason"] = reason
    return SSEEvent(EventType.TOOL_APPROVAL_REQUIRED, data)


def ev_skill_patch_proposed(
    approval_id: str,
    skill_id: str,
    changelog: str,
    diff: dict[str, Any],
    trigger: str,
    from_version: int | None = None,
    to_version: int | None = None,
) -> SSEEvent:
    data: dict[str, Any] = {
        "approval_id": approval_id,
        "skill_id": skill_id,
        "changelog": changelog,
        "diff": diff,
        "trigger": trigger,
    }
    if from_version is not None:
        data["from_version"] = from_version
    if to_version is not None:
        data["to_version"] = to_version
    return SSEEvent(EventType.SKILL_PATCH_PROPOSED, data)
