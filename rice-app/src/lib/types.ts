/**
 * Common types for the RICE application
 */

// Runtime status
export interface RuntimeStatus {
  running: boolean;
  port?: number;
  pid?: number;
}

// Message types for chat
export interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: number;
  metadata?: Record<string, unknown>;
}

/** Per-turn tool approval policy selected in the chat composer. */
export type ApprovalMode = 'ask' | 'approve_for_me' | 'full_access';

// A single file bundled with a skill (README, instructions, scripts, license…)
// A skill is a bundle (folder), not a single file: SKILL.md + scripts/ + agents/
// + references/ + assets/. language drives syntax/icon in the source workspace.
export type SkillFileLanguage = 'markdown' | 'shell' | 'text' | 'python' | 'html' | 'json';

export interface SkillFile {
  /** Display path, e.g. "scripts/run.sh" */
  path: string;
  language: SkillFileLanguage;
  content: string;
}

// Skill definition (prompt-based workflow/capability)
export interface Skill {
  id: string;
  name: string;
  description: string;
  version: string;
  author: string;
  team?: string;
  category: string;
  tags: string[];
  downloads: number;
  rating: number;
  points: number;
  installed: boolean;
  enabled: boolean;
  /** 이 로컬 사용자가 게시한 마켓 항목. 설치 해제가 아니라 마켓 내리기 대상이다. */
  userPublished?: boolean;
  icon?: string;
  /** Bundled files shown in the detail modal. Falls back to a generated set. */
  files?: SkillFile[];
  /**
   * 파생본일 때 원작자 — 다운로드→개선→재공유한 fork. 마켓 카드/모달에서
   * "원작: {author} · 내가 개선" 같은 attribution을 표시한다.
   */
  forkedFrom?: { author: string; team?: string };
}

// A slash-command skill a plugin provides (invoked with "/" in chat)
export interface PluginSkill {
  /** Slash-command id, e.g. "deployment_troubleshooting" (rendered as /…) */
  command: string;
  desc: string;
}

// A data/tool connection a plugin grants the agent (Salesforce API, a DB, …)
export interface PluginConnector {
  name: string;
  desc?: string;
}

// A knowledge source a plugin draws on when answering
export interface PluginKnowledge {
  name: string;
  desc: string;
  /** Short scale/freshness note, e.g. "320 문서 · v2024.4" */
  meta?: string;
}

// Plugin definition
export interface Plugin {
  id: string;
  name: string;
  description: string;
  version: string;
  author: string;
  provider: string;
  category: string;
  tags: string[];
  skillCount: number;
  gains: string;
  useCases: string[];
  /** Slash-command skills the plugin provides (shown in the detail modal) */
  skills?: PluginSkill[];
  /** Data/tool connections the plugin grants (shown in the detail modal) */
  connectors?: PluginConnector[];
  /** Knowledge sources the plugin draws on (shown in the detail modal) */
  knowledge?: PluginKnowledge[];
  installed: boolean;
  enabled: boolean;
  /** Runtime package asset. It may be enabled/disabled, but not installed or removed. */
  builtin?: boolean;
  icon?: string;
}

// API response types
export interface ApiResponse<T = unknown> {
  success: boolean;
  data?: T;
  error?: string;
  message?: string;
  /** stable wire error identifier (RiceError.code) — 호출 측이 분기용으로 쓴다
   *  (예: 'provider_unavailable' → "런타임 오프라인" 안내).
   *  사용자 표시 문구는 lib/i18n/apiError.ts 의 apiErrorMessage(res) 로 지역화한다. */
  code?: string;
  /** 에러 문구 보간용 파라미터(RiceError.params) — errors 카탈로그가 {{detail}} 등으로 사용. */
  params?: Record<string, unknown>;
}

// ── SSE events (RICE_CONTRACTS 1.3) ──────────────────────────────
// 9 stream events + 2 control events (parking-based HITL approvals).
// The runtime owns contracts/sse-events.schema.json; this mirrors it so the
// app is wire-ready. Control events carry an approval_id (CONTRACTS 1.2).

export interface ThinkingEventData {
  text: string;
}

// ── plan-and-execute × ReAct (RICE_AGENT_EXECUTION.md §3) ──
// 복합 질의에서 플래너가 세운 턴 계획. plan_updated는 생성·단계 전이·수정 때마다
// FULL 스냅샷으로 온다 — 클라이언트는 plan_id로 카드를 통째로 교체한다(델타 없음).
// 같은 스냅샷이 메시지 metadata.plan으로도 영속화되어 리로드가 카드를 복원한다.
export type PlanStepStatus = 'pending' | 'in_progress' | 'done' | 'failed' | 'skipped';
export interface PlanStepData {
  id: string;
  title: string;
  detail?: string;
  status: PlanStepStatus;
  note?: string;
}
export interface PlanUpdatedEventData {
  plan_id: string;
  /** 해석된 사용자 의도 한 문장 (대화 언어) */
  goal: string;
  status: 'active' | 'completed' | 'abandoned';
  revision: number;
  steps: PlanStepData[];
}

