import type { IconName } from '../components/ui/Icon';
import type { ImpactReport, PatchTrigger, PlanStepData, SkillFile } from './types';

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

export interface Skill {
  id: string;
  name: string;
  ver: string;
  uses: number;
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
   * 마지막 사용 후 경과일 (last_used_at 기준, LEARNING_LOOP_DESIGN 5.1).
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
  current?: boolean;
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

export interface SkillDetail {
  summary: string;
  createdFrom: string;
  lastUsed: string;
  shared: boolean;
  rules: SkillRule[];
  versions: SkillVersion[];
  usage: SkillUsage[];
  permissions: SkillPermission[];
  /** 워크플로형(절차적) 스킬의 단계 — kind==='workflow' 일 때만 채워짐 */
  steps?: SkillStep[];
  /** 성과 요약 — 헤더 성과 스트립 */
  health?: SkillHealth;
  /** 최근 활동 피드 */
  activity?: SkillActivity[];
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
      /** rendered result preview (on ok) */
      result?: string;
      /** error text (on failure / skip) */
      error?: string;
    }
  | {
      kind: 'agent';
      text: string;
      bullets?: string[];
      usedSkill?: SkillRef;
      action?: { label: string; icon: IconName };
      /** epoch ms — stamped when the answer first arrives (live) or restored from DB */
      sentAt?: number;
    }
  | {
      kind: 'skill';
      title: string;
      skill: string;
      skillId: string;
      ver: string;
      desc: string;
      meta: string;
      /**
       * 있으면 "스킬로 저장"이 실제로 이 스킬을 학습 store에 생성한다 (패널·My Skills에 즉시 등장).
       * 없으면 이미 존재하는 스킬을 확정/재사용하는 것으로 처리.
       */
      save?: {
        command: string;
        summary: string;
        rules?: string[];
        steps?: SkillStep[];
        permissions?: SkillPermission[];
      };
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
      /** 승인 시 My Skills 히스토리에 보관될 이 버전의 규칙 스냅샷 */
      newRules?: SkillVersionRule[];
      /** 적용 후 사용처에 남길 한 줄 (선택) */
      note?: string;
      /** Phase D 가드 경고 (예: "AWS access key 자동 마스킹됨") — 비어있으면 표시 안 함 */
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
  { key: 'exp', label: '경험', icon: 'idea', desc: '대화·작업에서 패턴 관찰' },
  { key: 'gen', label: '스킬 생성', icon: 'tools', desc: '반복을 재사용 스킬로' },
  { key: 'use', label: '사용·개선', icon: 'renew', desc: '쓸수록 규칙 보강' },
  { key: 'model', label: '유저 모델', icon: 'user', desc: '세션을 넘어 기억' },
];

// ── Learning Impact (eval results — RICE_LEARNING_EVAL 6.3) ──────
// 제품 thesis "쓸수록 똑똑해짐"을 숫자로 증명하는 패널의 데이터. 디자인
// 단계엔 이 시드 fixture로, 런타임 완성 후엔 harness 실측(eval/results/
// latest.json, EVAL 6.4)으로 동일 스키마를 drop-in 교체한다.
// warm.skills_used의 @vN은 patch로 개선된 버전을 가리킨다 — 라이브 데모의
// 「분기 매출 요약」 v3→v4 개선(ACT 2.5)과 정량/정성이 같은 스킬을 향한다.
export const IMPACT_REPORT: ImpactReport = {
  task_suite: 'demo-v1',
  model: 'claude-sonnet-4-5',
  runs_per_task: 5,
  generated_at: '2026-06-22T10:00:00Z',
  tasks: [
    {
      id: 'T-A',
      title: '분기 매출 요약',
      cold: { turns: 5, tool_calls: 12, success_rate: 0.8, skill_hit: false },
      warm: { turns: 2, tool_calls: 5, success_rate: 1.0, skill_hit: true, skills_used: ['분기 매출 요약@v4'] },
      delta: { turns_pct: -0.6, tool_calls_pct: -0.58, success_pp: 0.2 },
    },
    {
      id: 'T-B',
      title: 'Db2 스키마 점검',
      cold: { turns: 4, tool_calls: 9, success_rate: 0.8, skill_hit: false },
      warm: { turns: 2, tool_calls: 4, success_rate: 1.0, skill_hit: true, skills_used: ['Db2 스키마 점검@v2'] },
      delta: { turns_pct: -0.5, tool_calls_pct: -0.56, success_pp: 0.2 },
    },
    {
      id: 'T-C',
      title: '고객 이탈 분석',
      cold: { turns: 6, tool_calls: 11, success_rate: 0.6, skill_hit: false },
      warm: { turns: 3, tool_calls: 6, success_rate: 0.8, skill_hit: true, skills_used: ['고객 이탈 분석@v1'] },
      delta: { turns_pct: -0.5, tool_calls_pct: -0.45, success_pp: 0.2 },
    },
  ],
  aggregate: { turns_pct: -0.54, tool_calls_pct: -0.53, success_pp: 0.2, skill_hit_rate: 1.0 },
};

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
}

