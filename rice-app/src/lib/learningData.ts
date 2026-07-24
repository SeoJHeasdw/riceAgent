import type { IconName } from '../components/ui/Icon';
import type { BackgroundTaskStatus, ImpactReport, PatchTrigger, PlanStepData, SkillFile } from './types';
import i18n from './i18n/index';

// 동적 키/returnObjects 호출용 넓은 타입 래퍼.
// i18next TFunction은 정적 키 기반 오버로드라 동적 템플릿 리터럴 키에 맞지 않는다.
// apiError.ts와 동일 패턴으로 unknown 경유 캐스트.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type TFnAny = (key: string, options?: Record<string, unknown>) => any;
const tAny: TFnAny = (i18n.t as unknown as TFnAny);

/**
 * learningData — mock content for the learning-loop surfaces (chat thread,
 * learning panel, my-skills management). Korean UI to match the approved
 * prototype and the demo audience.
 */

// ── Learning panel: user model, skills, installed tools ──────────
export interface UserSignal {
  id: string;
  label: string;
  value: string;
  conf: number;
  source: string;
}

/**
 * 출처 (provenance) — 이 스킬이 어디서 왔는가.
 * learned   — 내 대화/작업에서 자라난 스킬 (default)
 * installed — Skill Market에서 다운로드 (upstream으로 원본 추적)
 * authored  — 내가 직접 작성/제작
 * builtin   — 앱과 함께 제공되는 기본 내장 스킬 (읽기 전용 — 활성/비활성만 가능,
 *             삭제·수정·마켓 공유 불가; 런타임 source='builtin'에서 옴)
 */
export type SkillOrigin = 'learned' | 'installed' | 'authored' | 'builtin';

/**
 * 설치형(또는 fork된) 스킬의 원본 추적 — git fork의 upstream에 해당.
 * installedVer = 내가 복제한 fork 지점, latestVer = 원작자의 현재 버전.
 * latestVer > installedVer 이면 "업데이트 있음"을 표시한다.
 */
export interface SkillUpstream {
  /** 원작자 */
  author: string;
  team?: string;
  /** 마켓 스킬 id — 재방문·갱신용 */
  marketId: string;
  /** 설치 시점 버전 (fork 지점). 이후 내 버전이 이 위로 쌓인다. */
  installedVer: string;
  /** 원작자의 현재 버전. installedVer 보다 크면 업데이트 가능. */
  latestVer?: string;
}

/**
 * Skill.trend 에 저장되는 안정적 코드.
 * UI 라벨은 렌더 시 chat:learning.trend* 키로 번역한다.
 * 레거시 자유 문자열과 공존할 수 있도록 string 의 서브타입으로 정의하되,
 * 새 코드는 반드시 이 값 중 하나를 사용한다.
 *
 * 렌더 헬퍼: src/lib/learningData.ts 의 renderTrend(code, t)
 */
export type TrendCode =
  | 'trend_just_now'
  | 'trend_new'
  | 'trend_improved'
  | 'trend_improved_short'
  | 'trend_just_created'
  | 'trend_consolidated'
  | 'trend_consolidated_new'
  // 런타임 동적 코드 (LearningContext runtimeSkillToLearning)
  | 'trend_not_used_yet'
  | 'trend_in_use'
  | 'trend_used_today'
  | `trend_days_ago:${number}`;

export interface Skill {
  id: string;
  name: string;
  ver: string;
  /** 관여 근거가 확인된 턴 수 (자동 주입 횟수와 별개). */
  uses: number;
  /** 전문이 관련도 라우터에 의해 자동 주입된 노출 횟수. */
  injected?: number;
  /** 스킬 관여 턴에서 교정/실패 신호가 함께 감지된 횟수. */
  friction?: number;
  /** 자동 라우팅에 실패해 모델이 수동으로 로딩한 횟수. */
  routingMiss?: number;
  /**
   * stable code (TrendCode) 또는 레거시 자유 문자열.
   * 렌더 시 renderTrend(trend, t) 로 변환해 표시한다.
   */
  trend: string;
  enabled: boolean;
  hot?: boolean;
  shared?: boolean;
  /**
   * 출처 — 미설정 시 learned 로 간주 (skillOrigin 헬퍼 참고).
   * installed 면 upstream 으로 원본을 추적한다.
   */
  origin?: SkillOrigin;
  /** installed(또는 fork)일 때 원본 추적 */
  upstream?: SkillUpstream;
  /** 본문 개선(patch) 횟수 — version과 동기 (LEARNING_LOOP 5.2 / .usage.json) */
  patches?: number;
  /** 채팅에서 "/"로 호출하는 슬래시 커맨드 id (예: quarterly_sales_summary) */
  command?: string;
  /**
   * 생명주기 상태 (LEARNING_LOOP_DESIGN 5.1).
   * active  — 정상 사용 중 (default)
   * stale   — 사용이 줄어 재확인 권장
   * archived — 보관됨 (주입 중단)
   * 미설정 시 active 로 간주.
   */
  state?: 'active' | 'stale' | 'archived';
  /**
   * 마지막 lifecycle 활동 후 경과일 (freshness_at 기준, LEARNING_LOOP_DESIGN 5.1).
   * 실제 사용뿐 아니라 편집·생성·사용자의 명시적 재활성화가 freshness를 리셋한다.
   * 큐레이터 시간 규칙의 입력: 3일 이상 → stale, 7일 이상 → archived.
   * state 는 마지막 큐레이션 결과이고 idleDays 는 실제 경과 시간이라, 둘이
   * 어긋나 있으면("3일 지났는데 아직 active") "스킬 정리"가 전이를 발생시킨다.
   */
  idleDays?: number;
}

// A slash-command a plugin provides
export interface ProvidedSkill {
  command: string;
  desc: string;
}

export interface InstalledTool {
  id: string;
  name: string;
  kind: 'tool' | 'plugin';
  icon: IconName;
  enabled: boolean;
  meta: string;
  /** kind:'tool' — 도구 자체를 "/"로 호출할 때의 커맨드 id */
  command?: string;
  /** kind:'plugin' — 이 전문가가 제공하는 슬래시 커맨드 스킬들 */
  provides?: ProvidedSkill[];
}

// ── My-skills detail ────────────────────────────────────────────
export interface SkillRule {
  id: string;
  text: string;
  source: string;
  on: boolean;
  // ── 규칙 단위 텍스처 (실서비스 질감) ──
  /** 이 규칙이 실제 적용된 횟수 — 신뢰의 근거 */
  applied?: number;
  /** 학습 신뢰도 — 근거(교정·예시) 양에 따른 확신 정도 */
  confidence?: 'high' | 'medium' | 'low';
  /** 마지막으로 적용된 시점 (상대 표기) */
  lastApplied?: string;
}

export interface SkillVersionRule {
  text: string;
  /** 이 버전에서 새로 추가된 규칙인지 — 스냅샷에서 강조 표시 */
  added?: boolean;
}

/** 버전별 성과 — "이 버전으로 되돌릴까?"를 데이터로 판단하게 해주는 핵심 근거 */
export interface SkillVersionMetrics {
  /** 이 버전으로 실행된 횟수 */
  uses: number;
  /** 성공률 0..1 (사용자가 결과를 그대로 채택한 비율) */
  successRate: number;
  /** 평균 처리 턴 — 낮을수록 효율적 */
  avgTurns?: number;
  /** 사후 수정 비율 0..1 — 낮을수록 손댈 게 적었다는 뜻 */
  editRate?: number;
}