export interface ToolCallEventData {
  tool_call_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
}
export interface ToolResultEventData {
  tool_call_id: string;
  ok: boolean;
  result?: unknown;
  error?: string;
}
export interface MessageEventData {
  /** incremental token (streaming) */
  delta?: string;
  /** full content (non-streaming / final) */
  content?: string;
}
export interface MemoryWrittenEventData {
  memory_type: 'fact' | 'preference';
  content: string;
  /** 'quarantined'면 승인 전까지 주입되지 않는다(외부 콘텐츠 유래). */
  status?: 'active' | 'quarantined';
  memory_id?: string;
  derived_from_external?: boolean;
}
export interface SkillPatchedEventData {
  skill_id: string;
  version: number;
}
/** 승인이 즉시 적용 대신 candidate 격리로 귀결됨 (candidate_eval_enabled 켜짐,
 * LEARNING_LOOP §10.6). skill_patched 대신 발행된다 — "적용됨"과 "평가 대기"는
 * 다른 사건. 이후 승격/폐기는 백그라운드라 GET /skills/candidates 로 관찰한다. */
export interface SkillCandidateStagedEventData {
  skill_id: string;
  candidate_id: string;
  /** 0 = 신규 생성(baseline 없음) */
  baseline_version: number;
}
export interface ErrorEventData {
  error_code: string;
  message: string;
}

// ── 백그라운드 셸 작업 (AGENT_EXECUTION §11) ──
// 오래 도는 terminal 명령이 background 로 뜨거나 auto-background 로 승격될 때
// task_started, 완료가 대화에 드레인될 때 task_completed. 활성 스트림에서만 온다
// (유휴 중 완료는 스트림이 없어 없지만, 완료 관찰은 다음 턴 히스토리로 전달된다 —
// 카드의 완료 출력은 GET /sessions/{id}/tasks 폴링이 채운다).
export type BackgroundTaskStatus = 'running' | 'exited' | 'killed' | 'interrupted';
export interface TaskStartedEventData {
  task_id: string;
  command: string;
}
export interface TaskCompletedEventData {
  task_id: string;
  status: BackgroundTaskStatus;
  exit_code: number | null;
}
/** GET /sessions/{id}/tasks 의 한 작업 — 실행 중이면 라이브 출력, 완료면 최종. */
export interface BackgroundTask {
  task_id: string;
  command: string;
  status: BackgroundTaskStatus;
  exit_code: number | null;
  started_at: string;
  ended_at: string | null;
  output: string;
}

/** what raised a patch proposal — audit trail (LEARNING_LOOP 5.2.2).
 * T4 = brand-new skill proposal (from_version=0, Phase A.1 — creation unified
 * into the patch flow instead of a separate skill_created event).
 * T5 = 미드턴 에이전트 발의 (Self-Improvement v3, propose_skill 빌트인). */
export type PatchTrigger = 'T1a' | 'T1b' | 'T1c' | 'T3' | 'T4' | 'T5';

export interface SkillDiffLine {
  type: 'add' | 'remove' | 'context';
  text: string;
}
export interface SkillDiff {
  lines: SkillDiffLine[];
}

// ── control events (out-of-band approvals, CONTRACTS 1.2) ──
export interface ToolApprovalRequiredData {
  approval_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  reason?: string;
}
export interface SkillPatchProposedData {
  approval_id: string;
  skill_id: string;
  changelog: string;
  diff: SkillDiff;
  trigger: PatchTrigger;
  from_version?: number;
  to_version?: number;
  /** authoritative proposed full body — prefer this over reconstructing from
   * diff.lines (which was a fragile client-side join, see ChatScreen.tsx) */
  proposed_body?: string;
  /** Metadata persisted with a new skill (from_version=0). Empty values are
   * explicit so the approval surface can show the exact write. */
  description?: string;
  tags?: string[];
  /** Metadata replacements persisted with an existing-skill patch. */
  new_description?: string;
  new_tags?: string[];
  /** Phase D guard redaction notices (secret-scrub/size-cap), empty/absent when clean */
  warnings?: string[];
  /** 외부 콘텐츠(도구 결과) 유래 제안인지 + 근거 메시지 id들(감사). */
  derived_from_external?: boolean;
  evidence_message_ids?: string[];
}

// ── suggestion inbox + learning ledger (Self-Improvement v3) ──
/** 영속 제안함 항목 — payload는 SkillPatchProposedData와 같은 어휘(approval_id 제외).
 * 파킹 타임아웃/백그라운드 발굴처럼 스트림 밖에서 태어난 제안이 여기 산다. */
