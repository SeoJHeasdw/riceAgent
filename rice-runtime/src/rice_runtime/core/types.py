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
ApprovalMode = Literal["ask", "approve_for_me", "full_access"]


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
    # 웹 검색 토글이 꺼진 턴에서는 이 spec 자체를 LLM/서브에이전트 도구 표면에서
    # 제외한다. 이름 하드코딩 대신 선언이 capability를 소유한다.
    is_web_tool: bool = False
    # 같은 라운드의 다른 도구와 동시 실행해도 안전한가 — ToolScheduler의 병렬 배치
    # 판정 근거. False = 상태를 변경하거나 사용자 가시 효과를 스테이징하는 도구
    # (write_file/edit_file/terminal/propose_skill 등)로, 모델 호출 순서대로 직렬
    # 실행된다. 판정 기준이 스케줄러의 하드코딩 이름 목록이 아니라 도구 선언
    # 자신에게 있다(2026-07-10).
    parallel_safe: bool = True
    # 라우팅 전용 트리거 어휘 — 스킬의 tags와 같은 역할(2026-07-25). **모델에게는
    # 보이지 않는다**: provider 어댑터는 name/description/parameters만 직렬화하므로
    # 이 필드는 wire 에 실리지 않고 토큰을 쓰지 않는다. 오직 tool_dispatch 의
    # 결정론적 lexical 라우터(스포트라이트·tool_search)만 읽는다.
    #
    # WHY: 접힌 도구는 이름·설명의 영어 어휘로만 매칭되는데 그 텍스트는 대개
    # **벤더가 쓴 것**이라 한국어 트리거를 넣을 수 없다. 전역 ko→en intent
    # 테이블(core/lexical.py)은 범용 동사만 덮고 도메인어는 의도적으로 제외한다
    # ("per-skill/per-tool alias 를 테이블에 넣는 건 smell"). 그래서 도구가 자기
    # 트리거 어휘를 직접 들고 다니게 한다 — description 을 한국어로 오염시키면
    # 모델이 읽는 텍스트가 나빠지고 매 턴 토큰을 쓴다.
    keywords: tuple[str, ...] = ()


# keywords 는 플러그인 매니페스트·MCP 서버 응답 등 **신뢰할 수 없는 입력**에서 온다.
# 라우터 점수에만 쓰이지만(모델에 안 실림) 무제한 리스트가 매 턴 토큰화를 타면
# 라우팅 비용이 커지므로 개수·길이를 자른다.
MAX_TOOL_KEYWORDS = 12
MAX_TOOL_KEYWORD_LEN = 40


def coerce_tool_keywords(raw: object) -> tuple[str, ...]:
    """임의의 JSON 값 → 안전한 keywords 튜플. 리스트가 아니거나 항목이 문자열이
    아니면 조용히 버린다(fail-safe — 매니페스트 오타가 도구 로딩을 막지 않는다)."""
    if not isinstance(raw, (list, tuple)):
        return ()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        word = item.strip()[:MAX_TOOL_KEYWORD_LEN]
        if word:
            out.append(word)
        if len(out) >= MAX_TOOL_KEYWORDS:
            break
    return tuple(out)


@dataclass(frozen=True)
class ToolCall:
    """A request to run a tool, normalized across providers (RUNTIME_DESIGN §6)."""

    tool_name: str
    arguments: dict[str, Any]
    id: str = field(default_factory=lambda: new_id("call"))


# ── 이미지 첨부 계약 (2026-07-10, 멀티모달 provider seam) ─────────────────────
# 도구가 결과 dict에 `_images: [{"mime_type": str, "data_b64": str}]`를 실으면
# 디스패처가 분리해 `ToolResult.images`로 나르고, 루프가 tool Message의
# `metadata["images"]`에 부착한다(транscript metadata JSON에 그대로 영속 —
# 스키마 변경 없음). provider 어댑터는 이 metadata를 각자의 wire 이미지 블록으로
# 렌더한다(Claude tool_result image block / Ollama message.images / OpenAI-compat
# image_url 파트). base64가 텍스트 본문·SSE payload로 새지 않는 것이 계약의 핵심.
TOOL_RESULT_IMAGES_KEY = "_images"
IMAGES_METADATA_KEY = "images"