export interface SkillVersion {
  ver: string;
  when: string;
  note: string;
  /** note가 stable code일 때 보간용 파라미터. renderVersionNote()가 t()에 전달한다. */
  noteParams?: Record<string, string>;
  current?: boolean;
  /** 런타임이 보관한 해당 버전의 전체 SKILL.md 스냅샷. */
  body?: string;
  /** 이 버전 시점의 규칙 스냅샷 (읽기 전용 보기·복원용) */
  rules?: SkillVersionRule[];
  /** 이 버전의 실측 성과 — 복원 판단·버전 비교에 사용 */
  metrics?: SkillVersionMetrics;
  /** 변경 주체 — 'AI 자동 생성' · '내 교정' · 'AI 제안' 등 */
  author?: string;
}

/** 절차적 스킬(워크플로)의 한 단계 — 원자적 도구를 순서대로 배선한 것 */
export interface SkillStep {
  tool: string;
  label: string;
  desc: string;
  icon: IconName;
}

export interface SkillUsage {
  kind: string;
  name: string;
  icon: IconName;
}

export interface SkillPermission {
  name: string;
  mode: string;
}

/** 스킬 성과 요약 — 상세 헤더의 "성과" 스트립에 표시 (실서비스 질감) */
export interface SkillHealth {
  /** 현재 버전 성공률 0..1 */
  successRate: number;
  /** 직전 버전 대비 성공률 변화 pp (예: +0.08, -0.05) */
  successDelta?: number;
  /** 회당 절감 시간(분) — 사람이 직접 했을 때 대비 */
  timeSavedMin?: number;
  /** 평균 처리 턴 (warm) */
  avgTurns?: number;
  /** 스킬 없이 했을 때의 턴 (cold) — avgTurns와 대비 */
  coldTurns?: number;
  /** 최근 사용 추세 sparkline 포인트 (낮을수록 좋은 지표: 처리 턴) */
  spark?: number[];
  /** 사용자 피드백 집계 */
  rating?: { up: number; down: number };
}

/** 최근 활동 한 줄 — "살아있는" 데이터 질감을 주는 타임라인 */
export interface SkillActivity {
  when: string;
  text: string;
  kind: 'use' | 'edit' | 'learn' | 'share' | 'restore';
}

export type SkillAnalysisConfidence = 'high' | 'medium' | 'low';

export interface SkillAnalysisStep {
  title: string;
  description?: string;
  confidence?: SkillAnalysisConfidence;
  evidence?: string;
}

export interface SkillAnalysisToolMention {
  name: string;
  /** observed=runtime log, declared=manifest/metadata, mentioned=SKILL.md body inference */
  kind: 'observed' | 'declared' | 'mentioned';
  confidence?: SkillAnalysisConfidence;
  basis?: string;
  evidence?: string;
}

/**
 * AI-readable sidecar for SKILL.md. This is explanatory metadata only: it must
 * never drive permission, execution, or approval policy.
 */
export interface SkillAnalysis {
  status: 'fresh' | 'stale' | 'missing' | 'failed';
  bodyHash?: string;
  analyzerVersion?: string;
  analyzedAt?: string;
  /** 요약/단계 산출 주체 — llm(AI 분석) vs heuristic(규칙 기반 추출) */
  source?: 'llm' | 'heuristic';
  summary?: string;
  confidence?: SkillAnalysisConfidence;
  proceduralSteps?: SkillAnalysisStep[];
  toolMentions?: SkillAnalysisToolMention[];
  warnings?: string[];
}

/**
 * 런타임 분석 payload (snake_case) — GET /skills/{id}.analysis ·
 * POST /skills/{id}/analysis/refresh 응답. tool_mentions 는 서버가 이미
 * observed(실행 로그)/declared(frontmatter 선언)/mentioned(본문 감지) 세 출처를
 * 합류시킨 상태다 (learning/skill/analysis.py 계약).
 */
export interface RuntimeSkillAnalysis {
  status: 'fresh' | 'stale' | 'missing' | 'failed';
  body_hash: string | null;
  analyzer_version: string | null;
  analyzed_at: string | null;
  source: 'llm' | 'heuristic' | null;
  summary: string;
  confidence: SkillAnalysisConfidence | null;
  procedural_steps: {
    title: string;
    description?: string;
    confidence?: SkillAnalysisConfidence;
    evidence?: string;
  }[];
  tool_mentions: {
    name: string;
    kind: 'observed' | 'declared' | 'mentioned';
    confidence?: SkillAnalysisConfidence;
    basis?: string;
    evidence?: string;
  }[];
  warnings: string[];
}

/** GET /skills/{id}/history의 버전 스냅샷 payload. */
export interface RuntimeSkillVersion {
  version: number;
  updated_at: string;
  /** frontmatter를 포함한 전체 SKILL.md 원문. */
  body: string;
}

/** 런타임 분석 payload → 프론트 SkillAnalysis (snake→camel). */
export function runtimeAnalysisToSkillAnalysis(raw: RuntimeSkillAnalysis): SkillAnalysis {
  return {
    status: raw.status,
    bodyHash: raw.body_hash ?? undefined,
    analyzerVersion: raw.analyzer_version ?? undefined,
    analyzedAt: raw.analyzed_at ?? undefined,
    source: raw.source ?? undefined,
    summary: raw.summary || undefined,
    confidence: raw.confidence ?? undefined,
    proceduralSteps: raw.procedural_steps ?? [],
    toolMentions: raw.tool_mentions ?? [],
    warnings: raw.warnings ?? [],
  };
}

/**
 * SkillDetail.createdFrom / lastUsed 에 저장되는 stable code.
 * 렌더 헬퍼 renderCreatedFrom(code, t) / renderLastUsed(code, t) 로 번역한다.
 */
export type CreatedFromCode =
  | 'created_from_chat'
  | 'created_from_chat_auto'
  | 'created_from_loop'
  | 'created_from_patch'
  | 'created_from_builtin';

export type LastUsedCode =
  | 'last_used_just_now'
  | 'last_used_never'
  | `last_used_iso:${string}`;

export interface SkillDetail {
  summary: string;
  /** stable CreatedFromCode 또는 레거시 자유 문자열 — renderCreatedFrom()으로 렌더 */
  createdFrom: string;
  /** stable LastUsedCode 또는 ISO 날짜 또는 레거시 자유 문자열 — renderLastUsed()으로 렌더 */
  lastUsed: string;
  shared: boolean;
  rules: SkillRule[];
  versions: SkillVersion[];
  /** 런타임 히스토리 조회 상태. undefined면 마켓/로컬 전용 detail이다. */
  historyStatus?: 'idle' | 'loading' | 'loaded' | 'error';
  historyError?: string;
  usage: SkillUsage[];
  permissions: SkillPermission[];
  /** 워크플로형(절차적) 스킬의 단계 — kind==='workflow' 일 때만 채워짐 */
  steps?: SkillStep[];
  /** 성과 요약 — 헤더 성과 스트립 */
  health?: SkillHealth;
  /** 최근 활동 피드 */
  activity?: SkillActivity[];
  /** SKILL.md explanatory sidecar generated by the analyzer. */
  analysis?: SkillAnalysis;
  /**
   * 스킬의 실제 소스 파일 (SKILL.md·scripts…). 설치·제작 스킬은 실제 파일을 들고
   * 오고, 학습 스킬은 없을 수 있다(소스 뷰에서 학습 데이터로 합성 — ownedSkillFiles).
   */
  files?: SkillFile[];
}

// ── Chat thread ─────────────────────────────────────────────────
export interface ToolCallScope {
  name: string;
  mode: string;
}

