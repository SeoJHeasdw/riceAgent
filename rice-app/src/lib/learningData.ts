import type { IconName } from '../components/ui/Icon';
import type { ImpactReport, PatchTrigger, SkillFile } from './types';

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
 */
export type SkillOrigin = 'learned' | 'installed' | 'authored';

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
  | { kind: 'user'; text: string }
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
// ── Learning loop panel ─────────────────────────────────────────
export const USER_MODEL: UserSignal[] = [
  { id: 'u1', label: '언어', value: '한국어 우선', conf: 0.98, source: '명시적 설정' },
  { id: 'u2', label: '환경', value: 'Db2 · watsonx.data', conf: 0.91, source: '관찰된 사용' },
  { id: 'u3', label: '문체', value: '간결한 불릿 선호', conf: 0.86, source: '반복 교정' },
  { id: 'u4', label: '리듬', value: '오전에 리포트 작업 집중', conf: 0.74, source: '추론' },
  { id: 'u5', label: '역할', value: 'Data Platform · APAC', conf: 0.69, source: '추론' },
];

export const SKILLS: Skill[] = [
  { id: 's1', name: '분기 매출 요약', ver: 'v3', uses: 12, trend: '+1 방금', hot: true, enabled: true, patches: 2, command: 'quarterly_sales_summary', state: 'active', idleDays: 0 },
  { id: 's2', name: 'Db2 스키마 점검', ver: 'v2', uses: 8, trend: '안정', enabled: true, patches: 1, command: 'db2_schema_check', state: 'active', idleDays: 1 },
  { id: 's3', name: '회의록 액션아이템 추출', ver: 'v5', uses: 23, trend: '자주 사용', enabled: true, patches: 4, command: 'meeting_action_items', state: 'active', idleDays: 0 },
  // s4: 3일 미사용인데 아직 active — "스킬 정리"가 stale 로 전이시키는 데모 대상
  { id: 's4', name: 'PR 코멘트 요약', ver: 'v1', uses: 3, trend: '3일 전', enabled: false, patches: 0, command: 'pr_comment_summary', state: 'active', idleDays: 3 },
  { id: 's5', name: 'Jira 스프린트 회고 정리', ver: 'v2', uses: 1, trend: '4일 전', enabled: true, patches: 0, command: 'jira_retro_summary', state: 'stale', idleDays: 4 },
  { id: 's6', name: '한↔영 기술문서 번역', ver: 'v1', uses: 0, trend: '7일 전', enabled: false, patches: 0, command: 'tech_doc_translate', state: 'archived', idleDays: 7 },
  // s7: 마켓에서 설치한 뒤 내가 다듬은 스킬 — origin:installed + upstream.
  // installedVer(v3) 위로 내 v4가 쌓였고(내 개선 1건), 원작자는 v5까지 올림 → "업데이트 있음".
  {
    id: 's7', name: 'PDF 보고서 생성기', ver: 'v4', uses: 9, trend: '+1 어제', enabled: true, patches: 1,
    command: 'pdf_report_generator', state: 'active', idleDays: 1,
    origin: 'installed',
    upstream: { author: '정수아', team: '비즈니스 인텔리전스', marketId: 'skill-3', installedVer: 'v3', latestVer: 'v5' },
  },
  // s8: 내가 직접 작성해 공유까지 한 스킬 — origin:authored.
  { id: 's8', name: '로컬 문서 요약', ver: 'v2', uses: 6, trend: '안정', enabled: true, patches: 1, command: 'local_doc_summary', state: 'active', idleDays: 2, origin: 'authored' },
];

export const INSTALLED_TOOLS: InstalledTool[] = [
  {
    id: 'i1', name: 'watsonx Orchestrate', kind: 'plugin', icon: 'flow', enabled: true, meta: '워크플로 · 자동화',
    provides: [
      { command: 'orchestrate_build_flow', desc: '요구사항을 워크플로로 설계' },
      { command: 'orchestrate_deploy', desc: '스케줄·트리거로 배포' },
    ],
  },
  {
    id: 'i2', name: 'Db2 전문가', kind: 'plugin', icon: 'database', enabled: true, meta: '데이터 · 튜닝',
    provides: [
      { command: 'db2_perf_tune', desc: '느린 쿼리 실행계획 진단' },
      { command: 'db2_migrate_check', desc: '마이그레이션 리스크 점검' },
    ],
  },
  {
    id: 'i3', name: 'watsonx.governance 전문가', kind: 'plugin', icon: 'rule', enabled: false, meta: '거버넌스',
    provides: [
      { command: 'governance_policy_check', desc: '모델 정책 준수 점검' },
    ],
  },
  { id: 'i4', name: '회의록 액션아이템 추출기', kind: 'tool', icon: 'tools', enabled: true, meta: 'PMO · NLP', command: 'extract_action_items' },
];

