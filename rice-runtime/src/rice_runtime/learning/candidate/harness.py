"""learning/candidate/harness.py — baseline-vs-candidate 평가 엔진 (§10.6).

"승인된 제안이 실제로 더 나은가"를 같은 provider·같은 scaffold·같은 도구
표면에서 스킬 블록만 바꿔(단일 변인) 측정한다. eval/scripted_provider.py 가
증명하지 못했던 것 — marker 유무로 분기하는 각본 재생은 "모델이 스킬 덕에
다르게 행동한다"의 증거가 아니다 — 을 실제 provider 행동 관찰로 대체한다.

절차:
  1. **과제 생성** — held-out(제안을 낳은 구간의 실제 사용자 발화) +
     paraphrase(LLM 재표현, 원문 암기 과적합 방지) + 공유 rubric.
  2. **롤아웃** — 과제 × {baseline, candidate} × runs. rollout.py 의 시뮬레이션
     미니 루프(부작용 없음, 두 arm 동일 환경).
  3. **심판** — rubric 기반 pairwise 비교. 라벨은 A/B 로 은닉하고 좌우는
     (task, run) 인덱스로 결정적으로 스왑해 위치 편향을 상쇄한다.
  4. **판정** — 회귀 조건(§10.5: 성공률·비용) 위반이 하나도 없으면 promote.
     이 게이트는 "개선 증명"이 아니라 **회귀 방지**다: 변경 자체에는 사용자가
     이미 동의했고(HITL), 평가는 그 동의가 성능을 되돌리지 않음을 확인한다.
     tie 는 통과다 — 보수 편향을 폐기 쪽에 걸면 정상 개선이 심판 노이즈로
     계속 좌초한다.

실패 구분(리뷰 엔진과 동일 원칙): provider/infra 실패는 CandidateEvalError 로
전파해 재시도 계약(store.retry)을 태우고, "회귀 검출"은 정상 결과다 — 둘은
다른 사건이다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Sequence

from ...agent.providers.interfaces import ILLMProvider
from ...core.model_json import extract_json_object
from ...core.types import Message
from ...prompts.candidate_eval import (
    JUDGE_SCHEMA,
    JUDGE_SYSTEM_PROMPT,
    TASK_GEN_SCHEMA,
    TASK_GEN_SYSTEM_PROMPT,
    build_judge_user_prompt,
    build_rollout_system_prompt,
    build_task_gen_user_prompt,
    render_transcript,
)
from ..skill.verifier import tool_call_refs
from .rollout import RolloutResult, build_sandbox_tool_specs, run_rollout

log = logging.getLogger("rice.learning.candidate.harness")

_STRUCTURED_TEMPERATURE = 0.1  # 구조화 생성 고정 온도 (사용자 온도와 독립)
_SEED_PROMPT_MAX_CHARS = 600
_DEFAULT_RUBRIC_SIZE = 4
# 비용 회귀의 절대 하한 — 상대 허용율만 쓰면 짧은 출력에서 몇십 자 차이가
# 회귀로 오탐된다("Deployed." 9자 vs 검증 문장 포함 31자 = 3.4배). 상대 초과
# **그리고** 이 절대 초과를 모두 넘어야 비용 회귀다.
_COST_MIN_DELTA_CHARS = 2_000


class CandidateEvalError(RuntimeError):
    """평가 인프라 실패(provider 장애, 출력 파싱 불능) — 재시도 대상.

    '회귀 검출로 폐기'와 구분된다: 그쪽은 정상 판정이고 이쪽은 판단 불능이다."""


@dataclass(frozen=True)
class EvalTask:
    id: str
    prompt: str
    kind: str  # "held_out" | "paraphrase"


@dataclass(frozen=True)
class ArmMetrics:
    """한 arm 의 과제 전체 집계."""

    success_rate: float  # 심판 rubric 충족률 평균 (0..1)
    avg_llm_calls: float
    avg_output_chars: float
    wins: int
    losses: int
    ties: int


@dataclass(frozen=True)
class CandidateVerdict:
    promote: bool
    reasons: tuple[str, ...]  # 폐기 사유 코드 (promote=True 면 빈 튜플)
    report: dict  # 영속/표시용 전체 리포트 (skill_candidates.report)


class CandidateEvalHarness:
    """resolve_provider 는 메인 turn/review 와 같은 provider 를 쓴다 — 평가가
    측정하는 것은 '이 사용자의 실제 모델이 이 스킬로 더 잘하는가'이므로 더 싼
    모델로 대체하면 결과가 배포 환경을 대표하지 못한다."""

    def __init__(
        self,
        *,
        resolve_provider: Callable[[], ILLMProvider],
        render_skill_block: Callable[[str, str, int], str],
        resolve_tool_names: Callable[[], set[str]] | None = None,
        paraphrase_tasks: int = 2,
        max_tasks: int = 4,
        runs_per_task: int = 1,
        max_steps: int = 5,
        cost_tolerance: float = 0.5,
        output_max_tokens: int = 1_024,
    ) -> None:
        self._resolve_provider = resolve_provider
        # (skill_id, body, version) → 시스템 프롬프트 스킬 블록. 서비스가 실제
        # 주입 렌더러(_render_skills)를 묶어 넘긴다 — 평가 arm 이 실전과 같은
        # 표면을 보게 하는 배선.
        self._render_skill_block = render_skill_block
        self._resolve_tool_names = resolve_tool_names
        self._paraphrase_tasks = max(0, paraphrase_tasks)
        self._max_tasks = max(1, max_tasks)
        self._runs_per_task = max(1, runs_per_task)
        self._max_steps = max(1, max_steps)
        self._cost_tolerance = max(0.0, cost_tolerance)
        self._output_max_tokens = max(64, output_max_tokens)

    # ── public entrypoint ────────────────────────────────────────────────────
    async def evaluate(
        self,
        *,
        skill_id: str,
        baseline_body: str | None,
        candidate_body: str,
        baseline_version: int,
        description: str = "",
        seed_prompts: Sequence[str] = (),
        changelog: str = "",
    ) -> CandidateVerdict:
        """baseline_body=None 은 신규 생성 후보 — baseline arm 은 스킬 없이 돈다
        (cold-vs-warm ablation, LEARNING_EVAL §3 과 같은 축)."""
        provider = self._resolve_provider()
        if getattr(provider, "name", "") == "echo":
            raise CandidateEvalError("no real provider available for candidate eval")

        tasks, rubric, task_notes = await self._build_tasks(
            provider, skill_id=skill_id, description=description,
            seed_prompts=list(seed_prompts), changelog=changelog,
        )

        # 두 arm 이 같은 도구 표면(합집합)을 본다 — 표면 차이가 스킬 차이와
        # 교락되지 않게. 카탈로그 리졸버가 있으면 실재 도구로 한정한다.
        ref_names = list(
            dict.fromkeys(tool_call_refs(candidate_body) + tool_call_refs(baseline_body or ""))
        )
        if self._resolve_tool_names is not None:
            try:
                known = self._resolve_tool_names()
                ref_names = [n for n in ref_names if n in known]
            except Exception:  # noqa: BLE001 — 카탈로그 실패는 검사 스킵으로 강등
                log.warning("tool catalog resolve failed for candidate eval", exc_info=True)
        sandbox_tools = build_sandbox_tool_specs(ref_names)

        baseline_block = (
            self._render_skill_block(skill_id, baseline_body, baseline_version)
            if baseline_body is not None
            else ""
        )
        candidate_block = self._render_skill_block(
            skill_id, candidate_body, baseline_version + 1
        )
        baseline_system = build_rollout_system_prompt(baseline_block)
        candidate_system = build_rollout_system_prompt(candidate_block)

        # ── 롤아웃 + 심판 ────────────────────────────────────────────────────
        per_task: list[dict] = []
        base_success: list[float] = []
        cand_success: list[float] = []
        base_calls: list[int] = []
        cand_calls: list[int] = []
        base_chars: list[int] = []
        cand_chars: list[int] = []
        wins = losses = ties = 0

        try:
            for t_index, task in enumerate(tasks):
                for run_index in range(self._runs_per_task):
                    base_run = await run_rollout(
                        provider,
                        system_prompt=baseline_system,
                        task_prompt=task.prompt,
                        tools=sandbox_tools,
                        max_steps=self._max_steps,
                        max_output_tokens=self._output_max_tokens,
                    )
                    cand_run = await run_rollout(
                        provider,
                        system_prompt=candidate_system,
                        task_prompt=task.prompt,
                        tools=sandbox_tools,
                        max_steps=self._max_steps,
                        max_output_tokens=self._output_max_tokens,
                    )
                    # 좌우 은닉 + 결정적 스왑: 짝수 (task+run) 에서 candidate=A.
                    candidate_is_a = (t_index + run_index) % 2 == 0
                    judged = await self._judge(
                        provider,
                        task_prompt=task.prompt,
                        rubric=rubric,
                        a=cand_run if candidate_is_a else base_run,
                        b=base_run if candidate_is_a else cand_run,
                    )
                    rubric_size = max(1, len(rubric))
                    a_rate = min(rubric_size, max(0, judged["a_criteria_met"])) / rubric_size
                    b_rate = min(rubric_size, max(0, judged["b_criteria_met"])) / rubric_size
                    c_rate, b_rate2 = (a_rate, b_rate) if candidate_is_a else (b_rate, a_rate)
                    winner = judged["winner"]
                    cand_won = winner == ("A" if candidate_is_a else "B")
                    base_won = winner == ("B" if candidate_is_a else "A")
                    wins += 1 if cand_won else 0
                    losses += 1 if base_won else 0
                    ties += 1 if winner == "tie" else 0

                    cand_success.append(c_rate)
                    base_success.append(b_rate2)
                    base_calls.append(base_run.llm_calls)
                    cand_calls.append(cand_run.llm_calls)
                    base_chars.append(base_run.output_chars)
                    cand_chars.append(cand_run.output_chars)
                    per_task.append(
                        {
                            "task_id": task.id,
                            "kind": task.kind,
                            "run": run_index,
                            "prompt": task.prompt[:200],
                            "candidate_is_a": candidate_is_a,
                            "winner": (
                                "candidate" if cand_won else "baseline" if base_won else "tie"
                            ),
                            "candidate_criteria_rate": round(c_rate, 4),
                            "baseline_criteria_rate": round(b_rate2, 4),
                            "candidate_tool_calls": [c["name"] for c in cand_run.tool_calls],
                            "baseline_tool_calls": [c["name"] for c in base_run.tool_calls],
                            "judge_reason": str(judged.get("reason") or "")[:300],
                        }
                    )
        except CandidateEvalError:
            raise
        except Exception as exc:  # noqa: BLE001 — provider/스트림 실패는 재시도 대상
            log.exception("candidate rollout failed for %s", skill_id)
            raise CandidateEvalError(f"candidate rollout failed: {exc}") from exc

        base_metrics = _aggregate(base_success, base_calls, base_chars, losses, wins, ties)
        cand_metrics = _aggregate(cand_success, cand_calls, cand_chars, wins, losses, ties)

        # ── 판정: 회귀 조건 (§10.5 성공률·비용) ─────────────────────────────
        reasons: list[str] = []
        if cand_metrics.success_rate < base_metrics.success_rate - 1e-9:
            reasons.append("success_regression")
        if (
            base_metrics.avg_output_chars > 0
            and cand_metrics.avg_output_chars
            > base_metrics.avg_output_chars * (1 + self._cost_tolerance)
            and cand_metrics.avg_output_chars - base_metrics.avg_output_chars
            > _COST_MIN_DELTA_CHARS
        ):
            reasons.append("cost_regression")
        # 심판이 baseline 우세로 일관하면(candidate 무승·유패) rubric 평균이 같아도
        # 회귀 신호다 — pairwise 판단은 rate 절삭보다 민감하다.
        if losses > 0 and wins == 0:
            reasons.append("judge_preference_regression")

        report = {
            "schema_version": 1,
            "mode": "simulated_rollout",
            "skill_id": skill_id,
            "baseline_version": baseline_version,
            "rubric": list(rubric),
            "tasks": [{"id": t.id, "kind": t.kind, "prompt": t.prompt} for t in tasks],
            "task_notes": task_notes,
            "runs_per_task": self._runs_per_task,
            "baseline": _metrics_dict(base_metrics),
            "candidate": _metrics_dict(cand_metrics),
            "per_task": per_task,
            "verdict": "promote" if not reasons else "discard",
            "reasons": list(reasons),
        }
        return CandidateVerdict(
            promote=not reasons, reasons=tuple(reasons), report=report
        )

    # ── task generation ──────────────────────────────────────────────────────
    async def _build_tasks(
        self,
        provider: ILLMProvider,
        *,
        skill_id: str,
        description: str,
        seed_prompts: list[str],
        changelog: str,
    ) -> tuple[list[EvalTask], list[str], dict]:
        seeds = [
            " ".join(p.split())[:_SEED_PROMPT_MAX_CHARS] for p in seed_prompts if p.strip()
        ]
        tasks: list[EvalTask] = [
            EvalTask(id=f"held-{i + 1}", prompt=p, kind="held_out")
            for i, p in enumerate(seeds[: self._max_tasks])
        ]
        notes: dict = {"seed_count": len(seeds)}

        rubric: list[str] = []
        want_paraphrases = min(
            self._paraphrase_tasks, max(0, self._max_tasks - len(tasks))
        )
        # seed 가 하나도 없으면(구형 제안함 payload 등) 생성이 유일한 과제
        # 원천이라 실패를 infra 오류로 취급한다. seed 가 있으면 held-out 만으로도
        # 평가가 성립하므로 생성 실패는 강등 + 기록으로 감내한다.
        try:
            generated = await self._generate_tasks(
                provider,
                skill_id=skill_id,
                description=description or changelog,
                seed_prompts=seeds or [description or skill_id],
                paraphrase_count=max(1, want_paraphrases) if not tasks else want_paraphrases,
            )
            rubric = generated["rubric"]
            for i, p in enumerate(generated["paraphrases"]):
                if len(tasks) >= self._max_tasks:
                    break
                tasks.append(EvalTask(id=f"para-{i + 1}", prompt=p, kind="paraphrase"))
        except CandidateEvalError:
            if not tasks:
                raise
            notes["paraphrase_failed"] = True

        if not tasks:
            raise CandidateEvalError("no eval tasks could be built for candidate")
        if not rubric:
            # rubric 없는 심판은 무의미 — 최소 폴백: 과제 충실성 기준 한 줄.
            rubric = ["The response directly addresses and completes the user's request."]
            notes["rubric_fallback"] = True
        return tasks, rubric[:_DEFAULT_RUBRIC_SIZE], notes

    async def _generate_tasks(
        self,
        provider: ILLMProvider,
        *,
        skill_id: str,
        description: str,
        seed_prompts: list[str],
        paraphrase_count: int,
    ) -> dict:
        if paraphrase_count <= 0:
            return {"paraphrases": [], "rubric": []}
        raw = await self._structured_call(
            provider,
            system=TASK_GEN_SYSTEM_PROMPT,
            user=build_task_gen_user_prompt(
                skill_id=skill_id,
                description=description,
                seed_prompts=seed_prompts,
                paraphrase_count=paraphrase_count,
            ),
            schema=TASK_GEN_SCHEMA,
        )
        paraphrases = [
            " ".join(str(p).split())[:_SEED_PROMPT_MAX_CHARS]
            for p in (raw.get("paraphrases") or [])
            if str(p).strip()
        ]
        rubric = [
            " ".join(str(c).split())[:200] for c in (raw.get("rubric") or []) if str(c).strip()
        ]
        return {"paraphrases": paraphrases[:paraphrase_count], "rubric": rubric}

    # ── judge ────────────────────────────────────────────────────────────────
    async def _judge(
        self,
        provider: ILLMProvider,
        *,
        task_prompt: str,
        rubric: list[str],
        a: RolloutResult,
        b: RolloutResult,
    ) -> dict:
        raw = await self._structured_call(
            provider,
            system=JUDGE_SYSTEM_PROMPT,
            user=build_judge_user_prompt(
                task_prompt=task_prompt,
                rubric=rubric,
                transcript_a=render_transcript(list(a.tool_calls), a.final_text),
                transcript_b=render_transcript(list(b.tool_calls), b.final_text),
            ),
            schema=JUDGE_SCHEMA,
        )
        winner = str(raw.get("winner") or "tie")
        if winner not in ("A", "B", "tie"):
            winner = "tie"
        try:
            a_met = int(raw.get("a_criteria_met"))
            b_met = int(raw.get("b_criteria_met"))
        except (TypeError, ValueError) as exc:
            raise CandidateEvalError(f"judge returned malformed counts: {raw!r}") from exc
        return {
            "a_criteria_met": a_met,
            "b_criteria_met": b_met,
            "winner": winner,
            "reason": raw.get("reason"),
        }

    # ── shared structured call ───────────────────────────────────────────────
    async def _structured_call(
        self, provider: ILLMProvider, *, system: str, user: str, schema: dict
    ) -> dict:
        """단발 구조화 호출 — think 억제 + 스키마 디코딩 + 출력 상한(플래너/분석
        사이드카에서 검증된 소형 모델 안정화 레시피)."""
        parts: list[str] = []
        try:
            async for chunk in provider.stream(
                system=system,
                messages=[Message(role="user", content=user)],
                temperature=_STRUCTURED_TEMPERATURE,
                response_schema=schema,
                think=False,
                max_tokens=self._output_max_tokens,
            ):
                if chunk.kind == "text" and chunk.text:
                    parts.append(chunk.text)
        except Exception as exc:  # noqa: BLE001
            raise CandidateEvalError(f"structured eval call failed: {exc}") from exc
        data = extract_json_object("".join(parts))
        if data is None:
            raise CandidateEvalError("structured eval call returned no JSON object")
        return data


def _aggregate(
    success: list[float], calls: list[int], chars: list[int], wins: int, losses: int, ties: int
) -> ArmMetrics:
    n = max(1, len(success))
    return ArmMetrics(
        success_rate=round(sum(success) / n, 4),
        avg_llm_calls=round(sum(calls) / max(1, len(calls)), 2),
        avg_output_chars=round(sum(chars) / max(1, len(chars)), 1),
        wins=wins,
        losses=losses,
        ties=ties,
    )


def _metrics_dict(m: ArmMetrics) -> dict:
    return {
        "success_rate": m.success_rate,
        "avg_llm_calls": m.avg_llm_calls,
        "avg_output_chars": m.avg_output_chars,
        "wins": m.wins,
        "losses": m.losses,
        "ties": m.ties,
    }