export interface ToolCall {
  tool: string;
  label: string;
  status: 'done' | 'running' | 'error';
  time: string;
  params: Record<string, string>;
  result: string;
  scope?: ToolCallScope;
}

export interface SkillRef {
  id: string;
  label: string;
  ver: string;
}

export interface ThinkingStep {
  label: string;
  detail?: string;
  /** 이 단계에서 호출한 도구 (있으면 mono 칩으로 표시) */
  tool?: string;
}

/** one line of a skill-body diff (patch proposal card) */
export interface PatchDiffLine {
  t: 'add' | 'remove' | 'ctx';
  text: string;
}

/**
 * one row of a side-by-side file-edit diff (tool_edit 카드). 좌측 before / 우측 after.
 * 'add' = 순수 추가(좌 비움) · 'remove' = 순수 삭제(우 비움) · 'change' = 양쪽 수정 · 'same' = 문맥.
 */
export interface EditDiffRow {
  t: 'same' | 'change' | 'add' | 'remove';
  before?: string;
  after?: string;
}

export type ThreadMessage =
  | { kind: 'user'; text: string; sentAt?: number }
  | {
      kind: 'thinking';
      duration: string;
      /** structured step trace (legacy/scripted form) */
      steps?: ThinkingStep[];
      /** free-form reasoning streamed from a thinking model (e.g. qwen `message.thinking`) */
      text?: string;
      /** still streaming → header shows a live "생각 중…" affordance and stays open */
      streaming?: boolean;
      /** epoch ms when this block started — used to stamp `duration` on finalize */
      startedAt?: number;
    }
  | {
      // plan-and-execute × ReAct 턴 계획 카드 (RICE_AGENT_EXECUTION.md §3).
      // SSE plan_updated가 올 때마다 planId가 같은 카드를 스냅샷으로 통째 교체하고,
      // 리로드 시엔 메시지 metadata.plan(마지막 스냅샷 승리)으로 복원한다.
      kind: 'plan';
      planId: string;
      /** 해석된 사용자 의도 한 문장 (대화 언어) */
      goal: string;
      status: 'active' | 'completed' | 'abandoned';
      revision: number;
      steps: PlanStepData[];
    }
  | {
      // generic builtin/connector tool call trace (live SSE: tool_call → tool_result)
      kind: 'toolcall';
      /** matches the runtime ToolResult.tool_call_id so the result can update this card */
      callId: string;
      tool: string;
      args: Record<string, unknown>;
      status: 'running' | 'done' | 'error';
      /**
       * 이 행동에 앞서 모델이 사용자에게 남긴 한 줄 "왜"(#1 rationale primitive).
       * 도구 호출 직전의 어시스턴트 본문(interstitial text)에서 온다 — 접히는 추론과
       * 달리 항상 보이며, 카드에 캡션으로 붙어 "생성·읽기·생성"의 반복을 스스로
       * 검토·개선하는 서사로 읽히게 한다. 없으면(모델이 나레이션 안 함) 캡션 생략.
       */
      rationale?: string;
      /** rendered result preview (on ok) */
      result?: string;
      /** error text (on failure / skip) */
      error?: string;
    }
  | {
      kind: 'agent';
      text: string;
      /** current answer is still receiving/draining streamed tokens */
      streaming?: boolean;
      bullets?: string[];
      usedSkill?: SkillRef;
      action?: { label: string; icon: IconName };
      /** epoch ms — stamped when the answer first arrives (live) or restored from DB */
      sentAt?: number;
    }
  | { kind: 'memory'; text: string; facts: string[]; target?: { kind: 'skill'; id: string; label: string } }
  | {
      kind: 'approval';
      /** parking id — POST /sessions/{id}/tool-approval 가 이 값을 실어 응답 (CONTRACTS 1.2) */
      approvalId: string;
      tool: string;
      label: string;
      scope: ToolCallScope;
      reason: string;
      /** 승인 전에 사용자가 검토할 실행 내용 (예: 메일 받는사람·제목) */
      fields?: { label: string; value: string }[];
      /** 전체 본문 미리보기 (예: 메일 본문) */
      body?: string;
      /** 승인/거부 후 표시할 동적 결과 문구 (도구별로 클라이언트에서 생성, 레이턴시 0) */
      approvedText?: string;
      deniedText?: string;
    }
  | {
      // skill_patch_proposed 제어 이벤트의 화면 표현 — diff 카드 (LEARNING_LOOP 5.2.3)
      kind: 'patch';
      /** parking id — POST /skills/{id}/patch 가 이 값을 실어 응답 (CONTRACTS 1.2) */
      approvalId: string;
      skillId: string;
      skillName: string;
      fromVer: string;
      toVer: string;
      /** 한 줄 changelog */
      changelog: string;
      /** 무엇이 제안을 일으켰는가 (감사용) */
      trigger: PatchTrigger;
      triggerLabel: string;
      /** 현재 version 대비 본문 diff (UI 렌더용) */
      diff: PatchDiffLine[];
      /** 승인 전 편집 가능한 개선된 전체 본문 */
      proposedBody: string;
      /** 승인과 함께 저장될 메타데이터. 생성은 description/tags, 기존 스킬
       * 개선은 newDescription/newTags를 사용한다. */
      isCreation: boolean;
      description?: string;
      tags?: string[];
      newDescription?: string;
      newTags?: string[];
      /** 승인 시 My Skills 히스토리에 보관될 이 버전의 규칙 스냅샷 */
      newRules?: SkillVersionRule[];
      /** 적용 후 사용처에 남길 한 줄 (선택) */
      note?: string;
      /** Phase D 가드 경고 코드 (예: "masked:AWS access key" — learningWarnings.ts가 로케일 문장으로 푼다) — 비어있으면 표시 안 함 */
      warnings?: string[];
    }
  | {
      kind: 'toolrun';
      id?: string;
      label: string;
      status: 'done' | 'running';
      steps: number;
      time: string;
      open?: boolean;
      usedSkill?: SkillRef;
      reuse?: { fromSteps: number; toSteps: number; fromTime: string; toTime: string };
      calls: ToolCall[];
    }
  // ── 에이전트 실행 질감(thinking ↔ tool ↔ 설명 인터리빙)을 위한 낱낱 도구 카드 ──
  // 추상 묶음(toolrun)과 달리, 실제 transcript처럼 한 동작 = 한 카드로 보여준다.
  | {
      // 셸 명령 실행 — IN(명령)/OUT(출력) 카드
      kind: 'tool_bash';
      /** 카드 제목 — 무엇을 위한 명령인지 한 줄 */
      desc: string;
      /** IN — 실행한 셸 명령 (mono) */
      cmd: string;
      /** OUT — 출력 (mono, 여러 줄 가능). 생략 시 실행만 표시 */
      out?: string;
      status?: 'done' | 'running' | 'error';
      /** 출력이 길면 기본 접힘 + "출력 보기" */
      collapsed?: boolean;
    }
  | {
      // 파일/지식 읽기 — 파일명 + 한 줄 요약
      kind: 'tool_read';
      /** 읽은 파일 경로 또는 지식 소스 */
      file: string;
      /** 무엇을 읽고 무엇을 확인했는지 한 줄 */
      summary: string;
      /** 라인 수·문서 수 등 메타 (선택) */
      meta?: string;
    }
  | {
      // 파일 편집 — before/after split diff 카드
      kind: 'tool_edit';
      /** 편집한 파일 경로 */
      file: string;
      /** "N줄 추가/변경" 같은 요약 */
      summary: string;
      /** 좌(before)/우(after) split diff 라인들 */
      diff: EditDiffRow[];
      /** 길면 접고 "펼치기"로 확장 */
      collapsed?: boolean;
    }
  | {
      // 결과 비교 표 (chat3 의 "결과 (모드별 동작)")
      kind: 'table';
      /** 표 제목 (선택) */
      caption?: string;
      columns: string[];
      rows: string[][];
    }
  | {
      // 백그라운드 셸 작업 카드 (AGENT_EXECUTION §11) — terminal 이 background 로 뜨거나
      // auto-background 로 승격된, 턴을 넘겨 사는 명령. 라이브 SSE(task_started →
      // task_completed) + 폴링(GET /sessions/{id}/tasks)으로 갱신되고, 리로드 시엔
      // synthetic task_completed 메시지로 복원된다. 도구 카드(한 라운드 내 완결)와
      // 달리 running 상태로 턴 경계를 넘을 수 있고 사용자가 kill 할 수 있다.
      kind: 'task';
      /** runtime task_id — SSE·폴링·kill 이 이 값으로 카드를 찾는다(위치 아닌 키) */
      taskId: string;
      /** 실행한 셸 명령 */
      command: string;
      status: BackgroundTaskStatus;
      /** 종료 코드 (exited 일 때 유효; running 이면 undefined) */
      exitCode?: number | null;
      /** 출력 (라이브 폴링·완료 시 채워짐, 40KB 상한). undefined = 아직 없음 */
      output?: string;
      /** 긴 출력 기본 접힘 */
      collapsed?: boolean;
    };