def split_image_attachments(result: Any) -> tuple[Any, tuple[dict[str, str], ...]]:
    """도구 결과에서 `_images` 첨부를 분리한다 → (본문 결과, 검증된 첨부 튜플).

    첨부가 없거나 dict가 아니면 결과를 그대로 돌려준다. 항목 검증은 관대하게 —
    mime_type/data_b64가 둘 다 비지 않은 dict만 남긴다(잘못된 항목이 턴을 깨면
    안 된다)."""
    if not isinstance(result, dict) or TOOL_RESULT_IMAGES_KEY not in result:
        return result, ()
    raw = result.get(TOOL_RESULT_IMAGES_KEY)
    images = tuple(
        {"mime_type": str(item["mime_type"]), "data_b64": str(item["data_b64"])}
        for item in (raw if isinstance(raw, list) else ())
        if isinstance(item, dict) and item.get("mime_type") and item.get("data_b64")
    )
    cleaned = {k: v for k, v in result.items() if k != TOOL_RESULT_IMAGES_KEY}
    return cleaned, images


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    ok: bool
    result: Any = None
    error: str | None = None
    # 도구가 첨부한 이미지(위 계약) — SSE payload(`ev_tool_result`)에는 싣지 않고
    # 루프가 tool Message metadata로 옮긴다. 빈 튜플 = 첨부 없음.
    images: tuple[dict[str, str], ...] = ()


# ─────────────────────────────────────────────────────────────────────────────
# Turn plan — plan-and-execute × ReAct (RICE_AGENT_EXECUTION.md)
#
# 복합 질의는 실행 전에 명시적 계획으로 승격된다: 플래너가 의도(goal)와 단계를
# 세우고, 루프는 매 반복 이 상태를 시스템 프롬프트에 렌더하며, 모델은
# `update_plan`으로 단계 완료/실패/수정을 보고한다(ReAct의 관찰→재평가를 계획
# 수준으로 끌어올린 것). 로컬 LLM이 메인 모델일 때 긴 멀티스텝 작업에서 길을
# 잃지 않게 하는 뼈대다 — 토큰 절약보다 수행 품질이 우선이라는 제품 결정.
# ─────────────────────────────────────────────────────────────────────────────
PlanStepStatus = Literal["pending", "in_progress", "done", "failed", "skipped"]
PlanStatus = Literal["active", "completed", "abandoned"]


@dataclass(frozen=True)
class PlanStep:
    """One unit of intended work. `id`(s1, s2, …)는 모델이 update_plan에서
    참조하는 안정 키; `note`는 진행하며 채워지는 결과 한 줄."""

    id: str
    title: str
    detail: str = ""
    status: PlanStepStatus = "pending"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "detail": self.detail,
            "status": self.status,
            "note": self.note,
        }


@dataclass(frozen=True)
class TurnPlan:
    """의도 해석(goal) + 단계들의 스냅샷. 불변 — 갱신은 PlanState(agent/planner.py)가
    새 revision의 TurnPlan을 만들어 교체한다. 스냅샷 전체가 plan_updated SSE와
    메시지 metadata("plan")로 실려 프론트 카드/리로드 복원이 같은 그림을 본다."""

    goal: str
    steps: tuple[PlanStep, ...]
    plan_id: str = field(default_factory=lambda: new_id("plan"))
    revision: int = 1
    status: PlanStatus = "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "status": self.status,
            "revision": self.revision,
            "steps": [s.to_dict() for s in self.steps],
        }


