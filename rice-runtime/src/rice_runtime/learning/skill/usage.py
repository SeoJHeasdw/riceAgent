"""skill/usage.py — .usage.json 사이드카 카운터.

책임(single): 각 스킬에 대한 use/view/patch 카운터와 last_used_at 타임스탬프를
<name>.usage.json 파일로 관리한다.

포맷 (LEARNING_LOOP §3):
  {
    "skill": "<name>",
    "use": <int>,      # 턴 종료 시 **관여(engagement) 근거가 있는** 스킬에 +1 (observe)
    "view": <int>,     # 모델의 skill_view 온디맨드 로딩 시 +1 (view_skill_body)
    "injected": <int>, # 자동 전문 주입 횟수 (관여 여부와 무관 — 노출 측정)
    "patch": <int>,    # 본문 수정 승인 시 +1 (apply_patch에서)
    "friction": <int>, # 이 스킬이 관여한 턴에서 교정/실패 신호가 감지된 횟수
    "routing_miss": <int>, # 라우터가 못 맞췄는데 모델이 수동 로딩한 횟수 (발견성 결함 신호)
    "last_used_at": "<ISO8601 | null>",
    "created_at": "<ISO8601>",
    "tools": {"<tool_name>": <int>, ...}   # observed 도구 호출 ledger (아래 참고)
  }

정직화(2026-07-11): 이전엔 자동 주입 = use+1 이었다 — 라우터 오탐 한 번에
last_used_at 이 갱신되고 curator 의 stale/archive 판단·리뷰 카탈로그 정렬이 전부
오염됐다(현장 데이터: 서로 무관한 스킬 3개가 같은 초에 "사용"됨). 이제
use 는 관여 근거(명시적 언급, skill_view, 주입 본문이 지시한 도구의 실제 호출)가
있을 때만 오르고, 단순 노출은 injected 로 분리 집계한다. UI 상세 조회(get_skill)는
더 이상 view 를 올리지 않는다 — 모델의 온디맨드 로딩 신호와 사용자의 패널 열람은
다른 사건이다(view=152 vs use=26 인플레이션의 주범).

`tools` — **observed 도구 단서 ledger**: 이 스킬이 주입돼 있던 턴에서 실제로
호출된 도구별 누적 카운트 (observe에서 record_tools로 증가). 분석 사이드카의
mentioned(본문 감지)·frontmatter tools(declared 선언)와 출처가 분리된 실측
단서다. 턴 단위 귀속이므로 "이 스킬이 그 도구를 시켰다"는 인과 단정이 아니라
"주입된 턴의 실행 로그"라는 설명용 근거임에 유의 — UI도 그렇게 표기한다.

파일 경로: ~/.rice/skills/<name>.usage.json
파일이 없으면 0으로 초기화된 기본값을 반환하고, 증가 시 생성한다.

WHY: usage 데이터는 스킬 본문과 별도 파일로 관리한다. 스킬 본문을 편집해도
카운터가 날아가지 않도록 하기 위함이다. apply_patch 시 .history/에 본문만 보관하고
usage는 유지된다 (LEARNING_LOOP §5.2.4).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from ...core.paths import RicePaths
from ...core.types import now_iso


# observed 도구 ledger 상한 — 한 스킬의 사이드카가 무한히 크지 않게. 초과 시
# 최저 카운트부터 탈락(자주 쓰인 도구가 살아남는 게 설명 가치가 높다).
MAX_OBSERVED_TOOLS = 40


@dataclass
class UsageData:
    skill: str
    use: int = 0
    view: int = 0
    injected: int = 0
    patch: int = 0
    friction: int = 0
    routing_miss: int = 0
    last_used_at: str | None = None
    created_at: str = ""
    tools: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "skill": self.skill,
            "use": self.use,
            "view": self.view,
            "injected": self.injected,
            "patch": self.patch,
            "friction": self.friction,
            "routing_miss": self.routing_miss,
            "last_used_at": self.last_used_at,
            "created_at": self.created_at,
            "tools": self.tools,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UsageData":
        raw_tools = d.get("tools") or {}
        tools = {str(k): int(v) for k, v in raw_tools.items()} if isinstance(raw_tools, dict) else {}
        return cls(
            skill=d.get("skill", ""),
            use=int(d.get("use", 0)),
            view=int(d.get("view", 0)),
            injected=int(d.get("injected", 0)),
            patch=int(d.get("patch", 0)),
            friction=int(d.get("friction", 0)),
            routing_miss=int(d.get("routing_miss", 0)),
            last_used_at=d.get("last_used_at"),
            created_at=d.get("created_at", ""),
            tools=tools,
        )


class UsageStore:
    """~/.rice/skills/<name>.usage.json 파일 CRUD (LEARNING_LOOP §3)."""

    def __init__(self, paths: RicePaths) -> None:
        self._paths = paths

    def _usage_path(self, name: str) -> Path:
        return self._paths.skills / f"{name}.usage.json"

    def read(self, name: str) -> UsageData:
        """usage 파일을 읽는다. 없으면 0으로 초기화된 기본값 반환."""
        path = self._usage_path(name)
        if not path.exists():
            return UsageData(skill=name, created_at=now_iso())
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return UsageData.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            # NOTE(agent): 손상된 usage 파일은 기본값으로 복구한다.
            # 카운터 손실은 감내하고 런타임 오류를 막는 것이 우선이다.
            return UsageData(skill=name, created_at=now_iso())

    def _write(self, usage: UsageData) -> None:
        """usage 파일을 원자적으로 쓴다 (temp write → rename — docstring이 늘
        '원자적'이라 주장해 왔으나 write_text 직접 쓰기였다. 코드가 주장을 따라간다)."""
        self._paths.skills.mkdir(parents=True, exist_ok=True)
        path = self._usage_path(usage.skill)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(usage.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def increment_use(self, name: str) -> UsageData:
        """use += 1, last_used_at = now. 턴 관찰(observe)에서 호출."""
        usage = self.read(name)
        usage.use += 1
        usage.last_used_at = now_iso()
        self._write(usage)
        return usage

    def increment_view(self, name: str) -> UsageData:
        """view += 1. 모델의 skill_view 온디맨드 로딩(view_skill_body)에서만 증가 —
        UI 상세 조회는 세지 않는다(텔레메트리 정직화, 2026-07-11)."""
        usage = self.read(name)
        usage.view += 1
        self._write(usage)
        return usage

    def increment_injected(self, name: str) -> UsageData:
        """injected += 1. prefetch 가 전문을 자동 주입한 턴의 관찰(observe)에서
        호출 — 노출(exposure) 집계이며 use(관여)와 분리된다."""
        usage = self.read(name)
        usage.injected += 1
        self._write(usage)
        return usage

    def increment_friction(self, name: str) -> UsageData:
        """friction += 1. 이 스킬이 관여한 턴에서 교정/실패 신호(signals.py)가
        감지됐을 때 — 리뷰 LLM에게 '이 스킬 절차를 의심하라'는 힌트가 된다."""
        usage = self.read(name)
        usage.friction += 1
        self._write(usage)
        return usage

    def increment_routing_miss(self, name: str) -> UsageData:
        """routing_miss += 1. 관련도 라우터가 이번 턴 쿼리에 못 맞췄는데 모델이
        skill_view 로 수동 로딩했을 때 — 발견성(설명/태그) 결함의 실측 신호."""
        usage = self.read(name)
        usage.routing_miss += 1
        self._write(usage)
        return usage

    def increment_patch(self, name: str) -> UsageData:
        """patch += 1. apply_patch 승인 시 증가."""
        usage = self.read(name)
        usage.patch += 1
        self._write(usage)
        return usage

    def record_tools(self, name: str, tool_names: list[str]) -> UsageData:
        """observed 도구 ledger 갱신 — 이 스킬이 주입돼 있던 턴에서 호출된 도구별
        카운트 누적 (observe에서 호출). 상한 초과 시 최저 카운트부터 탈락."""
        usage = self.read(name)
        for tool in tool_names:
            if not tool:
                continue
            usage.tools[tool] = usage.tools.get(tool, 0) + 1
        if len(usage.tools) > MAX_OBSERVED_TOOLS:
            kept = sorted(usage.tools.items(), key=lambda kv: kv[1], reverse=True)
            usage.tools = dict(kept[:MAX_OBSERVED_TOOLS])
        self._write(usage)
        return usage

    def delete(self, name: str) -> None:
        """usage 사이드카 파일을 삭제한다 (DELETE /skills/{id}). idempotent."""
        self._usage_path(name).unlink(missing_ok=True)