// Chat history (sessions + threads) is no longer mocked here - it lives in
// state.db and is loaded over the runtime API (GET /sessions, /messages).
// To (re)seed the demo conversations, run:
//   python rice-runtime/scripts/seed_demo_sessions.py
// ── Learning-loop proposals (pending human review) ───────────────
// 루프가 관찰에서 만들어낸 "확정 전" 제안들. 패널에서 수락하면 실제로
// 규칙·스킬 상태가 바뀐다 (관찰 → 제안 → 적용). 확정된 학습은 SESSION_DELTAS.
export type ProposalKind = 'rule' | 'tool' | 'prune';

/** 제안의 출처 — 어느 대화에서 관찰됐는지. 카드에서 클릭하면 그 대화로 점프한다. */
export interface ProposalSource {
  conversationId: string;
  title: string;
  /** 관찰 횟수 등 근거가 되는 수치 */
  count?: number;
  /** 출처 대화에서 스크롤·하이라이트할 메시지의 인덱스 (로드된 thread 기준) */
  messageIndex?: number;
}

export interface Proposal {
  id: string;
  kind: ProposalKind;
  /** glanceable 한 줄 제목 */
  title: string;
  /** 루프가 이 제안을 띄운 근거(관찰) — 실제 대화 내용에 기반해야 한다 */
  evidence: string;
  /** 근거가 관찰된 대화 (추적 가능하게 링크) */
  source?: ProposalSource;
  /** 대상 스킬 (rule 승격 · prune 정리) */
  targetSkillId?: string;
  /** 추가할 규칙 텍스트 (kind:'rule') */
  rule?: string;
  /** 저장할 새 원자적 도구 (kind:'tool') */
  tool?: { name: string; command: string; summary: string };
  /** 수락 버튼 라벨 */
  acceptLabel: string;
}

// 근거는 모두 시드된 대화(seed_c1…, state.db)의 실제 내용에 기반한다 (출처 대화에서 직접 확인 가능):
//  p1 — c1 매출 요약 불릿에 'watsonx.ai 라인 기여도'가 2번 등장 (아직 규칙 아님)
//  p2 — c1·c2·c3 모두 Db2 조회로 분석을 시작 (반복 패턴 → 도구화)
export interface LoopStage {
  key: string;
  label: string;
  icon: IconName;
  desc: string;
}

export const LOOP_STAGES: LoopStage[] = [
  {
    key: 'exp', icon: 'idea',
    get label() { return tAny('connectors:loopStages.exp.label') as string; },
    get desc() { return tAny('connectors:loopStages.exp.desc') as string; },
  },
  {
    key: 'gen', icon: 'tools',
    get label() { return tAny('connectors:loopStages.gen.label') as string; },
    get desc() { return tAny('connectors:loopStages.gen.desc') as string; },
  },
  {
    key: 'use', icon: 'renew',
    get label() { return tAny('connectors:loopStages.use.label') as string; },
    get desc() { return tAny('connectors:loopStages.use.desc') as string; },
  },
  {
    key: 'model', icon: 'user',
    get label() { return tAny('connectors:loopStages.model.label') as string; },
    get desc() { return tAny('connectors:loopStages.model.desc') as string; },
  },
];

// ── Learning Impact (eval results — RICE_LEARNING_EVAL 6.3) ──────
// 제품 thesis "쓸수록 똑똑해짐"을 숫자로 증명하는 패널의 데이터. 디자인
// 단계엔 이 시드 fixture로, 런타임 완성 후엔 harness 실측(eval/results/
// latest.json, EVAL 6.4)으로 동일 스키마를 drop-in 교체한다.
// warm.skills_used의 @vN은 patch로 개선된 버전을 가리킨다 — 라이브 데모의
// 「분기 매출 요약」 v3→v4 개선(ACT 2.5)과 정량/정성이 같은 스킬을 향한다.
/**
 * IMPACT_REPORT — demo fixture. title과 skills_used를 렌더 시점에 번역한다.
 * ImpactReport를 그대로 구현하되, title/skills_used는 Proxy-like getter를 피하고
 * buildImpactReport() 팩토리로 현재 언어 기준 객체를 생성한다.
 * MySkillsScreen은 언어 변경 시 재렌더되므로 re-render마다 최신 문자열을 읽게 된다.
 */
function buildImpactReport(): ImpactReport {
  const ta = (key: string) => tAny(key) as string;
  return {
    task_suite: 'demo-v1',
    model: 'claude-sonnet-4-5',
    runs_per_task: 5,
    generated_at: '2026-06-22T10:00:00Z',
    tasks: [
      {
        id: 'T-A',
        title: ta('chat:impact.taskTitle.T-A'),
        cold: { turns: 5, tool_calls: 12, success_rate: 0.8, skill_hit: false },
        warm: { turns: 2, tool_calls: 5, success_rate: 1.0, skill_hit: true,
          skills_used: [ta('chat:impact.taskTitle.T-A') + '@v4'] },
        delta: { turns_pct: -0.6, tool_calls_pct: -0.58, success_pp: 0.2 },
      },
      {
        id: 'T-B',
        title: ta('chat:impact.taskTitle.T-B'),
        cold: { turns: 4, tool_calls: 9, success_rate: 0.8, skill_hit: false },
        warm: { turns: 2, tool_calls: 4, success_rate: 1.0, skill_hit: true,
          skills_used: [ta('chat:impact.taskTitle.T-B') + '@v2'] },
        delta: { turns_pct: -0.5, tool_calls_pct: -0.56, success_pp: 0.2 },
      },
      {
        id: 'T-C',
        title: ta('chat:impact.taskTitle.T-C'),
        cold: { turns: 6, tool_calls: 11, success_rate: 0.6, skill_hit: false },
        warm: { turns: 3, tool_calls: 6, success_rate: 0.8, skill_hit: true,
          skills_used: [ta('chat:impact.taskTitle.T-C') + '@v1'] },
        delta: { turns_pct: -0.5, tool_calls_pct: -0.45, success_pp: 0.2 },
      },
    ],
    aggregate: { turns_pct: -0.54, tool_calls_pct: -0.53, success_pp: 0.2, skill_hit_rate: 1.0 },
  };
}