# ─────────────────────────────────────────────────────────────────────────────
# Turn context — what learning prefetch produces and the agent loop consumes
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class InjectedSkill:
    """An active skill injected into the system prompt (LEARNING_LOOP §7).
    `body` is the SKILL.md body (frontmatter stripped). `description`/`tags`
    exist so non-learning sources (plugin SKILL.md bundles) can ride the same
    relevance pipeline as learned skills — index rendering and lexical scoring
    both need them. `source` is provenance for audit ("why was this in the
    prompt"): "learned" (skill store / builtin) or "plugin".

    `resource_dir`/`resources`: plugin skills shipped in the standard
    `skills/<name>/SKILL.md` layout may bundle sibling files (references/,
    examples/, scripts/ — progressive disclosure). The loader lists them here so
    the `skill_resource` tool can serve them on demand — without this the body's
    "see references/x.md" pointers are dead: file tools are workspace-sandboxed
    and can never reach a plugin's install dir. Empty for flat/learned skills."""

    skill_id: str
    name: str
    version: int
    body: str
    description: str = ""
    tags: tuple[str, ...] = ()
    source: str = "learned"
    resource_dir: str = ""
    resources: tuple[str, ...] = ()


@dataclass(frozen=True)
class PluginAgent:
    """A specialist sub-agent persona shipped in a plugin's `agents/*.md`
    (2026-07-19 — commands/agents 소비). `body` is the markdown body after
    frontmatter and becomes the worker's system prompt when the main model
    delegates with `delegate_subagent(agent=<name>)`. `description` keeps the
    author's full trigger guidance (may be long — Claude Code style examples);
    enumeration surfaces (tool spec, UI) must shorten it themselves.

    InjectedSkill 과 같은 이유로 core 에 산다: 생산자(execution/plugin loader)와
    소비자(agent/subagents)가 다른 모듈이라, 직접 import 금지선을 넘지 않는
    공용 타입 바닥이 여기뿐이다."""

    agent_id: str  # "plugin:<plugin_id>:agent:<name>" — 충돌 없는 감사용 식별자
    name: str
    description: str
    body: str
    plugin_id: str = ""


@dataclass(frozen=True)
class MemoryItem:
    kind: Literal["fact", "preference"]
    content: str
    id: str = field(default_factory=lambda: new_id("mem"))
    # 출처/격리. status='quarantined'면 prefetch가 주입하지 않는다 —
    # 외부 콘텐츠(도구 결과)가 섞인 턴에서 나온 메모리는 사용자 승인 전까지 대기.
    status: Literal["active", "quarantined"] = "active"
    derived_from_external: bool = False
    source_session_id: str | None = None
    source_message_ids: tuple[str, ...] = ()


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
    # Exact prefetch snapshot, captured before the turn starts. IDs alone are
    # retained for compatibility; learning evidence prefers these pairs.
    injected_skill_versions: tuple[tuple[str, int], ...] = ()
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
# `plan_updated` (2026-07-04): plan-and-execute × ReAct의 계획 스냅샷 스트림.
# Emitting is done via server/sse/emitter.py; the orchestrator yields these.
#
# NOTE(agent): `skill_created`/`SKILL_CREATED` was retired 2026-07-02 (Phase A.1,
# Hermes learning_loop 이식). Skill creation is now unified into the
# `skill_patch_proposed`(from_version=0) HITL flow — a bare creation event with
# no approval gate contradicted RICE's own "스킬은 사용자 확인 후 저장" contract
# (RICE_CONTRACTS §4). Do not resurrect it; see PatchProposal's docstring.
# ─────────────────────────────────────────────────────────────────────────────
class EventType(str, Enum):
    # stream events
    THINKING = "thinking"
    PLAN_UPDATED = "plan_updated"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    MESSAGE = "message"
    MEMORY_WRITTEN = "memory_written"
    SKILL_PATCHED = "skill_patched"
    # 승인이 즉시 적용 대신 candidate 격리로 귀결됨 (LEARNING_LOOP §10.6,
    # candidate_eval_enabled). 순수 추가 이벤트 — 구형 클라이언트는 무시한다
    # (sse.ts 는 미지 타입을 드랍). 이후의 승격/폐기는 백그라운드에서 일어나므로
    # SSE 가 아니라 GET /skills/candidates·GET /learning/events 로 관찰한다.
    SKILL_CANDIDATE_STAGED = "skill_candidate_staged"
    # 백그라운드 셸 작업(§11) — 활성 스트림에서만 방출된다. task_started 는 spawn/
    # 승격 시점(항상 턴 안), task_completed 는 완료 드레인 시점(창 A: 턴 내 / 창 B:
    # 다음 턴 ASSEMBLE). 순수 추가 이벤트 — 구형 클라이언트는 미지 타입을 드랍한다
    # (sse.ts). 프론트 작업 카드는 Phase 2.
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
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