// ── Connectors (linked external accounts / data sources) ─────────
/**
 * 커넥터가 노출하는 각 도구를 에이전트가 언제 호출할 수 있는지에 대한 정책.
 * 도구 자체는 제공자가 정의하므로 사용자가 임의로 추가/삭제하지 않고, "언제 쓸 수
 * 있는가"만 설정한다(실제 Claude 커넥터 권한 모델과 동일).
 */
export type ScopePermission = '항상 허용' | '승인 필요' | '차단';

export interface ConnectorScope {
  name: string;
  /** '읽기' = 읽기 전용 도구, '쓰기'/'실행' = 쓰기·실행 도구 (그룹·기본 권한 결정) */
  mode: string;
  /** 생략 시 mode로 추정: 읽기→'항상 허용', 그 외→'승인 필요' */
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
 */
export interface ConnectorUpdate {
  version: string;
  note: string;
  /** 이 업데이트로 새로 노출되는 도구 (적용 시 scopes에 추가) */
  addsTools?: ConnectorScope[];
}

/** 커넥터 구분을 정규화 (kind 미지정 = 원격 커넥터) */
export const connectorKind = (c: Connector): 'remote' | 'local-mcp' => c.kind ?? 'remote';

export const CONNECTORS: Connector[] = [
  {
    id: 'db2',
    name: 'Db2 (prod)',
    icon: 'database',
    category: '데이터베이스',
    provider: 'IBM Db2',
    status: 'connected',
    account: 'db2-prod.ibm.internal',
    lastSync: '방금',
    summary: '분기 매출 조회·스키마 점검에 쓰는 운영 데이터베이스 연결이에요.',
    scopes: [
      { name: '연결된 데이터베이스의 스키마·테이블 목록을 조회합니다.', mode: '읽기' },
      { name: '특정 테이블의 컬럼·타입·인덱스를 설명합니다.', mode: '읽기' },
      { name: '읽기 전용 SQL(SELECT) 쿼리를 실행합니다.', mode: '읽기' },
      { name: '쿼리 실행 계획(EXPLAIN)을 조회합니다.', mode: '읽기' },
      { name: 'INSERT·UPDATE·DELETE 문을 실행합니다.', mode: '쓰기', permission: '승인 필요' },
    ],
    usedBy: [
      { kind: '스킬', name: '분기 매출 요약', icon: 'skill' },
      { kind: '스킬', name: 'Db2 스키마 점검', icon: 'skill' },
      { kind: '플러그인', name: 'Db2 전문가', icon: 'database' },
    ],
    examples: ['sales 테이블 스키마를 리뷰해줘', '2026 Q1 매출 요약 만들어줘'],
  },
  {
    id: 'slack',
    name: 'Slack',
    icon: 'chat',
    category: '메시징',
    provider: 'Slack',
    status: 'connected',
    account: 'ibm-data-platform.slack.com',
    lastSync: '10분 전',
    summary: '요약·리포트를 채널로 보내고 멘션을 읽어옵니다.',
    scopes: [
      { name: '워크스페이스의 채널 목록을 조회합니다.', mode: '읽기' },
      { name: '채널의 최근 메시지를 읽어옵니다.', mode: '읽기' },
      { name: '워크스페이스 전체에서 메시지를 검색합니다.', mode: '읽기' },
      { name: '사용자를 멘션한 메시지를 조회합니다.', mode: '읽기' },
      { name: '채널에 메시지를 게시합니다.', mode: '쓰기' },
      { name: '스레드에 답글을 작성합니다.', mode: '쓰기' },
      { name: '채널에 파일을 업로드합니다.', mode: '쓰기' },
    ],
    usedBy: [{ kind: '자동화', name: '주간 매출 리포트 → Slack', icon: 'flow' }],
    examples: ['이번 주 요약을 #sales-report 채널로 보내줘'],
  },
  {
    id: 'outlook',
    name: 'Outlook',
    icon: 'send',
    category: '메일·일정',
    provider: 'Microsoft 365',
    status: 'connected',
    account: 'jaeho.seo@ibm.com',
    lastSync: '1시간 전',
    summary: '메일 초안 작성·발송과 일정 확인에 사용합니다.',
    scopes: [
      { name: '받은 편지함의 메일 목록을 조회합니다.', mode: '읽기' },
      { name: '특정 메일의 본문·첨부를 가져옵니다.', mode: '읽기' },
      { name: '지정한 기간의 캘린더 일정을 읽어옵니다.', mode: '읽기' },
      { name: '메일 초안을 생성합니다.', mode: '쓰기' },
      { name: '메일을 발송합니다.', mode: '쓰기', permission: '승인 필요' },
      { name: '캘린더 일정을 생성합니다.', mode: '쓰기' },
    ],
    usedBy: [{ kind: '대화', name: '임원 보고 메일 초안', icon: 'chat' }],
    examples: ['이 요약으로 임원 보고 메일 초안 만들어줘'],
  },
  {
    id: 'jira',
    name: 'Jira',
    icon: 'rule',
    category: '이슈 트래커',
    provider: 'Atlassian',
    status: 'disconnected',
    summary: '스프린트·이슈를 읽어 회고 정리에 활용합니다.',
    scopes: [
      { name: 'JQL로 이슈를 검색합니다.', mode: '읽기' },
      { name: '특정 이슈의 상세 정보를 조회합니다.', mode: '읽기' },
      { name: '보드의 스프린트 목록을 가져옵니다.', mode: '읽기' },
      { name: '새 이슈를 생성합니다.', mode: '쓰기' },
      { name: '이슈에 댓글을 추가합니다.', mode: '쓰기' },
      { name: '이슈의 상태를 전환합니다.', mode: '쓰기' },
    ],
    usedBy: [{ kind: '스킬', name: '회의록 액션아이템 추출', icon: 'skill' }],
    examples: ['이번 스프린트 회고를 정리해줘'],
  },
  {
    id: 'gdrive',
    name: 'Google Drive',
    icon: 'cloud',
    category: '스토리지',
    provider: 'Google',
    status: 'disconnected',
    summary: '문서를 읽어 요약하거나 결과 파일을 저장합니다.',
    scopes: [
      { name: '파일·폴더 목록을 조회합니다.', mode: '읽기' },
      { name: '이름이나 내용으로 파일을 검색합니다.', mode: '읽기' },
      { name: '문서 내용을 읽거나 내보냅니다.', mode: '읽기' },
      { name: '새 파일을 생성합니다.', mode: '쓰기' },
      { name: '기존 파일을 수정합니다.', mode: '쓰기' },
    ],
    usedBy: [],
    examples: ['이 폴더의 문서들을 요약해줘'],
  },
  {
    id: 'orchestrate',
    name: 'watsonx Orchestrate',
    icon: 'flow',
    category: '자동화',
    provider: 'IBM watsonx',
    status: 'connected',
    account: 'orchestrate.ibm.com',
    lastSync: '어제',
    summary: '워크플로를 스케줄·트리거로 자동 실행합니다.',
    scopes: [
      { name: '사용 가능한 워크플로 목록을 조회합니다.', mode: '읽기' },
      { name: '워크플로 실행 상태·기록을 확인합니다.', mode: '읽기' },
      { name: '워크플로를 실행합니다.', mode: '실행', permission: '승인 필요' },
      { name: '워크플로 스케줄을 등록·변경합니다.', mode: '실행', permission: '승인 필요' },
    ],
    usedBy: [{ kind: '자동화', name: '분기 매출 요약 · 매주 월 08:00', icon: 'flow' }],
    examples: ['매주 월요일 아침 리포트 자동화를 만들어줘'],
  },
  {
    id: 'gmail',
    name: 'Gmail',
    icon: 'send',
    category: '메일',
    provider: 'Google',
    status: 'connected',
    account: 'jaeho.seo@gmail.com',
    lastSync: '5분 전',
    summary: '메일을 읽어 요약하거나 임원 보고 메일을 작성·발송합니다. 프로토타입 기본 커넥터예요.',
    // 실제 Google Gmail 커넥터가 노출하는 도구 (읽기 3 · 쓰기/삭제 9)
    scopes: [
      { name: "Retrieves a specific email thread from the authenticated user's Gmail account.", mode: '읽기' },
      { name: 'Lists user labels.', mode: '읽기' },
      { name: "Searches for email threads from the authenticated user's Gmail account.", mode: '읽기' },
      { name: "Creates a new draft email in the authenticated user's Gmail account.", mode: '쓰기' },
      { name: 'Creates a new label.', mode: '쓰기' },
      { name: 'Deletes a label.', mode: '쓰기', permission: '승인 필요' },
      { name: 'Adds labels to a message.', mode: '쓰기' },
      { name: 'Adds labels to a thread.', mode: '쓰기' },
      { name: "Lists draft emails from the authenticated user's Gmail account.", mode: '쓰기' },
      { name: 'Removes labels from a message.', mode: '쓰기' },
      { name: 'Removes labels from a thread.', mode: '쓰기' },
      { name: 'Modifies an existing label.', mode: '쓰기' },
    ],
    usedBy: [{ kind: '대화', name: '임원 보고 메일 발송', icon: 'chat' }],
    examples: ['받은 편지함에서 이번 주 중요 메일만 요약해줘', '이 요약으로 임원 보고 메일 초안 만들어줘'],
    update: {
      version: 'v2026.6',
      note: '메일 발송·휴지통 이동 도구가 추가되었습니다.',
      addsTools: [
        { name: 'Sends an email message.', mode: '쓰기', permission: '승인 필요' },
        { name: 'Moves a message to trash.', mode: '쓰기', permission: '승인 필요' },
      ],
    },
  },

  // ── 로컬 MCP 서버 (개발자) — 내 기기에서 직접 실행/연결 ──────────────
  {
    id: 'mcp-filesystem',
    name: 'Filesystem',
    icon: 'server',
    category: '파일 시스템',
    provider: '로컬 MCP · stdio',
    kind: 'local-mcp',
    status: 'connected',
    account: 'npx @modelcontextprotocol/server-filesystem',
    lastSync: '방금',
    summary: '내 컴퓨터의 지정한 폴더를 읽고 써서 에이전트가 로컬 파일을 다룰 수 있게 합니다.',
    transport: {
      type: 'stdio',
      command: 'npx',
      args: ['-y', '@modelcontextprotocol/server-filesystem', '/Users/jaehoseo/Desktop'],
    },
    scopes: [
      { name: 'read_file', mode: '읽기' },
      { name: 'write_file', mode: '쓰기' },
      { name: 'list_directory', mode: '읽기' },
    ],
    usedBy: [{ kind: '스킬', name: '로컬 문서 요약', icon: 'skill' }],
    examples: ['바탕화면의 마크다운 파일들을 정리해줘'],
    update: {
      version: 'v0.7.0',
      note: '서버에 파일 이동·검색 도구가 추가되었습니다.',
      addsTools: [
        { name: 'move_file', mode: '쓰기', permission: '승인 필요' },
        { name: 'search_files', mode: '읽기' },
      ],
    },
  },
  {
    id: 'mcp-git',
    name: 'Git',
    icon: 'tools',
    category: '버전 관리',
    provider: '로컬 MCP · stdio',
    kind: 'local-mcp',
    status: 'connected',
    account: 'uvx mcp-server-git',
    lastSync: '2분 전',
    summary: '로컬 저장소의 커밋·diff·로그를 읽어 코드 변경을 설명하거나 요약합니다.',
    transport: {
      type: 'stdio',
      command: 'uvx',
      args: ['mcp-server-git', '--repository', '/Users/jaehoseo/Desktop/vswrk/bob/rice'],
    },
    scopes: [
      { name: 'git_log', mode: '읽기' },
      { name: 'git_diff', mode: '읽기' },
      { name: 'git_status', mode: '읽기' },
    ],
    usedBy: [{ kind: '대화', name: '변경사항 리뷰', icon: 'chat' }],
    examples: ['최근 커밋들의 변경 요약해줘'],
  },
  {
    id: 'mcp-sqlite',
    name: 'SQLite',
    icon: 'database',
    category: '데이터베이스',
    provider: '로컬 MCP · stdio',
    kind: 'local-mcp',
    status: 'disconnected',
    summary: '로컬 SQLite 파일을 쿼리해 분석합니다. 연결하면 노출 도구를 확인할 수 있어요.',
    transport: {
      type: 'stdio',
      command: 'uvx',
      args: ['mcp-server-sqlite', '--db-path', './data.db'],
    },
    scopes: [],
    usedBy: [],
    examples: ['data.db의 테이블 목록을 보여줘'],
  },
];

/** maps a tool scope / permission name to the connector that owns it (or null) */
export function connectorIdForScope(scope: string): string | null {
  const s = scope.toLowerCase();
  if (s.includes('gmail') || s.includes('메일 보내기') || s.includes('받은 편지함')) return 'gmail';
  if (s.includes('calendar') || s.includes('캘린더')) return 'calendar';
  if (s.startsWith('db2')) return 'db2';
  if (s.includes('orchestrate') || s.includes('워크플로')) return 'orchestrate';
  if (s.startsWith('#') || s.includes('slack') || s.includes('멘션')) return 'slack';
  if (s.includes('메일') || s.includes('일정') || s.includes('outlook')) return 'outlook';
  if (s.includes('drive')) return 'gdrive';
  if (s.includes('jira')) return 'jira';
  return null;
}

// ── Connector directory (추가 가능한 원격 커넥터 카탈로그) ──────────
export interface CatalogConnector {
  id: string;
  name: string;
  icon: IconName;
  category: string;
  provider: string;
  summary: string;
  /** '인기' · '새로 생성' · '추천' 등 카드에 표시할 뱃지 */
  badges?: string[];
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
    category: '개발',
    provider: 'GitHub',
    summary: '이슈·PR·코드를 읽어 변경을 요약하거나 리뷰를 돕습니다.',
    badges: ['인기'],
    examples: ['이 저장소의 열린 PR을 요약해줘'],
    comingSoon: true,
  },
  {
    id: 'notion',
    name: 'Notion',
    icon: 'bookmark',
    category: '문서',
    provider: 'Notion',
    summary: '워크스페이스 문서를 검색·요약하고 페이지를 작성합니다.',
    badges: ['인기'],
    examples: ['이번 주 회의 노트를 한 페이지로 정리해줘'],
    comingSoon: true,
  },
  {
    id: 'linear',
    name: 'Linear',
    icon: 'flow',
    category: '이슈 트래커',
    provider: 'Linear',
    summary: '이슈·사이클을 읽어 진행 상황을 정리하고 티켓을 생성합니다.',
    badges: ['새로 생성'],
    examples: ['이번 사이클 남은 이슈를 정리해줘'],
    comingSoon: true,
  },
  {
    id: 'confluence',
    name: 'Confluence',
    icon: 'rule',
    category: '문서',
    provider: 'Atlassian',
    summary: '스페이스 문서를 검색해 답을 찾거나 보고서를 작성합니다.',
    examples: ['온보딩 문서를 요약해줘'],
    comingSoon: true,
  },
  {
    id: 'salesforce',
    name: 'Salesforce',
    icon: 'growth',
    category: 'CRM',
    provider: 'Salesforce',
    summary: '거래·고객 데이터를 조회해 파이프라인을 분석합니다.',
    badges: ['추천'],
    examples: ['이번 분기 파이프라인 상위 거래를 보여줘'],
    comingSoon: true,
  },
  {
    id: 'hubspot',
    name: 'HubSpot',
    icon: 'growth',
    category: '마케팅·CRM',
    provider: 'HubSpot',
    summary: '컨택·딜·캠페인 데이터를 읽어 리포트를 만듭니다.',
    examples: ['이번 달 신규 리드 추이를 요약해줘'],
    comingSoon: true,
  },
  {
    id: 'asana',
    name: 'Asana',
    icon: 'flow',
    category: '프로젝트',
    provider: 'Asana',
    summary: '태스크·프로젝트를 읽어 상태를 정리하고 업무를 만듭니다.',
    badges: ['새로 생성'],
    examples: ['마감 임박한 태스크를 알려줘'],
    comingSoon: true,
  },
  {
    id: 'dropbox',
    name: 'Dropbox',
    icon: 'cloud',
    category: '스토리지',
    provider: 'Dropbox',
    summary: '폴더의 문서를 읽어 요약하거나 결과 파일을 저장합니다.',
    examples: ['이 폴더의 PDF들을 한 줄씩 요약해줘'],
    comingSoon: true,
  },
  {
    id: 'postgres',
    name: 'PostgreSQL',
    icon: 'database',
    category: '데이터베이스',
    provider: 'PostgreSQL',
    summary: '스키마를 점검하고 읽기 전용 쿼리로 데이터를 분석합니다.',
    badges: ['추천'],
    examples: ['orders 테이블의 최근 추세를 분석해줘'],
    comingSoon: true,
  },
  {
    id: 'zendesk',
    name: 'Zendesk',
    icon: 'chat',
    category: '고객지원',
    provider: 'Zendesk',
    summary: '티켓을 읽어 이슈를 분류하고 응답 초안을 작성합니다.',
    examples: ['오늘 들어온 긴급 티켓을 정리해줘'],
    comingSoon: true,
  },
];

// Made with Bob