/** demo fixture — 렌더마다 현재 언어로 title/skills_used를 갱신한다. */
export const IMPACT_REPORT: ImpactReport = new Proxy({} as ImpactReport, {
  get(_target, prop) {
    return buildImpactReport()[prop as keyof ImpactReport];
  },
});

// ── My Plugins (installed plugin management screen) ──────────────
export interface PluginSkill {
  name: string;
  desc: string;
}

export interface PluginKnowledge {
  name: string;
  desc: string;
  meta: string;
}

export interface PluginPermission {
  name: string;
  mode: string;
}

export interface PluginUpdate {
  version: string;
  note: string;
}

export interface MyPlugin {
  id: string;
  name: string;
  icon: IconName;
  enabled: boolean;
  /** 앱과 함께 제공되는 읽기 전용 플러그인 — 활성/비활성만 가능. */
  builtin?: boolean;
  source: string;
  version: string;
  author: string;
  updated: string;
  summary: string;
  skills: PluginSkill[];
  knowledge: PluginKnowledge[];
  permissions: PluginPermission[];
  examples: string[];
  /** 제공사가 지식·스킬을 갱신했을 때 표시되는 사용 가능한 새 버전 */
  update?: PluginUpdate;
  /**
   * 설치 시 위험 스캔 결과 — "<skill_name>:<label>" (라벨은 scan_risks 안정 코드,
   * localizeRiskLabel이 로케일 문장으로 푼다). 비어있으면 깨끗. 플래그가 있으면
   * 런타임이 비활성으로 설치하며, 활성화는 사용자 판단.
   */
  riskFlags?: string[];
  /** 플러그인 agents/*.md 페르소나 이름 — 모델이 위임 작업에서 전문 워커로 쓴다. */
  agentNames?: string[];
}

// ── Connectors (linked external accounts / data sources) ─────────
/**
 * 커넥터가 노출하는 각 도구를 에이전트가 언제 호출할 수 있는지에 대한 정책.
 * 도구 자체는 제공자가 정의하므로 사용자가 임의로 추가/삭제하지 않고, "언제 쓸 수
 * 있는가"만 설정한다(실제 Claude 커넥터 권한 모델과 동일).
 *
 * stable code — UI 라벨은 connectors.detail.permAllow/permApproval/permBlock 으로 번역.
 */
export type ScopePermission = 'allow' | 'approval' | 'block';

export interface ConnectorScope {
  name: string;
  /**
   * stable mode code — UI 그룹 헤더 라벨은 connectors.detail.toolGroupRead/Write/Exec 으로 번역.
   * 'read' = 읽기 전용 도구, 'write' = 쓰기·삭제 도구, 'exec' = 실행 도구.
   */
  mode: 'read' | 'write' | 'exec';
  /** 생략 시 mode로 추정: read→'allow', 그 외→'approval' */
  permission?: ScopePermission;
  /** 원격 MCP 서버가 tools/list 로 제공한 도구 설명 — 사용자가 무슨 도구인지 판단하는 근거 */
  description?: string;
}

export interface ConnectorUser {
  kind: string;
  name: string;
  icon: IconName;
}

export interface McpEnvVar {
  key: string;
  value: string;
}

/**
 * How a local MCP server is launched / reached. `stdio` runs a command on this
 * machine (arbitrary local code execution — treat as developer-only); `http`
 * points at a locally-running MCP endpoint.
 */
export type McpTransport =
  | { type: 'stdio'; command: string; args: string[]; env?: McpEnvVar[] }
  | { type: 'http'; endpoint: string };

export interface Connector {
  id: string;
  name: string;
  icon: IconName;
  category: string;
  provider: string;
  /**
   * 'remote' = OAuth/큐레이팅된 외부 통합(기본). 'local-mcp' = 내 기기에서 실행·연결되는
   * 로컬 MCP 서버(개발자). 생략 시 'remote'로 취급. {@link connectorKind} 참고.
   */
  kind?: 'remote' | 'local-mcp';
  /**
   * 백엔드 registry 의 connector_type(예: 'gmail'/'calendar'/'drive') — 실제(온라인) 원격
   * 커넥터에만 있다. connect/재연결·polling 시 어떤 타입으로 OAuth 를 시작할지 식별하는 데 쓴다.
   */
  connectorType?: string;
  status: 'connected' | 'disconnected';
  account?: string;
  lastSync?: string;
  summary: string;
  /** local-mcp일 때 실행/연결 방식 */
  transport?: McpTransport;
  scopes: ConnectorScope[];
  usedBy: ConnectorUser[];
  examples: string[];
  /** 제공사가 도구·권한을 갱신했을 때 표시되는 사용 가능한 업데이트 */
  update?: ConnectorUpdate;
  /** 토큰 만료·취소 등으로 자격증명이 무효해져 재연결이 필요한 상태(백엔드 status='error') */
  errored?: boolean;
  /** 비개발자용 한 줄 요약(로케일) — 화면의 기본 오류 문구. */
  errorDetail?: string;
  /** 복구 동작 분류 — reconnect(OAuth 재연결), approval(제공자 승인 대기, 재연결 무의미),
   *  check(원인 미상, 재검증 우선). 상태 태그·버튼 라벨이 이 값으로 갈린다. */
  errorKind?: 'reconnect' | 'approval' | 'check';
  /** 런타임이 기록한 원문 진단(영문, error_code 포함) — 접힌 "기술 정보"에서만 노출. */
  errorTechnical?: string;
  setupUrl?: string;
  /**
   * 백엔드 connectors 테이블에 실제 레코드가 있는 커넥터(GET /connectors 유래) — true 면
   * 미연결 상태에서도 "제거"(DELETE /connectors/{id})가 가능하다. 연결 가능 프리셋 카드·
   * 목업에는 지울 레코드가 없으므로 이 플래그가 없다.
   */
  persisted?: boolean;
}

/**
 * 제공사(또는 MCP 서버)가 도구·권한을 갱신했을 때 표시되는 사용 가능한 업데이트.
 * 적용하면 {@link addsTools}가 커넥터의 도구 목록에 합쳐진다.
 *
 * 번역 원칙: version/note를 직접 저장하지 않고 diff 수치만 저장해 렌더 시 번역한다.
 */
export interface ConnectorUpdate {
  /** 추가된 도구 수 (렌더 시 connectors.detail.update.added 키로 번역) */
  addedCount?: number;
  /** 제거된 도구 수 (렌더 시 connectors.detail.update.removed 키로 번역) */
  removedCount?: number;
  /** 이 업데이트로 새로 노출되는 도구 (적용 시 scopes에 추가) */
  addsTools?: ConnectorScope[];
}

/** 커넥터 구분을 정규화 (kind 미지정 = 원격 커넥터) */
export const connectorKind = (c: Connector): 'remote' | 'local-mcp' => c.kind ?? 'remote';

// ── fixture 헬퍼 (렌더 시점에 카탈로그를 읽는 usedBy 생성) ──────────────────
// ConnectorUser.kind와 name은 fixture.usedByKind / fixture.<id>.usedBy에서 온다.
// tAny를 사용해 동적 키 + returnObjects 타입 오류를 피한다 (apiError.ts 동일 패턴).
function fixtureUsedBy(id: string): ConnectorUser[] {
  const items = tAny(`connectors:fixture.${id}.usedBy`, { returnObjects: true }) as Array<{ kind: string; name: string }>;
  if (!Array.isArray(items)) return [];
  const kindIconMap: Record<string, IconName> = {
    skill: 'skill', plugin: 'database', automation: 'flow', conversation: 'chat',
  };
  return items.map((u) => ({
    kind: tAny(`connectors:fixture.usedByKind.${u.kind}`) as string || u.kind,
    name: u.name,
    icon: kindIconMap[u.kind] ?? 'skill',
  }));
}