export interface SkillSuggestion {
  id: string;
  /** 클라이언트 전용 — 라이브 파킹 미러(안전망)에만 존재하고 서버 inbox row엔 없다.
   *  존재하면 결정을 파킹 엔드포인트(respondSkillPatch, approval_id)로 라우팅한다. */
  approval_id?: string;
  created_at: string;
  origin: 'review' | 'learn' | 'agent' | 'discovery' | 'consolidation' | 'timeout' | 'review_sweep' | 'disconnected';
  kind: 'create' | 'patch' | 'archive';
  skill_id: string;
  payload: {
    skill_id: string;
    changelog: string;
    diff: SkillDiff;
    trigger: PatchTrigger;
    from_version: number;
    to_version: number;
    proposed_body: string;
    description?: string;
    tags?: string[];
    new_description?: string;
    new_tags?: string[];
    warnings?: string[];
    session_id?: string;
    derived_from_external?: boolean;
    evidence_message_ids?: string[];
    /** archive kind 전용 */
    reason?: string;
    absorbed_into?: string | null;
  };
  status: 'pending' | 'approved' | 'rejected' | 'expired';
  decided_at: string | null;
}

// ── candidate 승격 게이트 (LEARNING_LOOP §10.6, GET /skills/candidates) ──
/** 평가 리포트의 arm(baseline/candidate) 별 집계 (LEARNING_EVAL §10). */
export interface SkillCandidateArmMetrics {
  success_rate: number;
  avg_llm_calls: number;
  avg_output_chars: number;
  wins: number;
  losses: number;
  ties: number;
}
/** 평가 리포트 — 판정 + 그 판정의 신뢰도(evidence). 폐기돼도 리포트는 남는다. */
export interface SkillCandidateReport {
  verdict?: 'promote' | 'discard';
  reasons?: string[];
  version?: number;
  forced?: boolean;
  error?: string;
  mode?: string;
  rubric?: string[];
  baseline?: SkillCandidateArmMetrics;
  candidate?: SkillCandidateArmMetrics;
  evidence?: {
    pairings?: number;
    held_out_tasks?: number;
    paraphrase_tasks?: number;
    judge_consistency_checked?: boolean;
    judge_agreement_rate?: number | null;
    grounded_tool_result_rate?: number | null;
    tool_calls_observed?: number;
    success_stddev?: { baseline?: number; candidate?: number };
  };
}
/** 승인됐지만 아직 baseline-vs-candidate 평가를 통과하지 못한 제안 1건.
 * payload 는 제안함과 같은 어휘(+ 평가 전용 seed_prompts/tool_transcript). */
export interface SkillCandidate {
  id: string;
  created_at: string;
  skill_id: string;
  kind: 'create' | 'patch';
  status: 'pending' | 'running' | 'promoted' | 'discarded' | 'expired' | 'failed';
  baseline_version: number;
  baseline_body_hash: string | null;
  payload: SkillSuggestion['payload'] & {
    seed_prompts?: string[];
    tool_transcript?: unknown[];
    edited_body?: string;
  };
  attempts: number;
  claimed_at: string | null;
  decided_at: string | null;
  report: SkillCandidateReport | null;
}

/** 학습 감사 로그 한 줄 (GET /learning/events) */
export interface LearningEvent {
  id: string;
  ts: string;
  kind: string; // proposed | approved | rejected | timeout | memory_written | ...
  skill_id: string | null;
  session_id: string | null;
  origin: string | null;
  trigger: string | null;
  detail: Record<string, unknown> | null;
}

/** 런타임 메모리 항목 (GET /memories) */
export interface RuntimeMemory {
  id: string;
  kind: 'fact' | 'preference';
  content: string;
  /** 'quarantined'면 승인 전까지 주입 안 됨(외부 콘텐츠 유래). */
  status?: 'active' | 'quarantined';
  derived_from_external?: boolean;
  source_session_id?: string | null;
  source_message_ids?: string[];
}

export type SSEEvent =
  | { type: 'thinking'; data: ThinkingEventData }
  | { type: 'plan_updated'; data: PlanUpdatedEventData }
  | { type: 'tool_call'; data: ToolCallEventData }
  | { type: 'tool_result'; data: ToolResultEventData }
  | { type: 'message'; data: MessageEventData }
  | { type: 'memory_written'; data: MemoryWrittenEventData }
  | { type: 'skill_patched'; data: SkillPatchedEventData }
  | { type: 'skill_candidate_staged'; data: SkillCandidateStagedEventData }
  | { type: 'task_started'; data: TaskStartedEventData }
  | { type: 'task_completed'; data: TaskCompletedEventData }
  | { type: 'done'; data: Record<string, never> }
  | { type: 'error'; data: ErrorEventData }
  // control events
  | { type: 'tool_approval_required'; data: ToolApprovalRequiredData }
  | { type: 'skill_patch_proposed'; data: SkillPatchProposedData };

