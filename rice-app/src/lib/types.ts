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
  /** Slash-command id, e.g. "watsonx_troubleshooting_ocp" (rendered as /…) */
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
  icon?: string;
}

// API response types
export interface ApiResponse<T = unknown> {
  success: boolean;
  data?: T;
  error?: string;
  message?: string;
}

// ── SSE events (RICE_CONTRACTS 1.3) ──────────────────────────────
// 9 stream events + 2 control events (parking-based HITL approvals).
// The runtime owns contracts/sse-events.schema.json; this mirrors it so the
// app is wire-ready. Control events carry an approval_id (CONTRACTS 1.2).

export interface ThinkingEventData {
  text: string;
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
export interface SkillCreatedEventData {
  skill_id: string;
  title: string;
  version: number;
}
export interface MemoryWrittenEventData {
  memory_type: 'fact' | 'preference';
  content: string;
}
export interface SkillPatchedEventData {
  skill_id: string;
  version: number;
}
export interface ErrorEventData {
  error_code: string;
  message: string;
}

/** what raised a patch proposal — audit trail (LEARNING_LOOP 5.2.2) */
export type PatchTrigger = 'T1a' | 'T1b' | 'T1c' | 'T3';

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
}

export type SSEEvent =
  | { type: 'thinking'; data: ThinkingEventData }
  | { type: 'tool_call'; data: ToolCallEventData }
  | { type: 'tool_result'; data: ToolResultEventData }
  | { type: 'message'; data: MessageEventData }
  | { type: 'skill_created'; data: SkillCreatedEventData }
  | { type: 'memory_written'; data: MemoryWrittenEventData }
  | { type: 'skill_patched'; data: SkillPatchedEventData }
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
  bundled_resources: string[];
  skill_files: string[];
  has_bundled_resources: boolean;
  /** plugin이 요구하는 외부 연결 (.mcp.json mcpServers) — 이름·제목·설명 */
  connectors: { name: string; title?: string; description?: string }[];
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

// Made with Bob
