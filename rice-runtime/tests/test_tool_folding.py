"""Progressive disclosure (RUNTIME_DESIGN §2.1/§11) — connector+plugin ("non-core")
도구 스키마가 예산을 넘으면 tool_search/tool_describe/tool_call 3개 브릿지로 접힌다.

TestFoldToolsForBudget 은 순수 함수(fold_tools_for_budget)를, TestBridgeDispatch 는
ToolDispatcher 가 브릿지 호출을 실제 재귀 dispatch 로 처리하며 정책(allow/approval/block)을
그대로 강제하는지를 검증한다 — 이 브릿지가 §5 실행 승인을 우회하지 않는다는 게 핵심 불변식.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from rice_runtime.agent.tool_dispatch import (
    BRIDGE_TOOL_SPECS,
    ToolDispatcher,
    _TOOL_BUDGET_TOKENS,
    fold_tools_for_budget,
)
from rice_runtime.core.types import ApprovalResponse, ToolCall, ToolResult, ToolSpec


def _spec(name: str, *, source="connector", owner_id="c1", requires_approval=False, big=False) -> ToolSpec:
    description = ("x" * 500) if big else f"{name} does a thing"
    parameters = {"type": "object", "properties": {"a": {"type": "string"}}}
    return ToolSpec(
        name=name, description=description, parameters=parameters,
        source=source, owner_id=owner_id, requires_approval=requires_approval,
    )


class TestFoldToolsForBudget:
    def test_under_budget_keeps_everything_as_is(self):
        core = (_spec("builtin_echo", source="builtin"),)
        foldable = tuple(_spec(f"tool_{i}") for i in range(5))
        result = fold_tools_for_budget(core, foldable)
        assert result == core + foldable

    def test_over_budget_folds_to_three_bridges(self):
        core = (_spec("builtin_echo", source="builtin"),)
        # 충분히 큰 도구를 예산(15K 토큰 = 60K 문자 상당)을 넘도록 다량 생성.
        foldable = tuple(_spec(f"big_tool_{i}", big=True) for i in range(200))
        result = fold_tools_for_budget(core, foldable)
        assert result == core + BRIDGE_TOOL_SPECS
        assert {t.name for t in result} == {"builtin_echo", "tool_search", "tool_describe", "tool_call"}

    def test_bridge_specs_never_require_approval(self):
        """브릿지 자체는 항상 승인 없이 통과 — 실제 승인은 재귀 dispatch 된 실도구가 결정."""
        assert all(t.requires_approval is False for t in BRIDGE_TOOL_SPECS)

    def test_tool_call_bridge_is_conservatively_serialized(self):
        """스케줄러가 숨은 target의 parallel_safe를 못 보므로 실행 bridge는 직렬화한다."""
        assert BRIDGE_TOOL_SPECS[2].name == "tool_call"
        assert BRIDGE_TOOL_SPECS[2].parallel_safe is False

    def test_budget_constant_matches_design_doc(self):
        """RICE_LEARNING_LOOP_DESIGN.md §7 표의 MCP 도구 예산(~15K) — 회귀 방지용 고정."""
        assert _TOOL_BUDGET_TOKENS == 15_000


class TestBridgeDispatch:
    def _dispatcher(self, *, folded, permission="allow", approve=True):
        connectors = MagicMock()
        connectors.permission_for = MagicMock(return_value=permission)
        connectors.invoke = AsyncMock(return_value=ToolResult("inner", ok=True, result={"echoed": True}))
        approvals = MagicMock()
        approvals.request = AsyncMock(
            return_value=ApprovalResponse(approval_id="a1", decision="approve" if approve else "reject")
        )
        disp = ToolDispatcher(
            connectors=connectors, plugins=MagicMock(), approvals=approvals, approval_timeout_s=1,
            folded_catalog={t.name: t for t in folded},
        )
        return disp, connectors, approvals

    @pytest.mark.asyncio
    async def test_tool_search_matches_name_or_description(self):
        folded = [
            _spec("gmail_search_threads", requires_approval=False),
            _spec("gmail_send", requires_approval=True),
            _spec("calendar_list_events", requires_approval=False),
        ]
        disp, _, _ = self._dispatcher(folded=folded)
        call = ToolCall(tool_name="tool_search", arguments={"query": "gmail"}, id="s1")
        _ = [ev async for ev in disp.dispatch("sess", call, BRIDGE_TOOL_SPECS[0])]
        res = await disp.result_for(call)
        assert res.ok is True
        names = {m["name"] for m in res.result["matches"]}
        assert names == {"gmail_search_threads", "gmail_send"}

    @pytest.mark.asyncio
    async def test_tool_search_empty_query_returns_everything(self):
        folded = [_spec("a"), _spec("b")]
        disp, _, _ = self._dispatcher(folded=folded)
        call = ToolCall(tool_name="tool_search", arguments={"query": ""}, id="s1")
        _ = [ev async for ev in disp.dispatch("sess", call, BRIDGE_TOOL_SPECS[0])]
        res = await disp.result_for(call)
        assert {m["name"] for m in res.result["matches"]} == {"a", "b"}

    @pytest.mark.asyncio
    async def test_tool_describe_returns_full_schema(self):
        folded = [_spec("gmail_send", requires_approval=True)]
        disp, _, _ = self._dispatcher(folded=folded)
        call = ToolCall(tool_name="tool_describe", arguments={"name": "gmail_send"}, id="d1")
        _ = [ev async for ev in disp.dispatch("sess", call, BRIDGE_TOOL_SPECS[1])]
        res = await disp.result_for(call)
        assert res.ok is True
        assert res.result["name"] == "gmail_send"
        assert res.result["parameters"] == folded[0].parameters

    @pytest.mark.asyncio
    async def test_tool_describe_unknown_name_errors(self):
        disp, _, _ = self._dispatcher(folded=[])
        call = ToolCall(tool_name="tool_describe", arguments={"name": "nope"}, id="d2")
        _ = [ev async for ev in disp.dispatch("sess", call, BRIDGE_TOOL_SPECS[1])]
        res = await disp.result_for(call)
        assert res.ok is False and "nope" in (res.error or "")

    @pytest.mark.asyncio
    async def test_tool_call_allow_policy_invokes_real_connector_tool(self):
        folded = [_spec("gmail_search_threads", requires_approval=False)]
        disp, connectors, approvals = self._dispatcher(folded=folded, permission="allow")
        call = ToolCall(
            tool_name="tool_call",
            arguments={"name": "gmail_search_threads", "arguments": {"q": "invoice"}},
            id="c1",
        )
        events = [ev async for ev in disp.dispatch("sess", call, BRIDGE_TOOL_SPECS[2])]
        res = await disp.result_for(call)
        assert res.ok is True and res.result == {"echoed": True}
        approvals.request.assert_not_called()
        connectors.invoke.assert_awaited_once()
        inner_call = connectors.invoke.call_args.args[0]
        assert inner_call.tool_name == "gmail_search_threads"
        assert inner_call.arguments == {"q": "invoice"}
        assert [(ev.type, ev.data["tool_call_id"]) for ev in events] == [
            ("tool_call", "c1"),
            ("tool_result", "c1"),
        ]
        assert events[0].data == {
            "tool_call_id": "c1",
            "tool_name": "gmail_search_threads",
            "arguments": {"q": "invoice"},
        }
        assert all(":bridged" not in str(ev.data) for ev in events)

    @pytest.mark.asyncio
    async def test_tool_call_does_not_bypass_approval_policy(self):
        """핵심 불변식: 브릿지를 거쳐도 도구 자신의 승인 정책은 그대로 강제된다."""
        folded = [_spec("gmail_send", requires_approval=True)]
        disp, connectors, approvals = self._dispatcher(folded=folded, permission="approval", approve=True)
        call = ToolCall(
            tool_name="tool_call", arguments={"name": "gmail_send", "arguments": {"to": "x@y.com"}}, id="c2",
        )
        events = [ev async for ev in disp.dispatch("sess", call, BRIDGE_TOOL_SPECS[2])]
        res = await disp.result_for(call)
        assert res.ok is True
        approvals.request.assert_awaited_once()  # 실제 승인 절차를 거쳤다
        connectors.invoke.assert_awaited_once()
        assert [ev.type for ev in events] == [
            "tool_call",
            "tool_approval_required",
            "tool_result",
        ]
        assert events[0].data == {
            "tool_call_id": "c2",
            "tool_name": "gmail_send",
            "arguments": {"to": "x@y.com"},
        }
        assert events[1].data["tool_name"] == "gmail_send"
        assert events[1].data["arguments"] == {"to": "x@y.com"}
        assert events[2].data["tool_call_id"] == "c2"
        assert all(":bridged" not in str(ev.data) for ev in events)

    @pytest.mark.asyncio
    async def test_tool_call_rejected_approval_emits_one_outer_result(self):
        folded = [_spec("gmail_send", requires_approval=True)]
        disp, connectors, approvals = self._dispatcher(
            folded=folded, permission="approval", approve=False
        )
        call = ToolCall(
            tool_name="tool_call",
            arguments={"name": "gmail_send", "arguments": {"to": "x@y.com"}},
            id="c2-reject",
        )

        events = [ev async for ev in disp.dispatch("sess", call, BRIDGE_TOOL_SPECS[2])]
        res = await disp.result_for(call)

        assert res.ok is False and "reject" in (res.error or "")
        approvals.request.assert_awaited_once()
        connectors.invoke.assert_not_called()
        assert [ev.type for ev in events] == [
            "tool_call",
            "tool_approval_required",
            "tool_result",
        ]
        assert events[-1].data["tool_call_id"] == "c2-reject"
        assert all(":bridged" not in str(ev.data) for ev in events)

    @pytest.mark.asyncio
    async def test_tool_call_does_not_bypass_block_policy(self):
        folded = [_spec("gmail_delete_all", requires_approval=True)]
        disp, connectors, approvals = self._dispatcher(folded=folded, permission="block")
        call = ToolCall(
            tool_name="tool_call", arguments={"name": "gmail_delete_all", "arguments": {}}, id="c3",
        )
        events = [ev async for ev in disp.dispatch("sess", call, BRIDGE_TOOL_SPECS[2])]
        res = await disp.result_for(call)
        assert res.ok is False and "block" in (res.error or "").lower()
        connectors.invoke.assert_not_called()
        approvals.request.assert_not_called()
        assert [ev.type for ev in events] == ["tool_call", "tool_result"]
        assert events[0].data["tool_call_id"] == "c3"
        assert events[0].data["tool_name"] == "gmail_delete_all"
        assert events[1].data["tool_call_id"] == "c3"
        assert all(":bridged" not in str(ev.data) for ev in events)

    @pytest.mark.asyncio
    async def test_tool_call_unknown_name_errors_without_touching_connectors(self):
        disp, connectors, _ = self._dispatcher(folded=[])
        call = ToolCall(tool_name="tool_call", arguments={"name": "ghost", "arguments": {}}, id="c4")
        events = [ev async for ev in disp.dispatch("sess", call, BRIDGE_TOOL_SPECS[2])]
        res = await disp.result_for(call)
        assert res.ok is False and "ghost" in (res.error or "")
        connectors.invoke.assert_not_called()
        # Unknown targets still use the same presentation shape as persisted
        # bridge calls: one attempted target card, paired to one outer result.
        assert [ev.type for ev in events] == ["tool_call", "tool_result"]
        assert events[0].data == {
            "tool_call_id": "c4",
            "tool_name": "ghost",
            "arguments": {},
        }
        assert events[1].data["tool_call_id"] == "c4"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("arguments", "error"),
        [
            ({"name": 123, "arguments": {}}, "name must be"),
            ({"name": "gmail_send", "arguments": []}, "arguments must be"),
        ],
    )
    async def test_tool_call_rejects_malformed_bridge_arguments(self, arguments, error):
        folded = [_spec("gmail_send", requires_approval=False)]
        disp, connectors, approvals = self._dispatcher(folded=folded)
        call = ToolCall(tool_name="tool_call", arguments=arguments, id="bad-bridge")

        events = [ev async for ev in disp.dispatch("sess", call, BRIDGE_TOOL_SPECS[2])]
        res = await disp.result_for(call)

        assert res.ok is False and error in (res.error or "")
        connectors.invoke.assert_not_called()
        approvals.request.assert_not_called()
        assert [ev.type for ev in events] == ["tool_call", "tool_result"]
        assert events[0].data["tool_call_id"] == "bad-bridge"
        assert events[0].data["tool_name"] == "tool_call"
        assert events[1].data["tool_call_id"] == "bad-bridge"