export type SSEEventType = SSEEvent['type'];

// ── approval responses (POST bodies, CONTRACTS 1.2/1.3) ──────────
export interface ToolApprovalDecision {
  approval_id: string;
  decision: 'approve' | 'reject';
}
export interface SkillPatchDecision {
  approval_id: string;
  decision: 'approve' | 'reject';
  /** edited full body when the user approves with edits */
  edited_body?: string;
}

/** an approval awaiting response — recovered on SSE reconnect via
 *  GET /sessions/{id}/pending-approvals (CONTRACTS 1.2) */
export interface PendingApproval {
  approval_id: string;
  kind: 'tool_approval' | 'skill_patch';
  payload: ToolApprovalRequiredData | SkillPatchProposedData;
}

// ── curator 통합 (Phase B, POST /skills/curate?mode=consolidate) ──────────
// approval_id 없음 — /learning-review와 달리 SSE 스트림이 아닌 일반 REST 응답에
// 그대로 실려온다. 승인/거부는 approval_id 조회 없이 이 데이터를 그대로 반송한다.
export interface ConsolidateMergeProposal {
  kind: 'patch';
  skill_id: string;
  changelog: string;
  diff: SkillDiff;
  trigger: PatchTrigger;
  proposed_body: string;
  from_version: number;
  to_version: number;
  description?: string;
  tags?: string[];
  warnings?: string[];
}
export interface ConsolidateArchiveProposal {
  kind: 'archive';
  skill_id: string;
  reason: string;
  absorbed_into: string | null;
  absorbed_into_version?: number | null;
  absorbed_into_body_hash?: string | null;
}
export type ConsolidateProposal = ConsolidateMergeProposal | ConsolidateArchiveProposal;
export interface CurateConsolidateResult {
  transitioned: [string, string][];
  proposals: ConsolidateProposal[];
}

// ── Learning Impact (eval results, RICE_LEARNING_EVAL 6.3) ────────
// UI depends only on this schema; design stage = seed fixture, runtime
// stage = harness output (eval/results/latest.json) — drop-in.
export interface ImpactMetricSet {
  turns: number;
  tool_calls: number;
  success_rate: number;
  skill_hit: boolean;
  skills_used?: string[];
}
export interface ImpactTaskDelta {
  turns_pct: number;
  tool_calls_pct: number;
  success_pp: number;
}
export interface ImpactTask {
  id: string;
  title: string;
  cold: ImpactMetricSet;
  warm: ImpactMetricSet;
  delta: ImpactTaskDelta;
}
export interface ImpactAggregate {
  turns_pct: number;
  tool_calls_pct: number;
  success_pp: number;
  skill_hit_rate: number;
}
export interface ImpactReport {
  task_suite: string;
  model: string;
  runs_per_task: number;
  generated_at: string;
  tasks: ImpactTask[];
  aggregate: ImpactAggregate;
}

// Settings
export interface AppSettings {
  runtime: {
    autoStart: boolean;
    port: number;
  };
  ui: {
    theme: 'light' | 'dark' | 'system';
    fontSize: 'small' | 'medium' | 'large';
  };
  api: {
    keys: Record<string, string>;
  };
}

// ── Platform API types (rice-platform, localhost:7070) ──────────
// registry._to_catalog_entry() 응답 스키마와 1:1 대응.
export interface PlatformArtifact {
  id: string;
  type: 'skill' | 'plugin';
  name: string;
  display_name: string;
  description: string;
  version: string;
  category: string;
  tags: string[];
  license: string;
  author_name: string;
  author_team: string;
  download_count: number;
  checksum: string;
  /** manifest.requires — runtime 버전 제약 + plugin이 요구하는 connector 목록 (CONTRACTS §2.1) */
  requires: { runtime?: string; connectors?: string[] };
  published_at: string;
  /** 게시·삭제를 관리하는 주체. 로컬 앱 게시본은 `rice-app`. */
  managed_by: string;
  bundled_resources: string[];
  skill_files: string[];
  has_bundled_resources: boolean;
  /** plugin이 요구하는 외부 연결 (.mcp.json mcpServers) — 이름·제목·설명 */
  connectors: { name: string; title?: string; description?: string }[];
  /** plugin이 제공하는 SKILL.md frontmatter 기반 스킬 메타 */
  plugin_skills?: { name: string; command?: string; description?: string }[];
  /** marketplace/detail에서 보여줄 추천 질문 */
  examples?: string[];
}

export interface PlatformFileResponse {
  path: string;
  content: string | null;
  language: string;
  binary?: boolean;
}

export interface PlatformPublishPayload {
  manifest: Record<string, unknown>;
  files: { path: string; content: string }[];
}