export const SESSION_DELTAS: string[] = [
  '"매출 요약엔 항상 단위를 포함" 규칙 추가',
  'APAC 시장을 기본 우선순위로 학습',
  '월요일 오전 리포트 루틴 인지',
];

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
//  p3 — s4(PR 코멘트 요약)는 c4 대화 외엔 거의 안 쓰이고 초안 상태
export const PROPOSALS: Proposal[] = [
  {
    id: 'p1',
    kind: 'rule',
    title: '「분기 매출 요약」에 규칙 추가',
    evidence: '이 대화의 매출 요약 2건에서 watsonx.ai 라인 기여도를 매번 포함했는데, 아직 규칙이 아니에요.',
    source: { conversationId: 'c1', title: '분기 매출 리포트 자동화', count: 2, messageIndex: 20 },
    targetSkillId: 's1',
    rule: '매출 요약에 watsonx.ai 라인 기여도를 항상 포함',
    acceptLabel: '규칙으로 승격',
  },
  {
    id: 'p2',
    kind: 'tool',
    title: '반복 작업을 새 도구로 저장',
    evidence: '최근 세 대화에서 매번 Db2 조회로 분석을 시작했어요. 이 조회를 도구로 묶을까요?',
    source: { conversationId: 'c3', title: '고객 이탈 분석 스킬 만들기', count: 3, messageIndex: 2 },
    tool: {
      name: 'Db2 빠른 조회',
      command: 'db2_quick_query',
      summary: '자주 쓰는 Db2 조회를 한 번에 실행하는 원자적 도구예요. 반복 패턴에서 자동으로 추출됐어요.',
    },
    acceptLabel: '도구 저장',
  },
  {
    id: 'p3',
    kind: 'prune',
    title: '안 쓰는 스킬 정리',
    evidence: '「PR 코멘트 요약」은 해당 대화 외엔 거의 쓰이지 않고, 아직 초안(v1) 상태예요.',
    source: { conversationId: 'c4', title: 'PR 리뷰 코멘트 요약 워크플로', messageIndex: 1 },
    targetSkillId: 's4',
    acceptLabel: 'Stale로 표시',
  },
];

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