def ev_plan_updated(plan: TurnPlan) -> SSEEvent:
    """계획 생성·단계 전이·수정 때마다 전체 스냅샷을 흘린다 — 프론트는 델타 조립
    없이 plan_id로 카드를 통째로 교체한다(단순함이 정확성)."""
    return SSEEvent(EventType.PLAN_UPDATED, plan.to_dict())


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


def ev_memory_written(
    memory_type: Literal["fact", "preference"],
    content: str,
    *,
    status: str = "active",
    memory_id: str | None = None,
    derived_from_external: bool = False,
) -> SSEEvent:
    data: dict[str, Any] = {"memory_type": memory_type, "content": content, "status": status}
    if memory_id is not None:
        data["memory_id"] = memory_id
    if derived_from_external:
        data["derived_from_external"] = True
    return SSEEvent(EventType.MEMORY_WRITTEN, data)


def ev_skill_patched(skill_id: str, version: int) -> SSEEvent:
    return SSEEvent(EventType.SKILL_PATCHED, {"skill_id": skill_id, "version": version})


def ev_skill_candidate_staged(
    skill_id: str, candidate_id: str, baseline_version: int
) -> SSEEvent:
    return SSEEvent(
        EventType.SKILL_CANDIDATE_STAGED,
        {
            "skill_id": skill_id,
            "candidate_id": candidate_id,
            "baseline_version": baseline_version,
        },
    )


def ev_task_started(task_id: str, command: str) -> SSEEvent:
    """백그라운드 작업이 떴다(spawn 또는 auto-background 승격). 항상 턴 안에서
    방출된다 — 시작은 정의상 활성 스트림 안이다(§11.7)."""
    return SSEEvent(EventType.TASK_STARTED, {"task_id": task_id, "command": command})


def ev_task_completed(task_id: str, status: str, exit_code: int | None) -> SSEEvent:
    """백그라운드 작업이 끝났다 — 완료 드레인(창 A/B) 시점에 활성 스트림으로만
    방출된다. 유휴 중 완료는 스트림이 없어 이 이벤트가 없지만, 완료 관찰은 DB
    백업 큐로 다음 턴에 히스토리에 전달된다(§11.2)."""
    return SSEEvent(
        EventType.TASK_COMPLETED,
        {"task_id": task_id, "status": status, "exit_code": exit_code},
    )


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
    proposed_body: str | None = None,
    description: str | None = None,
    tags: tuple[str, ...] | None = None,
    new_description: str | None = None,
    new_tags: tuple[str, ...] | None = None,
    warnings: tuple[str, ...] = (),
    derived_from_external: bool = False,
    evidence_message_ids: tuple[str, ...] = (),
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
    if proposed_body is not None:
        # 견고성 개선(버그#0과 연계, 2026-07-02): 프론트가 diff.lines를 필터링해
        # proposedBody를 재구성하던 것을 서버가 준 원문으로 대체할 수 있게 한다 —
        # 재구성 로직은 remove 아닌 줄을 이어붙이는 방식이라 원문과 미묘하게
        # 어긋날 잠재 위험이 있었다(개행/공백 edge case).
        data["proposed_body"] = proposed_body
    # Metadata is part of the approved write, so it must travel on the same
    # control event as the body.  Creation uses description/tags; an existing
    # skill patch uses new_description/new_tags.  Keep empty creation values
    # ("" / []) explicit so the review card can show the exact persisted shape.
    if description is not None:
        data["description"] = description
    if tags is not None:
        data["tags"] = list(tags)
    if new_description is not None:
        data["new_description"] = new_description
    if new_tags is not None:
        data["new_tags"] = list(new_tags)
    if warnings:
        data["warnings"] = list(warnings)
    # Provenance— 카드가 "외부 콘텐츠 유래" 배지 + 근거 개수를 표시.
    if derived_from_external:
        data["derived_from_external"] = True
    if evidence_message_ids:
        data["evidence_message_ids"] = list(evidence_message_ids)
    return SSEEvent(EventType.SKILL_PATCH_PROPOSED, data)