function fixtureScopes(id: string, modes: Array<'read' | 'write' | 'exec'>, permissions?: Array<ScopePermission | undefined>): ConnectorScope[] {
  const names = tAny(`connectors:fixture.${id}.scopes`, { returnObjects: true }) as string[];
  if (!Array.isArray(names)) return [];
  return names.map((name, idx) => ({
    name,
    mode: modes[idx] ?? 'read',
    ...(permissions?.[idx] ? { permission: permissions[idx] } : {}),
  }));
}

function fixtureExamples(id: string): string[] {
  const items = tAny(`connectors:fixture.${id}.examples`, { returnObjects: true }) as string[];
  return Array.isArray(items) ? items : [];
}

export const CONNECTORS: Connector[] = [
  {
    id: 'db2',
    name: 'Db2 (prod)',
    icon: 'database',
    get category() { return tAny('connectors:fixture.db2.category'); },
    provider: 'IBM Db2',
    status: 'connected',
    account: 'db2-prod.ibm.internal',
    get lastSync() { return tAny('connectors:fixture.lastSync.justNow'); },
    get summary() { return tAny('connectors:fixture.db2.summary'); },
    get scopes() {
      return fixtureScopes('db2',
        ['read', 'read', 'read', 'read', 'write'],
        [undefined, undefined, undefined, undefined, 'approval'],
      );
    },
    get usedBy() { return fixtureUsedBy('db2'); },
    get examples() { return fixtureExamples('db2'); },
  },
  {
    id: 'slack',
    name: 'Slack',
    icon: 'chat',
    get category() { return tAny('connectors:fixture.slack.category'); },
    provider: 'Slack',
    status: 'connected',
    account: 'ibm-data-platform.slack.com',
    get lastSync() { return tAny('connectors:fixture.lastSync.min10Ago'); },
    get summary() { return tAny('connectors:fixture.slack.summary'); },
    get scopes() {
      return fixtureScopes('slack',
        ['read', 'read', 'read', 'read', 'write', 'write', 'write'],
      );
    },
    get usedBy() { return fixtureUsedBy('slack'); },
    get examples() { return fixtureExamples('slack'); },
  },
  {
    id: 'outlook',
    name: 'Outlook',
    icon: 'send',
    get category() { return tAny('connectors:fixture.outlook.category'); },
    provider: 'Microsoft 365',
    status: 'connected',
    account: 'jaeho.seo@ibm.com',
    get lastSync() { return tAny('connectors:fixture.lastSync.hour1Ago'); },
    get summary() { return tAny('connectors:fixture.outlook.summary'); },
    get scopes() {
      return fixtureScopes('outlook',
        ['read', 'read', 'read', 'write', 'write', 'write'],
        [undefined, undefined, undefined, undefined, 'approval', undefined],
      );
    },
    get usedBy() { return fixtureUsedBy('outlook'); },
    get examples() { return fixtureExamples('outlook'); },
  },
  {
    id: 'jira',
    name: 'Jira',
    icon: 'rule',
    get category() { return tAny('connectors:fixture.jira.category'); },
    provider: 'Atlassian',
    status: 'disconnected',
    get summary() { return tAny('connectors:fixture.jira.summary'); },
    get scopes() {
      return fixtureScopes('jira',
        ['read', 'read', 'read', 'write', 'write', 'write'],
      );
    },
    get usedBy() { return fixtureUsedBy('jira'); },
    get examples() { return fixtureExamples('jira'); },
  },
  {
    id: 'gdrive',
    name: 'Google Drive',
    icon: 'cloud',
    get category() { return tAny('connectors:fixture.gdrive.category'); },
    provider: 'Google',
    status: 'disconnected',
    get summary() { return tAny('connectors:fixture.gdrive.summary'); },
    get scopes() {
      return fixtureScopes('gdrive',
        ['read', 'read', 'read', 'write', 'write'],
      );
    },
    usedBy: [],
    get examples() { return fixtureExamples('gdrive'); },
  },
  {
    id: 'orchestrate',
    name: 'watsonx Orchestrate',
    icon: 'flow',
    get category() { return tAny('connectors:fixture.orchestrate.category'); },
    provider: 'IBM watsonx',
    status: 'connected',
    account: 'orchestrate.ibm.com',
    get lastSync() { return tAny('connectors:fixture.lastSync.yesterday'); },
    get summary() { return tAny('connectors:fixture.orchestrate.summary'); },
    get scopes() {
      return fixtureScopes('orchestrate',
        ['read', 'read', 'exec', 'exec'],
        [undefined, undefined, 'approval', 'approval'],
      );
    },
    get usedBy() { return fixtureUsedBy('orchestrate'); },
    get examples() { return fixtureExamples('orchestrate'); },
  },
  {
    id: 'gmail',
    name: 'Gmail',
    icon: 'send',
    get category() { return tAny('connectors:fixture.gmail.category'); },
    provider: 'Google',
    status: 'connected',
    account: 'jaeho.seo@gmail.com',
    get lastSync() { return tAny('connectors:fixture.lastSync.min5Ago'); },
    get summary() { return tAny('connectors:fixture.gmail.summary'); },
    // 실제 Google Gmail 커넥터가 노출하는 도구 (읽기 3 · 쓰기/삭제 9) — scope name은 런타임 API 문자열이므로 번역 대상 아님
    scopes: [
      { name: "Retrieves a specific email thread from the authenticated user's Gmail account.", mode: 'read' },
      { name: 'Lists user labels.', mode: 'read' },
      { name: "Searches for email threads from the authenticated user's Gmail account.", mode: 'read' },
      { name: "Creates a new draft email in the authenticated user's Gmail account.", mode: 'write' },
      { name: 'Creates a new label.', mode: 'write' },
      { name: 'Deletes a label.', mode: 'write', permission: 'approval' },
      { name: 'Adds labels to a message.', mode: 'write' },
      { name: 'Adds labels to a thread.', mode: 'write' },
      { name: "Lists draft emails from the authenticated user's Gmail account.", mode: 'write' },
      { name: 'Removes labels from a message.', mode: 'write' },
      { name: 'Removes labels from a thread.', mode: 'write' },
      { name: 'Modifies an existing label.', mode: 'write' },
    ],
    get usedBy() { return fixtureUsedBy('gmail'); },
    get examples() { return fixtureExamples('gmail'); },
    update: {
      addedCount: 2,
      removedCount: 0,
      addsTools: [
        { name: 'Sends an email message.', mode: 'write', permission: 'approval' },
        { name: 'Moves a message to trash.', mode: 'write', permission: 'approval' },
      ],
    },
  },

  // ── 로컬 MCP 서버 (개발자) — 내 기기에서 직접 실행/연결 ──────────────
  {
    id: 'mcp-filesystem',
    name: 'Filesystem',
    icon: 'server',
    get category() { return tAny('connectors:fixture.mcp-filesystem.category'); },
    get provider() { return tAny('connectors:meta.localStdioProvider'); },
    kind: 'local-mcp',
    status: 'connected',
    account: 'npx @modelcontextprotocol/server-filesystem',
    get lastSync() { return tAny('connectors:fixture.lastSync.justNow'); },
    get summary() { return tAny('connectors:fixture.mcp-filesystem.summary'); },
    transport: {
      type: 'stdio',
      command: 'npx',
      args: ['-y', '@modelcontextprotocol/server-filesystem', '/Users/jaehoseo/Desktop'],
    },
    // MCP tool names은 코드 식별자이므로 번역 대상 아님
    scopes: [
      { name: 'read_file', mode: 'read' },
      { name: 'write_file', mode: 'write' },
      { name: 'list_directory', mode: 'read' },
    ],
    get usedBy() { return fixtureUsedBy('mcp-filesystem'); },
    get examples() { return fixtureExamples('mcp-filesystem'); },
    update: {
      addedCount: 2,
      removedCount: 0,
      addsTools: [
        { name: 'move_file', mode: 'write', permission: 'approval' },
        { name: 'search_files', mode: 'read' },
      ],
    },
  },
  {
    id: 'mcp-git',
    name: 'Git',
    icon: 'tools',
    get category() { return tAny('connectors:fixture.mcp-git.category'); },
    get provider() { return tAny('connectors:meta.localStdioProvider'); },
    kind: 'local-mcp',
    status: 'connected',
    account: 'uvx mcp-server-git',
    get lastSync() { return tAny('connectors:fixture.lastSync.min2Ago'); },
    get summary() { return tAny('connectors:fixture.mcp-git.summary'); },
    transport: {
      type: 'stdio',
      command: 'uvx',
      args: ['mcp-server-git', '--repository', '/Users/jaehoseo/Desktop/vswrk/bob/rice'],
    },
    // MCP tool names은 코드 식별자이므로 번역 대상 아님
    scopes: [
      { name: 'git_log', mode: 'read' },
      { name: 'git_diff', mode: 'read' },
      { name: 'git_status', mode: 'read' },
    ],
    get usedBy() { return fixtureUsedBy('mcp-git'); },
    get examples() { return fixtureExamples('mcp-git'); },
  },
  {
    id: 'mcp-sqlite',
    name: 'SQLite',
    icon: 'database',
    get category() { return tAny('connectors:fixture.mcp-sqlite.category'); },
    get provider() { return tAny('connectors:meta.localStdioProvider'); },
    kind: 'local-mcp',
    status: 'disconnected',
    get summary() { return tAny('connectors:fixture.mcp-sqlite.summary'); },
    transport: {
      type: 'stdio',
      command: 'uvx',
      args: ['mcp-server-sqlite', '--db-path', './data.db'],
    },
    scopes: [],
    usedBy: [],
    get examples() { return fixtureExamples('mcp-sqlite'); },
  },
];