// ── My Skills (full management screen) ───────────────────────────
export const SKILL_DETAILS: Record<string, SkillDetail> = {
  s1: {
    summary: 'Db2 매출 데이터를 임원용 불릿 요약으로 변환합니다. 대화에서 자동 생성된 뒤 교정으로 다듬어졌어요.',
    createdFrom: '대화에서 자동 생성',
    lastUsed: '방금',
    shared: false,
    rules: [
      { id: 'r1', text: 'APAC 시장을 항상 맨 위에 표시', source: '교정에서 학습', on: true, applied: 11, confidence: 'high', lastApplied: '방금' },
      { id: 'r2', text: '모든 수치에 단위 포함 ($, %, GB)', source: '교정에서 학습', on: true, applied: 12, confidence: 'high', lastApplied: '방금' },
      { id: 'r3', text: '한국어 · 간결한 불릿 3개 이내', source: '예시에서 학습', on: true, applied: 12, confidence: 'high', lastApplied: '어제' },
      { id: 'r4', text: '전분기 대비 증감률 자동 계산', source: '반복 관찰', on: true, applied: 9, confidence: 'medium', lastApplied: '3일 전' },
    ],
    steps: [
      { tool: 'db2.query', label: 'Db2 매출 조회', desc: 'db2-prod.sales 에서 기간별 매출 추출', icon: 'database' },
      { tool: 'data.aggregate', label: '지역·라인별 집계', desc: 'region·product_line 기준 합계 계산', icon: 'flow' },
      { tool: 'report.format', label: '임원용 불릿 생성', desc: 'APAC 우선·단위 포함 규칙 적용', icon: 'edit' },
    ],
    health: {
      successRate: 0.94,
      successDelta: 0.08,
      timeSavedMin: 18,
      avgTurns: 2.1,
      coldTurns: 5,
      spark: [5, 4, 4, 3, 3, 2, 2],
      rating: { up: 9, down: 1 },
    },
    activity: [
      { when: '방금', text: '「분기 매출 리포트 자동화」 대화에서 실행 · 성공', kind: 'use' },
      { when: '오늘 08:00', text: 'watsonx Orchestrate 스케줄 자동 실행', kind: 'use' },
      { when: '오늘', text: '"APAC 우선" 규칙 추가로 v3 발행', kind: 'learn' },
      { when: '어제', text: '결과에서 단위 누락 1건 교정 → 규칙 보강', kind: 'edit' },
    ],
    versions: [
      {
        ver: 'v3',
        when: '오늘',
        note: 'APAC 우선·단위 포함 규칙 추가',
        current: true,
        author: 'AI 제안 + 내 교정',
        metrics: { uses: 7, successRate: 0.94, avgTurns: 2.1, editRate: 0.08 },
        rules: [
          { text: 'APAC 시장을 항상 맨 위에 표시', added: true },
          { text: '모든 수치에 단위 포함 ($, %, GB)', added: true },
          { text: '한국어 · 간결한 불릿 3개 이내' },
          { text: '전분기 대비 증감률 자동 계산' },
        ],
      },
      {
        ver: 'v2',
        when: '지난주',
        note: '한국어 불릿 형식으로 고정',
        author: '내 교정',
        metrics: { uses: 4, successRate: 0.86, avgTurns: 2.8, editRate: 0.22 },
        rules: [
          { text: '한국어 · 간결한 불릿 3개 이내', added: true },
          { text: '전분기 대비 증감률 자동 계산' },
        ],
      },
      {
        ver: 'v1',
        when: '2주 전',
        note: '최초 생성 — Db2 조회 + 요약',
        author: 'AI 자동 생성',
        metrics: { uses: 1, successRate: 0.70, avgTurns: 4.2, editRate: 0.45 },
        rules: [{ text: 'Db2 매출 조회 후 요약', added: true }],
      },
    ],
    usage: [
      { kind: '자동화', name: 'watsonx Orchestrate · 매주 월 08:00', icon: 'flow' },
      { kind: '도구', name: '분기 매출 리포트 생성기', icon: 'tools' },
      { kind: '대화', name: '분기 매출 리포트 자동화', icon: 'chat' },
    ],
    permissions: [
      { name: 'db2-prod.sales', mode: '읽기' },
      { name: '~/reports', mode: '쓰기' },
    ],
    // 성숙한 학습 스킬의 실제 번들 — 대화로 시작했지만 절차·참고·스크립트가 쌓였다.
    files: [
      {
        path: 'SKILL.md',
        language: 'markdown',
        content: `---
name: quarterly-sales-summary
description: Db2 매출 데이터를 임원용 불릿 요약으로 변환
version: v3
source: 대화에서 학습 (교정으로 다듬어짐)
---

# 분기 매출 요약

Db2 매출 데이터를 조회해 **임원 보고용 핵심 불릿**으로 요약합니다.
대화에서 자동 생성된 뒤, 반복 교정으로 규칙이 쌓였습니다.

## 학습한 규칙

- **APAC 시장을 항상 맨 위에** 표시
- 모든 수치에 **단위 포함** ($, %, GB)
- 한국어 · 간결한 불릿 3개 이내
- 전분기 대비 증감률 자동 계산

## 절차

1. 매출 데이터 조회 — scripts/fetch_sales.sh (db2-prod.sales)
2. 지역·라인별 집계 — scripts/aggregate.py
3. 임원용 불릿 생성 — assets/exec_summary_template.md 형식

세부 형식은 references/exec-summary-format.md, 데이터 소스 이력은
references/data-sources.md 를 참고하세요.
`,
      },
      {
        path: 'scripts/fetch_sales.sh',
        language: 'shell',
        content: `#!/usr/bin/env bash
set -euo pipefail

# 분기 매출 조회 — 기간을 받아 region·product_line 합계를 CSV로 출력 (센트→달러 환산)
RANGE_START="\${1:?기간 시작(YYYY-MM-DD)}"
RANGE_END="\${2:?기간 끝(YYYY-MM-DD)}"

db2 -x "SELECT region, product_line, SUM(revenue)/100.0 AS rev_usd
  FROM db2-prod.sales
  WHERE order_date BETWEEN '\${RANGE_START}' AND '\${RANGE_END}'
  GROUP BY region, product_line
  ORDER BY rev_usd DESC"
`,
      },
      {
        path: 'scripts/aggregate.py',
        language: 'python',
        content: `#!/usr/bin/env python3
"""지역별 매출을 집계하고 APAC을 맨 위로 정렬한다(학습된 규칙). 표준 라이브러리만 사용."""
import csv
import sys
from collections import defaultdict

APAC_FIRST = "APAC"


def main() -> None:
    totals = defaultdict(float)
    for region, _line, rev in csv.reader(sys.stdin):
        totals[region] += float(rev)

    # APAC을 항상 맨 위에, 나머지는 매출 내림차순
    rows = sorted(totals.items(), key=lambda kv: (kv[0] != APAC_FIRST, -kv[1]))
    for region, rev in rows:
        print(region + "\\t$" + f"{rev / 1_000_000:.1f}M")


if __name__ == "__main__":
    main()
`,
      },
      {
        path: 'references/exec-summary-format.md',
        language: 'markdown',
        content: `# 임원 요약 형식 규칙

리포트 결과가 따라야 하는 컨벤션. SKILL.md가 이 문서를 참고로 불러온다.

## 구조

- 핵심 불릿 **3개 이내**
- 각 불릿은 한 줄 · 숫자 먼저, 맥락 뒤
- 첫 불릿은 항상 **APAC**

## 숫자 표기

- 통화: $ 접두 · 백만 단위는 M (예: $48.2M)
- 증감: 전분기 대비 +/- % (예: +11.4%)
`,
      },
      {
        path: 'references/data-sources.md',
        language: 'markdown',
        content: `# 데이터 소스

## 현재

- **db2-prod.sales** (Db2) — 분기 매출 원본

## 주의

- revenue는 **센트** 단위로 적재됨 → 집계 시 \`/100\` 으로 달러 환산
- 2026 Q2부터 watsonx.data.sales 로 이전 예정 (설치형 PDF 스킬과 동일 추세)
`,
      },
      {
        path: 'assets/exec_summary_template.md',
        language: 'markdown',
        content: `# {{quarter}} 매출 — 임원 요약

- {{bullet_apac}}
- {{bullet_total}}
- {{bullet_top_line}}
`,
      },
    ],
  },
  s2: {
    summary: 'Db2 스키마·타입·제약을 점검해 마이그레이션 전 리스크를 진단합니다.',
    createdFrom: '예시 3건에서 학습',
    lastUsed: '오늘',
    shared: true,
    rules: [
      { id: 'r1', text: 'NOT NULL · 외래키 누락을 먼저 보고', source: '교정에서 학습', on: true, applied: 8, confidence: 'high', lastApplied: '오늘' },
      { id: 'r2', text: '타입 불일치는 표로 정리', source: '예시에서 학습', on: true, applied: 7, confidence: 'medium', lastApplied: '오늘' },
      { id: 'r3', text: '제약 조건 위반 사전 점검', source: '교정에서 학습', on: true, applied: 5, confidence: 'medium', lastApplied: '지난주' },
    ],
    steps: [
      { tool: 'db2.introspect', label: '스키마 조회', desc: '테이블·컬럼·제약 메타데이터 수집', icon: 'database' },
      { tool: 'schema.diff', label: '타입·제약 비교', desc: '대상 스키마와 비교해 불일치 탐지', icon: 'flow' },
      { tool: 'report.format', label: '리스크 리포트', desc: 'NOT NULL·외래키 누락을 우선 정리', icon: 'rule' },
    ],
    health: {
      successRate: 0.92,
      successDelta: 0.12,
      timeSavedMin: 12,
      avgTurns: 2.4,
      coldTurns: 4,
      spark: [4, 3, 3, 3, 2, 2],
      rating: { up: 6, down: 0 },
    },
    activity: [
      { when: '오늘', text: '「마이그레이션 점검」 대화에서 실행 · 성공', kind: 'use' },
      { when: '지난주', text: '"제약 조건 사전 점검" 규칙 추가로 v2 발행', kind: 'learn' },
      { when: '지난주', text: 'Skill Market에 공유됨', kind: 'share' },
    ],
    versions: [
      {
        ver: 'v2',
        when: '지난주',
        note: '제약 조건 점검 추가',
        current: true,
        author: '내 교정',
        metrics: { uses: 6, successRate: 0.92, avgTurns: 2.4, editRate: 0.12 },
        rules: [
          { text: 'NOT NULL · 외래키 누락을 먼저 보고' },
          { text: '타입 불일치는 표로 정리' },
          { text: '제약 조건 위반 사전 점검', added: true },
        ],
      },
      {
        ver: 'v1',
        when: '3주 전',
        note: '최초 생성',
        author: 'AI 자동 생성',
        metrics: { uses: 2, successRate: 0.80, avgTurns: 3.5, editRate: 0.30 },
        rules: [
          { text: 'NOT NULL · 외래키 누락을 먼저 보고', added: true },
          { text: '타입 불일치는 표로 정리', added: true },
        ],
      },
    ],
    usage: [{ kind: '대화', name: 'Db2 → watsonx.data 마이그레이션 점검', icon: 'chat' }],
    permissions: [{ name: 'db2-prod', mode: '읽기' }],
  },
  s3: {
    summary: '회의 노트에서 담당자·기한이 붙은 액션아이템을 추출합니다.',
    createdFrom: '대화에서 자동 생성',
    lastUsed: '어제',
    shared: true,
    rules: [
      { id: 'r1', text: '담당자가 없으면 "미정"으로 표기', source: '교정에서 학습', on: true, applied: 22, confidence: 'high', lastApplied: '어제' },
      { id: 'r2', text: '기한은 ISO 날짜로 정규화', source: '예시에서 학습', on: false, applied: 14, confidence: 'medium', lastApplied: '지난주' },
      { id: 'r3', text: '담당자 추론에 직전 발화자 우선', source: '반복 관찰', on: true, applied: 9, confidence: 'low', lastApplied: '어제' },
    ],
    health: {
      successRate: 0.88,
      successDelta: -0.05,
      timeSavedMin: 9,
      avgTurns: 2.6,
      coldTurns: 6,
      spark: [4, 3, 3, 4, 4, 5],
      rating: { up: 18, down: 5 },
    },
    activity: [
      { when: '어제', text: '"직전 발화자 우선" 규칙 추가로 v5 발행', kind: 'learn' },
      { when: '어제', text: '회의록 3건에서 담당자 오인식 → 사후 수정', kind: 'edit' },
      { when: '3일 전', text: '「스프린트 회고」 자동화에서 실행', kind: 'use' },
    ],
    versions: [
      {
        ver: 'v5',
        when: '어제',
        note: '담당자 추론에 직전 발화자 우선 (실험적)',
        current: true,
        author: 'AI 제안',
        metrics: { uses: 9, successRate: 0.88, avgTurns: 2.6, editRate: 0.18 },
        rules: [
          { text: '담당자가 없으면 "미정"으로 표기' },
          { text: '기한은 ISO 날짜로 정규화' },
          { text: '담당자 추론에 직전 발화자 우선', added: true },
        ],
      },
      {
        ver: 'v4',
        when: '지난주',
        note: '기한 파싱 추가 — 안정 버전',
        author: '내 교정',
        metrics: { uses: 11, successRate: 0.93, avgTurns: 2.2, editRate: 0.10 },
        rules: [
          { text: '담당자가 없으면 "미정"으로 표기', added: true },
          { text: '기한은 ISO 날짜로 정규화', added: true },
        ],
      },
    ],
    usage: [
      { kind: '자동화', name: 'Jira 스프린트 회고 정리', icon: 'flow' },
      { kind: '대화', name: 'Jira 스프린트 회고 정리', icon: 'chat' },
    ],
    permissions: [{ name: '~/meeting-notes', mode: '읽기' }],
  },
  s4: {
    summary: 'GitHub PR 리뷰 스레드를 결정사항 중심으로 요약합니다. 아직 다듬는 중이에요.',
    createdFrom: '예시 1건에서 학습',
    lastUsed: '3일 전',
    shared: false,
    rules: [{ id: 'r1', text: '머지 차단 코멘트를 맨 위에', source: '예시에서 학습', on: true, applied: 3, confidence: 'low', lastApplied: '3일 전' }],
    health: {
      successRate: 0.62,
      timeSavedMin: 4,
      avgTurns: 4.1,
      coldTurns: 5,
      spark: [5, 5, 4, 5],
      rating: { up: 1, down: 1 },
    },
    activity: [
      { when: '3일 전', text: '「PR 리뷰 요약」 대화에서 최초 생성', kind: 'learn' },
      { when: '3일 전', text: '결과 2건 중 1건 사후 수정', kind: 'edit' },
    ],
    versions: [
      {
        ver: 'v1',
        when: '3일 전',
        note: '최초 생성 — 아직 근거가 부족한 초안',
        current: true,
        author: 'AI 자동 생성',
        metrics: { uses: 3, successRate: 0.62, avgTurns: 4.1, editRate: 0.40 },
        rules: [{ text: '머지 차단 코멘트를 맨 위에', added: true }],
      },
    ],
    usage: [{ kind: '대화', name: 'PR 리뷰 코멘트 요약 워크플로', icon: 'chat' }],
    permissions: [],
  },
  s5: {
    summary: 'Jira 스프린트의 완료 이슈·코멘트를 모아 회고(잘된 점·문제·액션) 형식으로 정리합니다. 최근 사용이 뜸해 Stale 상태예요.',
    createdFrom: '예시 2건에서 학습',
    lastUsed: '4일 전',
    shared: false,
    rules: [
      { id: 'r1', text: '액션 아이템은 담당자·기한과 함께', source: '교정에서 학습', on: true, applied: 1, confidence: 'medium', lastApplied: '4일 전' },
      { id: 'r2', text: '완료/미완료 이슈를 구분해 집계', source: '예시에서 학습', on: true, applied: 1, confidence: 'medium', lastApplied: '4일 전' },
    ],
    steps: [
      { tool: 'jira.query', label: '스프린트 이슈 조회', desc: '대상 스프린트의 이슈·상태·코멘트 수집', icon: 'database' },
      { tool: 'data.aggregate', label: '잘된 점·문제 분류', desc: '라벨·상태 기준으로 회고 항목 분류', icon: 'flow' },
      { tool: 'report.format', label: '회고 노트 생성', desc: '액션 아이템에 담당자·기한 부착', icon: 'edit' },
    ],
    health: {
      successRate: 0.85,
      successDelta: 0.05,
      timeSavedMin: 11,
      avgTurns: 2.7,
      coldTurns: 5,
      spark: [3, 3, 3, 4, 5, 6],
      rating: { up: 1, down: 0 },
    },
    activity: [
      { when: '4일 전', text: '「스프린트 23 회고」 대화에서 실행', kind: 'use' },
      { when: '2주 전', text: '담당자·기한 표기 규칙 추가로 v2 발행', kind: 'learn' },
    ],
    versions: [
      {
        ver: 'v2',
        when: '2주 전',
        note: '액션 아이템 담당자·기한 표기 추가',
        current: true,
        author: '내 교정',
        metrics: { uses: 1, successRate: 0.85, avgTurns: 2.7, editRate: 0.20 },
        rules: [
          { text: '액션 아이템은 담당자·기한과 함께', added: true },
          { text: '완료/미완료 이슈를 구분해 집계' },
        ],
      },
      {
        ver: 'v1',
        when: '3주 전',
        note: '최초 생성',
        author: 'AI 자동 생성',
        metrics: { uses: 4, successRate: 0.80, avgTurns: 3.1, editRate: 0.28 },
        rules: [{ text: '완료/미완료 이슈를 구분해 집계', added: true }],
      },
    ],
    usage: [{ kind: '대화', name: '스프린트 23 회고 준비', icon: 'chat' }],
    permissions: [{ name: 'jira-cloud', mode: '읽기' }],
  },
  s6: {
    summary: '한국어↔영어 기술문서를 용어 일관성을 지키며 번역합니다. 7일 이상 미사용으로 보관(주입 중단)됐어요.',
    createdFrom: '예시 1건에서 학습',
    lastUsed: '7일 전',
    shared: false,
    rules: [
      { id: 'r1', text: '코드·식별자·명령어는 번역하지 않음', source: '교정에서 학습', on: true, applied: 0, confidence: 'medium', lastApplied: '7일 전' },
      { id: 'r2', text: '용어집(glossary) 우선 적용', source: '예시에서 학습', on: true, applied: 0, confidence: 'low', lastApplied: '7일 전' },
    ],
    health: {
      successRate: 0.75,
      timeSavedMin: 6,
      avgTurns: 3.4,
      coldTurns: 5,
      spark: [4, 4, 5, 5, 6, 6],
      rating: { up: 0, down: 0 },
    },
    activity: [
      { when: '7일 전', text: '마지막 사용 이후 미사용 → 보관(주입 중단)', kind: 'use' },
      { when: '4주 전', text: '「API 레퍼런스 영문화」 대화에서 생성', kind: 'learn' },
    ],
    versions: [
      {
        ver: 'v1',
        when: '4주 전',
        note: '최초 생성 — 용어집 기반 번역',
        current: true,
        author: 'AI 자동 생성',
        metrics: { uses: 0, successRate: 0.75, avgTurns: 3.4, editRate: 0.33 },
        rules: [
          { text: '코드·식별자·명령어는 번역하지 않음', added: true },
          { text: '용어집(glossary) 우선 적용', added: true },
        ],
      },
    ],
    usage: [{ kind: '대화', name: 'API 레퍼런스 영문화', icon: 'chat' }],
    permissions: [],
  },
  // s7 — 설치형. 정수아의 마켓 스킬(skill-3)을 v3에서 설치해, 사내 템플릿에 맞게
  // v4로 다듬었다(fork+개선). files는 실제 소스 — 내 v4 커스터마이즈가 반영돼 있다.
  s7: {
    summary: 'HTML/Handlebars 템플릿에 데이터를 채워 인쇄용 PDF 보고서를 생성합니다. 마켓에서 설치한 뒤 사내 템플릿에 맞게 다듬었어요.',
    createdFrom: 'Skill Market · 정수아',
    lastUsed: '어제',
    shared: false,
    rules: [
      { id: 'r1', text: '사내 보고서는 IBM Plex 폰트 템플릿 사용', source: '교정에서 학습', on: true, applied: 6, confidence: 'high', lastApplied: '어제' },
      { id: 'r2', text: '통화는 천 단위 구분 · KRW 우선 표기', source: '교정에서 학습', on: true, applied: 5, confidence: 'medium', lastApplied: '3일 전' },
    ],
    steps: [
      { tool: 'template.load', label: '템플릿 로드', desc: 'templates/ 에서 보고서 템플릿 선택', icon: 'document' },
      { tool: 'data.merge', label: '데이터 병합', desc: 'JSON/CSV 데이터를 템플릿에 주입', icon: 'flow' },
      { tool: 'pdf.render', label: 'PDF 렌더', desc: 'headless 렌더링으로 인쇄용 PDF 출력', icon: 'export' },
    ],
    health: {
      successRate: 0.9,
      successDelta: 0.04,
      timeSavedMin: 14,
      avgTurns: 2.3,
      coldTurns: 5,
      spark: [4, 3, 3, 3, 2, 2],
      rating: { up: 4, down: 0 },
    },
    activity: [
      { when: '어제', text: '월간 KPI 보고서 PDF 생성 · 성공', kind: 'use' },
      { when: '3일 전', text: '"사내 폰트 템플릿" 규칙 추가로 v4 발행', kind: 'learn' },
      { when: '1주 전', text: 'Skill Market에서 설치 (정수아 · v3)', kind: 'restore' },
    ],
    versions: [
      {
        ver: 'v4',
        when: '3일 전',
        note: '사내 템플릿·통화 표기 규칙 추가 (내 개선)',
        current: true,
        author: '내 교정',
        metrics: { uses: 5, successRate: 0.9, avgTurns: 2.3, editRate: 0.1 },
        rules: [
          { text: '사내 보고서는 IBM Plex 폰트 템플릿 사용', added: true },
          { text: '통화는 천 단위 구분 · KRW 우선 표기', added: true },
        ],
      },
      {
        ver: 'v3',
        when: '1주 전',
        note: '정수아 v3 설치 — fork 지점',
        author: '정수아 · 비즈니스 인텔리전스',
        metrics: { uses: 4, successRate: 0.86, avgTurns: 2.6, editRate: 0.18 },
        rules: [],
      },
    ],
    usage: [{ kind: '대화', name: '월간 KPI 보고서 자동화', icon: 'chat' }],
    permissions: [{ name: '~/reports', mode: '쓰기' }],
    files: [
      {
        path: 'SKILL.md',
        language: 'markdown',
        content: `---
name: pdf-report-generator
description: 템플릿에 데이터를 채워 인쇄용 PDF 보고서를 생성
version: v4
upstream: 정수아 · v3
---

# PDF 보고서 생성기

HTML/Handlebars 템플릿에 구조화된 데이터를 병합해 인쇄용 PDF를 만듭니다.
정수아님의 v3을 설치한 뒤 **사내 보고서 규격에 맞게 다듬은 v4**입니다.

## 내 개선 (v3 → v4)

- 사내 보고서는 **IBM Plex** 폰트 템플릿을 기본으로 사용
- 통화는 천 단위 구분 · **KRW 우선** 표기

## 절차

1. \`templates/\` 에서 보고서 템플릿을 고른다.
2. \`scripts/render.sh\` 로 데이터(JSON/CSV)를 병합한다.
3. headless 렌더링으로 인쇄용 PDF를 출력한다.
`,
      },
      {
        path: 'scripts/render.sh',
        language: 'shell',
        content: `#!/usr/bin/env bash
set -euo pipefail

# PDF 보고서 생성기 (v4 · 사내 템플릿 기본)
TEMPLATE="\${1:-templates/ibm-plex-report.html}"
DATA="\${2:-data.json}"
OUTPUT="\${3:-report.pdf}"

echo "Rendering $TEMPLATE with $DATA → $OUTPUT"
python3 scripts/merge.py "$TEMPLATE" "$DATA" > .out.html
chromium --headless --print-to-pdf="$OUTPUT" .out.html
rm -f .out.html
echo "Done: $OUTPUT"
`,
      },
      {
        path: 'scripts/merge.py',
        language: 'python',
        content: `#!/usr/bin/env python3
"""템플릿(HTML)에 데이터(JSON)를 병합해 HTML을 출력한다. 표준 라이브러리만 사용."""
import json
import re
import sys


def render(template: str, data: dict) -> str:
    # 아주 단순한 {{ key }} 치환 — 사내 보고서 템플릿 규격에 맞춤
    def repl(m):
        key = m.group(1).strip()
        value = data
        for part in key.split("."):
            value = value.get(part, "") if isinstance(value, dict) else ""
        return str(value)

    return re.sub(r"{{\\s*([^}]+?)\\s*}}", repl, template)


def main() -> None:
    template_path, data_path = sys.argv[1], sys.argv[2]
    with open(template_path, encoding="utf-8") as f:
        template = f.read()
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)
    sys.stdout.write(render(template, data))


if __name__ == "__main__":
    main()
`,
      },
      {
        path: 'templates/ibm-plex-report.html',
        language: 'html',
        content: `<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8" />
    <title>{{ title }}</title>
    <style>
      body { font-family: "IBM Plex Sans KR", sans-serif; color: #161616; }
      h1 { font-weight: 600; }
      .total { font-weight: 600; }
    </style>
  </head>
  <body>
    <h1>{{ title }}</h1>
    <p>기간: {{ period }}</p>
    <table>
      <thead><tr><th>항목</th><th>금액 (KRW)</th></tr></thead>
      <tbody>{{ rows }}</tbody>
    </table>
    <p class="total">합계: {{ total }}</p>
  </body>
</html>
`,
      },
      {
        path: 'references/format-guide.md',
        language: 'markdown',
        content: `# 사내 보고서 포맷 가이드

PDF 보고서가 따라야 하는 사내 규격 메모. SKILL.md가 이 파일을 참고로 불러온다.

## 타이포그래피

- 본문/표: **IBM Plex Sans KR**
- 숫자: 천 단위 구분(\`,\`) · 통화는 **KRW 우선**, 필요 시 괄호로 USD 병기

## 색상

- 강조: IBM Blue 60 (#0f62fe)
- 본문: Gray 100 (#161616)

## 표

- 헤더는 굵게, 합계 행은 굵게 + 상단 보더
`,
      },
    ],
  },
  // s8 — 제작형. 내가 직접 작성해 마켓에 공유까지 한 스킬.
  s8: {
    summary: '로컬 폴더의 마크다운·텍스트 문서를 읽어 한국어로 핵심을 요약합니다. 직접 작성한 스킬이에요.',
    createdFrom: '직접 작성',
    lastUsed: '오늘',
    shared: true,
    rules: [
      { id: 'r1', text: '문서당 3줄 이내로 요약', source: '직접 작성', on: true, applied: 6, confidence: 'high', lastApplied: '오늘' },
      { id: 'r2', text: '코드 블록은 요약에서 제외', source: '직접 작성', on: true, applied: 4, confidence: 'medium', lastApplied: '어제' },
    ],
    steps: [
      { tool: 'fs.read', label: '문서 수집', desc: '지정 폴더의 .md/.txt 파일 읽기', icon: 'document' },
      { tool: 'summarize', label: '핵심 요약', desc: '문서별 3줄 요약 생성', icon: 'idea' },
    ],
    health: {
      successRate: 0.88,
      timeSavedMin: 8,
      avgTurns: 2.0,
      coldTurns: 4,
      spark: [3, 3, 2, 2, 2, 2],
      rating: { up: 3, down: 0 },
    },
    activity: [
      { when: '오늘', text: '바탕화면 노트 12건 요약 · 성공', kind: 'use' },
      { when: '어제', text: 'Skill Market에 공유됨', kind: 'share' },
      { when: '1주 전', text: '직접 작성으로 생성', kind: 'learn' },
    ],
    versions: [
      {
        ver: 'v2',
        when: '어제',
        note: '코드 블록 제외 규칙 추가',
        current: true,
        author: '내 작성',
        metrics: { uses: 4, successRate: 0.88, avgTurns: 2.0, editRate: 0.08 },
        rules: [
          { text: '문서당 3줄 이내로 요약' },
          { text: '코드 블록은 요약에서 제외', added: true },
        ],
      },
      {
        ver: 'v1',
        when: '1주 전',
        note: '최초 작성',
        author: '내 작성',
        metrics: { uses: 2, successRate: 0.82, avgTurns: 2.4, editRate: 0.15 },
        rules: [{ text: '문서당 3줄 이내로 요약', added: true }],
      },
    ],
    usage: [
      { kind: '커넥터', name: 'Filesystem (로컬 MCP)', icon: 'server' },
      { kind: '대화', name: '로컬 노트 정리', icon: 'chat' },
    ],
    permissions: [{ name: 'Filesystem (로컬 MCP)', mode: '읽기' }],
    files: [
      {
        path: 'SKILL.md',
        language: 'markdown',
        content: `---
name: local-doc-summary
description: 로컬 폴더의 문서를 읽어 한국어로 핵심 요약
version: v2
author: 나
---

# 로컬 문서 요약

지정한 폴더의 \`.md\` / \`.txt\` 문서를 읽어 한국어로 핵심을 요약합니다.

## 규칙

- 문서당 **3줄 이내**로 요약
- **코드 블록은 제외** (요약 노이즈 방지)

## 절차

1. 지정 폴더의 문서를 수집한다 (Filesystem MCP).
2. 문서별로 3줄 요약을 생성한다.
`,
      },
      {
        path: 'scripts/run.sh',
        language: 'shell',
        content: `#!/usr/bin/env bash
set -euo pipefail

# 로컬 문서 요약 — 폴더의 .md/.txt 를 모아 요약 입력으로 정리
DIR="\${1:-.}"
find "$DIR" -type f \\( -name '*.md' -o -name '*.txt' \\) -print
`,
      },
    ],
  },
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

export const MY_PLUGINS: MyPlugin[] = [
  {
    id: 'pg1',
    name: 'watsonx Orchestrate 전문가',
    icon: 'flow',
    enabled: true,
    source: '마켓플레이스 (IBM 및 파트너)',
    version: '2.4.1',
    author: 'watsonx Orchestrate 팀',
    updated: '5일 전',
    summary: 'Orchestrate 워크플로 설계·스킬 연결·배포까지 제품 전반을 아는 전문가. 고객 데모와 PoC 구성을 함께 만들어줍니다.',
    update: { version: 'v2024.4', note: '제공사가 지식과 스킬을 갱신했습니다.' },
    skills: [
      { name: '워크플로 설계', desc: '요구사항을 단계별 Orchestrate 플로우로 변환' },
      { name: '스킬 연결', desc: '기존 스킬·도구를 워크플로에 배선' },
      { name: '배포 자동화', desc: '스케줄·트리거 기반 자동 실행 구성' },
      { name: '트러블슈팅', desc: '실행 오류 로그를 분석해 원인 제시' },
    ],
    knowledge: [
      { name: 'Orchestrate 제품 문서', desc: '워크플로·스킬·배포 공식 가이드', meta: '320 문서 · v2024.4' },
      { name: 'PoC·데모 플레이북', desc: '고객 데모와 PoC 구성 패턴 모음', meta: '48 문서' },
      { name: 'API 레퍼런스', desc: '트리거·커넥터·외부 API 호출 규격', meta: '갱신됨' },
    ],
    permissions: [
      { name: 'watsonx Orchestrate API', mode: '읽기·실행' },
      { name: '~/workflows', mode: '쓰기' },
    ],
    examples: [
      '매주 월요일 아침 매출 리포트를 자동 생성하는 워크플로 만들어줘',
      'Db2 조회 → 요약 → Slack 전송을 하나의 플로우로 연결해줘',
      '이 워크플로가 어제 실패한 이유를 분석해줘',
      '승인 단계가 포함된 휴가 신청 자동화를 설계해줘',
      'Orchestrate에서 외부 API를 호출하려면 어떻게 구성해?',
    ],
  },
  {
    id: 'pg2',
    name: 'Db2 전문가',
    icon: 'database',
    enabled: true,
    source: '마켓플레이스 (IBM 및 파트너)',
    version: '1.3.0',
    author: 'Db2 개발팀',
    updated: '1주 전',
    summary: 'Db2 튜닝·마이그레이션·장애 대응 지식을 갖춘 전문가. On-prem 설치와 성능 이슈를 함께 진단합니다.',
    skills: [
      { name: '성능 튜닝 진단', desc: '느린 쿼리의 실행 계획을 분석해 개선안 제시' },
      { name: '마이그레이션 점검', desc: '스키마·타입·제약 비교로 리스크 사전 진단' },
      { name: '장애 대응', desc: '에러 코드·로그 기반 원인 추적' },
    ],
    knowledge: [
      { name: 'Db2 성능 튜닝 가이드', desc: '실행 계획·인덱스·버퍼풀 운영', meta: '210 문서' },
      { name: '마이그레이션 핸드북', desc: 'watsonx.data 전환 체크리스트', meta: '64 문서' },
      { name: '오류 코드 사전', desc: 'SQLxxxxN 코드별 원인·대응', meta: '1,200 항목' },
    ],
    permissions: [{ name: 'db2-prod', mode: '읽기' }],
    examples: [
      '이 SELECT 쿼리가 느린 이유와 인덱스 제안을 알려줘',
      'Db2에서 watsonx.data로 옮길 때 깨질 수 있는 타입을 점검해줘',
      'SQL0911N 락 타임아웃 오류 대응 방법 알려줘',
      'sales 테이블의 스키마를 리뷰하고 개선점을 짚어줘',
      'buffer pool 크기를 어떻게 잡는 게 좋을까?',
    ],
  },
  {
    id: 'pg3',
    name: 'watsonx.governance 전문가',
    icon: 'rule',
    enabled: false,
    source: '마켓플레이스 (IBM 및 파트너)',
    version: '0.9.2',
    author: 'watsonx.governance 팀',
    updated: '3일 전',
    summary: '모델 거버넌스·리스크·규제 대응 프레임워크에 정통. 감사·컴플라이언스 질문과 고객 거버넌스 시나리오를 처리합니다.',
    update: { version: 'v1.0.0', note: '제공사가 지식과 스킬을 갱신했습니다.' },
    skills: [
      { name: '리스크 평가', desc: '모델 사용 시나리오의 리스크 등급 산정' },
      { name: '규제 매핑', desc: 'EU AI Act 등 규제 요건을 통제 항목에 매핑' },
      { name: '감사 리포트', desc: '거버넌스 활동을 감사용 문서로 정리' },
    ],
    knowledge: [
      { name: 'EU AI Act 해설', desc: '규제 조항·통제 항목 매핑', meta: '갱신됨' },
      { name: '거버넌스 프레임워크 문서', desc: '리스크 등급·감사 절차 표준', meta: '120 문서' },
    ],
    permissions: [{ name: 'model-registry', mode: '읽기' }],
    examples: [
      '이 모델의 EU AI Act 리스크 등급을 평가해줘',
      '컴플라이언스 감사를 위한 모델 카드 초안을 만들어줘',
      '편향 모니터링을 어떤 지표로 설정해야 해?',
      '규제 요건과 현재 통제 항목의 갭을 정리해줘',
      '프로덕션 모델의 거버넌스 체크리스트를 보여줘',
    ],
  },
];

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
  },
  {
    id: 'confluence',
    name: 'Confluence',
    icon: 'rule',
    category: '문서',
    provider: 'Atlassian',
    summary: '스페이스 문서를 검색해 답을 찾거나 보고서를 작성합니다.',
    examples: ['온보딩 문서를 요약해줘'],
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
  },
  {
    id: 'hubspot',
    name: 'HubSpot',
    icon: 'growth',
    category: '마케팅·CRM',
    provider: 'HubSpot',
    summary: '컨택·딜·캠페인 데이터를 읽어 리포트를 만듭니다.',
    examples: ['이번 달 신규 리드 추이를 요약해줘'],
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
  },
  {
    id: 'dropbox',
    name: 'Dropbox',
    icon: 'cloud',
    category: '스토리지',
    provider: 'Dropbox',
    summary: '폴더의 문서를 읽어 요약하거나 결과 파일을 저장합니다.',
    examples: ['이 폴더의 PDF들을 한 줄씩 요약해줘'],
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
  },
  {
    id: 'zendesk',
    name: 'Zendesk',
    icon: 'chat',
    category: '고객지원',
    provider: 'Zendesk',
    summary: '티켓을 읽어 이슈를 분류하고 응답 초안을 작성합니다.',
    examples: ['오늘 들어온 긴급 티켓을 정리해줘'],
  },
];

// Made with Bob