/** maps a tool scope / permission name to the connector that owns it (or null) */
export function connectorIdForScope(scope: string): string | null {
  const s = scope.toLowerCase();
  if (s.includes('gmail') || s.includes('send email') || s.includes('inbox') || s.includes('draft email')) return 'gmail';
  if (s.includes('calendar') || s.includes('event') || s.includes('schedule')) return 'calendar';
  if (s.startsWith('db2')) return 'db2';
  if (s.includes('orchestrate') || s.includes('workflow')) return 'orchestrate';
  if (s.startsWith('#') || s.includes('slack') || s.includes('mention') || s.includes('channel')) return 'slack';
  if (s.includes('outlook') || s.includes('mail') || s.includes('appointment')) return 'outlook';
  if (s.includes('drive')) return 'gdrive';
  if (s.includes('jira')) return 'jira';
  return null;
}

// ── Connector directory (추가 가능한 원격 커넥터 카탈로그) ──────────
/** 커넥터 카탈로그 카드에 표시하는 뱃지 — stable code, 번역은 렌더 시 connectors:badge.<code> 로 */
export type BadgeCode = 'popular' | 'new' | 'recommended';

export interface CatalogConnector {
  id: string;
  name: string;
  icon: IconName;
  category: string;
  provider: string;
  summary: string;
  /** stable badge codes — 렌더 시 t('connectors:badge.<code>') 로 번역 */
  badges?: BadgeCode[];
  examples?: string[];
  /**
   * true = 아직 백엔드 registry(connector/registry.py)에 실제로 연결되지 않은 항목 —
   * "둘러보기"에서는 보여주되 바로 추가/연결은 막고 커스텀 추가로 안내한다. 실제 지원이
   * 붙으면(디스크립터 추가) 이 플래그를 지운다. 지금은 카탈로그 전부가 이 상태다.
   */
  comingSoon?: boolean;
}

export const CONNECTOR_CATALOG: CatalogConnector[] = [
  {
    id: 'github',
    name: 'GitHub',
    icon: 'tools',
    get category() { return tAny('connectors:catalog.category.dev'); },
    provider: 'GitHub',
    get summary() { return tAny('connectors:fixture.catalog.github.summary'); },
    badges: ['popular'],
    get examples() { return fixtureExamples('catalog.github' as string); },
    comingSoon: true,
  },
  {
    id: 'notion',
    name: 'Notion',
    icon: 'bookmark',
    get category() { return tAny('connectors:catalog.category.docs'); },
    provider: 'Notion',
    get summary() { return tAny('connectors:fixture.catalog.notion.summary'); },
    badges: ['popular'],
    get examples() { return fixtureExamples('catalog.notion' as string); },
    comingSoon: true,
  },
  {
    id: 'linear',
    name: 'Linear',
    icon: 'flow',
    get category() { return tAny('connectors:catalog.category.issueTracker'); },
    provider: 'Linear',
    get summary() { return tAny('connectors:fixture.catalog.linear.summary'); },
    badges: ['new'],
    get examples() { return fixtureExamples('catalog.linear' as string); },
    comingSoon: true,
  },
  {
    id: 'confluence',
    name: 'Confluence',
    icon: 'rule',
    get category() { return tAny('connectors:catalog.category.docs'); },
    provider: 'Atlassian',
    get summary() { return tAny('connectors:fixture.catalog.confluence.summary'); },
    get examples() { return fixtureExamples('catalog.confluence' as string); },
    comingSoon: true,
  },
  {
    id: 'salesforce',
    name: 'Salesforce',
    icon: 'growth',
    get category() { return tAny('connectors:catalog.category.crm'); },
    provider: 'Salesforce',
    get summary() { return tAny('connectors:fixture.catalog.salesforce.summary'); },
    badges: ['recommended'],
    get examples() { return fixtureExamples('catalog.salesforce' as string); },
    comingSoon: true,
  },
  {
    id: 'hubspot',
    name: 'HubSpot',
    icon: 'growth',
    get category() { return tAny('connectors:catalog.category.marketingCrm'); },
    provider: 'HubSpot',
    get summary() { return tAny('connectors:fixture.catalog.hubspot.summary'); },
    get examples() { return fixtureExamples('catalog.hubspot' as string); },
    comingSoon: true,
  },
  {
    id: 'asana',
    name: 'Asana',
    icon: 'flow',
    get category() { return tAny('connectors:catalog.category.project'); },
    provider: 'Asana',
    get summary() { return tAny('connectors:fixture.catalog.asana.summary'); },
    badges: ['new'],
    get examples() { return fixtureExamples('catalog.asana' as string); },
    comingSoon: true,
  },
  {
    id: 'dropbox',
    name: 'Dropbox',
    icon: 'cloud',
    get category() { return tAny('connectors:catalog.category.storage'); },
    provider: 'Dropbox',
    get summary() { return tAny('connectors:fixture.catalog.dropbox.summary'); },
    get examples() { return fixtureExamples('catalog.dropbox' as string); },
    comingSoon: true,
  },
  {
    id: 'postgres',
    name: 'PostgreSQL',
    icon: 'database',
    get category() { return tAny('connectors:catalog.category.database'); },
    provider: 'PostgreSQL',
    get summary() { return tAny('connectors:fixture.catalog.postgres.summary'); },
    badges: ['recommended'],
    get examples() { return fixtureExamples('catalog.postgres' as string); },
    comingSoon: true,
  },
  {
    id: 'zendesk',
    name: 'Zendesk',
    icon: 'chat',
    get category() { return tAny('connectors:catalog.category.customerSupport'); },
    provider: 'Zendesk',
    get summary() { return tAny('connectors:fixture.catalog.zendesk.summary'); },
    get examples() { return fixtureExamples('catalog.zendesk' as string); },
    comingSoon: true,
  },
];

// ── i18n 렌더 헬퍼 ─────────────────────────────────────────────────
// 상태에는 stable code를 저장하고, 렌더 시점에 t() 를 받아 번역한다.
// 파라미터 t는 'chat' 네임스페이스로 호출된 useTranslation().t 이어야 한다.
//
// react-i18next TFunction 은 복잡한 오버로드 타입이라 직접 받으면 TS 오류가 난다.
// i18next apiError.ts 패턴과 동일하게 unknown 캐스트 래퍼를 사용한다.
// 호출 측: `(tChat as unknown as TFnChat)` 혹은 `wrapT(tChat)` 헬퍼를 이용한다.

export type TFnChat = (key: string, opts?: Record<string, unknown>) => string;

/**
 * react-i18next TFunction → TFnChat 변환.
 * `const tFn = wrapT(useTranslation('chat').t)` 패턴으로 사용한다.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function wrapT(t: any): TFnChat {
  return t as TFnChat;
}

/**
 * Skill.trend → 번역 문자열.
 * stable TrendCode는 chat:learning.trend* 키로 매핑된다.
 * 매핑되지 않는 레거시 문자열은 그대로 반환한다.
 */
export function renderTrend(trend: string, t: TFnChat): string {
  switch (trend) {
    case 'trend_just_now':       return t('learning.trendJustNow');
    case 'trend_new':            return t('learning.trendNew');
    case 'trend_improved':       return t('learning.trendImproved');
    case 'trend_improved_short': return t('learning.trendImprovedShort');
    case 'trend_just_created':   return t('learning.trendJustCreated');
    case 'trend_consolidated':   return t('learning.trendConsolidated');
    case 'trend_consolidated_new': return t('learning.trendConsolidatedNew');
    case 'trend_not_used_yet':   return t('learning.trendNotUsedYet');
    case 'trend_in_use':         return t('learning.trendInUse');
    case 'trend_used_today':     return t('learning.trendUsedToday');
    default: {
      // trend_days_ago:<N> 패턴
      if (trend.startsWith('trend_days_ago:')) {
        const days = parseInt(trend.split(':')[1], 10);
        return t('learning.trendDaysAgo', { count: days });
      }
      // 레거시 자유 문자열 — 그대로 반환
      return trend;
    }
  }
}

/**
 * SkillDetail.createdFrom → 번역 문자열.
 * stable CreatedFromCode는 chat:learning.createdFrom* 키로 매핑된다.
 */
export function renderCreatedFrom(code: string, t: TFnChat): string {
  switch (code) {
    case 'created_from_chat':      return t('learning.createdFromChat');
    case 'created_from_chat_auto': return t('learning.createdFromChatAuto');
    case 'created_from_loop':      return t('learning.createdFromLoop');
    case 'created_from_patch':     return t('learning.sourcePatch');
    case 'created_from_builtin':   return t('learning.createdFromBuiltin');
    default:                       return code; // 레거시 자유 문자열
  }
}

/**
 * SkillDetail.lastUsed → 번역 문자열.
 * 'last_used_just_now' / 'last_used_never' → 카탈로그 문자열.
 * 'last_used_iso:<ISO>' → 앱 로케일로 포맷된 날짜.
 * ISO 날짜 문자열 직접 저장 → 앱 로케일 포맷.
 */
export function renderLastUsed(code: string, t: TFnChat, locale: string): string {
  if (code === 'last_used_just_now') return t('learning.lastUsedJustNow');
  if (code === 'last_used_never')    return t('learning.lastUsedNever');
  const isoPayload = code.startsWith('last_used_iso:') ? code.slice('last_used_iso:'.length) : code;
  const d = new Date(isoPayload);
  if (!isNaN(d.getTime())) {
    return d.toLocaleDateString(locale === 'ko' ? 'ko-KR' : 'en-US');
  }
  return code; // 레거시 자유 문자열
}

/**
 * SkillVersion.when → 번역/포맷된 날짜 문자열.
 * stable LastUsedCode, ISO 날짜, 또는 레거시 자유 문자열을 처리한다.
 */
export function renderVersionWhen(when: string, t: TFnChat, locale: string): string {
  return renderLastUsed(when, t, locale);
}

/**
 * SkillVersion.note → 번역 문자열.
 * stable note code (noteAutoCreated, noteFromChat, noteLoopApproved, noteRuntimeSync, noteInstalled)는
 * 카탈로그로 매핑하고, 나머지 동적 changelog 문자열은 그대로 반환한다.
 * noteParams는 stable code일 때 i18next 보간 파라미터로 전달된다.
 */
export function renderVersionNote(note: string, t: TFnChat, noteParams?: Record<string, string>): string {
  switch (note) {
    case 'noteAutoCreated':  return t('learning.noteAutoCreated');
    case 'noteFromChat':     return t('learning.noteFromChat');
    case 'noteLoopApproved': return t('learning.noteLoopApproved');
    case 'noteRuntimeSync':  return t('learning.noteRuntimeSync');
    case 'noteInstalled':    return t('learning.noteInstalled', noteParams ?? {});
    default:                 return note; // 동적 changelog 또는 레거시 자유 문자열
  }
}

// 런타임은 changelog 폴백 문장을 합성하지 않는다(learning/proposals.py) — LLM이
// 생략하면 빈 문자열로 오고, 표시 폴백은 여기서 로케일로 결정한다. 코드 도입 전에
// 저장된 한글 폴백 문장(레거시)도 같은 키로 흡수한다.
const LEGACY_CHANGELOG_NEW = '신규 스킬 제안';
const LEGACY_CHANGELOG_IMPROVEMENT = '개선 제안';
const LEGACY_CHANGELOG_CONSOLIDATION = 'curator 통합 제안';

/** PatchProposal.changelog → 표시 문자열. isNew는 from_version === 0. */
export function renderChangelog(changelog: string | undefined, isNew: boolean, t: TFnChat): string {
  const text = (changelog ?? '').trim();
  if (!text) return isNew ? t('learning.changelogNewSkill') : t('learning.changelogImprovement');
  if (text === LEGACY_CHANGELOG_NEW) return t('learning.changelogNewSkill');
  if (text.startsWith(`${LEGACY_CHANGELOG_NEW} · `)) {
    return `${t('learning.changelogNewSkill')} · ${text.slice(LEGACY_CHANGELOG_NEW.length + 3)}`;
  }
  if (text === LEGACY_CHANGELOG_IMPROVEMENT) return t('learning.changelogImprovement');
  if (text === LEGACY_CHANGELOG_CONSOLIDATION) return t('learning.changelogConsolidation');
  return text;
}

// Made with Bob
