import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import i18n from '../lib/i18n';
import { apiErrorMessage } from '../lib/i18n/apiError';
import { SessionSidebar, type Session } from '../components/carbon/SessionSidebar';
import { ModelSettingsPanel } from '../components/carbon/ModelSettingsPanel';
import { LearningPanel } from '../components/carbon/LearningPanel';
import { ChatTurn } from '../components/carbon/ChatMessage';
import { useResizable } from '../hooks/useResizable';
import { useLearning } from '../lib/learning/LearningContext';
import type { ThreadMessage } from '../lib/learningData';
import { defaultModelSettings } from '../lib/mockData';
import { RUN_SKILL_EVENT, NEW_CHAT_EVENT, openCommandPalette } from '../lib/commandBridge';
import { api } from '../lib/api';
import { sseClient } from '../lib/sse';
import type { ApprovalMode, PatchTrigger, PendingApproval, PlanUpdatedEventData, ToolApprovalRequiredData, SkillPatchProposedData, MemoryWrittenEventData, SSEEvent } from '../lib/types';
import { CompanionCloudIcon, Icon, Kbd, ResizeHandle, ConfirmDialog, Tooltip, Toggle } from '../components/ui';
import type { IconName } from '../components/ui/Icon';
import { FileViewerProvider } from '../lib/fileViewer/FileViewerProvider';
import { useToast } from '../lib/toast/ToastContext';
import type { RegisteredModel } from '../components/carbon/ModelSettingsPanel';

// Electron(hiddenInset)에서만 좌상단에 macOS 트래픽 라이트가 있다. 고정 토글·헤더 여백을
// 이 값으로 gating해 브라우저 디자인 데모에서는 불필요한 좌측 여백을 두지 않는다.
const isElectron = navigator.userAgent.toLowerCase().includes('electron');

// 등록 모델의 provider(UI) → 런타임 provider kind 매핑.
// 'custom'(OpenAI 호환)도 openai kind로 — 같은 어댑터에 base_url만 다르다.
const providerKind = (provider?: string): 'claude' | 'openai' | 'watsonx' => {
  switch (provider) {
    case 'openai':
    case 'custom':
      return 'openai';
    case 'watsonx':
      return 'watsonx';
    default:
      return 'claude'; // anthropic + fallback
  }
};

/**
 * ChatScreen - Claude-Desktop-style chat with the learning-loop surface:
 * multi-step tool-call cards, the memory-written indicator, clickable
 * used-skill badges, a per-answer action bar, and an interactive learning
 * panel on the right.
 */

// ── live (state.db-backed) session/message mapping ─────────────────────────
// Shapes returned by GET /sessions and GET /sessions/{id}/messages (CONTRACTS 1.3).
type RawSession = { id: string; title: string | null; started_at?: string };
type RawToolCall = { id: string; tool_name: string; arguments?: Record<string, unknown> };
type RawMessage = {
  role: string;
  content: string;
  tool_calls?: RawToolCall[];
  /** epoch ms (server Message.ts field) — used to stamp sentAt on user bubbles */
  ts?: number;
  // plan: plan-and-execute 스냅샷 (plan_updated와 같은 형태) — 계획 생성 시 첫
  // 어시스턴트 메시지에, 이후 갱신마다 update_plan tool 메시지에 실린다.
  metadata?: {
    thinking?: string;
    tool_call_id?: string;
    tool_source?: string;
    plan?: PlanUpdatedEventData;
    synthetic?: string;
  } | null;
};

// Provider-compatible durable history retains the wrapper call the model emitted
// (`tool_call({name, arguments})`). Live SSE instead exposes the resolved target
// with the same outer id. Apply that same presentation-only unwrap on reload so
// both paths render one card for the actual action without rewriting provider
// history into a call to a folded (and therefore unadvertised) tool.
const unwrapBridgeToolCall = (call: RawToolCall): RawToolCall => {
  if (call.tool_name !== 'tool_call') return call;
  const name = call.arguments?.name;
  if (typeof name !== 'string' || !name) return call;
  const nested = call.arguments?.arguments;
  if (nested != null && (typeof nested !== 'object' || Array.isArray(nested))) return call;
  return {
    ...call,
    tool_name: name,
    arguments: (nested ?? {}) as Record<string, unknown>,
  };
};

type ThreadStateUpdate = ThreadMessage[] | ((previous: ThreadMessage[]) => ThreadMessage[]);

type LiveTurnProgress = {
  sessionId: string;
};

type RuntimeModelSettings = {
  provider?: {
    kind?: string;
    model?: string;
    base_url?: string;
    temperature?: number;
    configured?: boolean;
    show_thinking?: boolean;
  };
  registered_models?: RegisteredModel[];
};

// Human-readable, per-tool copy for the HITL approval card — derived client-side
// from the tool name + args, so the confirmation line is contextual with ZERO
// added latency (no extra model call). Extend the map as builtins grow.

const describeTool = (tool: string, args: Record<string, unknown>): { label: string; reason: string; approved: string; denied: string } => {
  const t = i18n.t.bind(i18n);
  const path = typeof args.path === 'string' ? (args.path as string) : undefined;
  switch (tool) {
    case 'write_file':
      return {
        label: path ? t('chat:tools.writeFile', { path }) : t('chat:tools.writeFileDefault'),
        reason: t('chat:tools.writeFileReason'),
        approved: path ? t('chat:tools.writeFileApproved', { path }) : t('chat:tools.writeFileApprovedDefault'),
        denied: t('chat:tools.writeFileDenied'),
      };
    case 'read_file':
      return {
        label: path ? t('chat:tools.readFile', { path }) : t('chat:tools.readFileDefault'),
        reason: t('chat:tools.readFileReason'),
        approved: path ? t('chat:tools.readFileApproved', { path }) : t('chat:tools.readFileApprovedDefault'),
        denied: t('chat:tools.readFileDenied'),
      };
    default:
      return {
        label: tool,
        reason: t('chat:tools.defaultReason'),
        approved: t('chat:tools.defaultApproved', { tool }),
        denied: t('chat:tools.defaultDenied'),
      };
  }
};

const toSession = (s: RawSession): Session => ({
  id: s.id,
  title: s.title || i18n.t('chat:untitledConversation'),
  // sqlite CURRENT_TIMESTAMP is UTC, "YYYY-MM-DD HH:MM:SS" — normalize to a Date.
  timestamp: s.started_at ? new Date(s.started_at.replace(' ', 'T') + 'Z') : new Date(),
  messageCount: 0, // list endpoint doesn't return a count; not shown for live sessions
});

// plan_updated 스냅샷 → 스레드 블록. 라이브 스트림과 리로드 복원이 같은 매핑을 쓴다.
const planToBlock = (d: PlanUpdatedEventData): Extract<ThreadMessage, { kind: 'plan' }> => ({
  kind: 'plan',
  planId: d.plan_id,
  goal: d.goal,
  status: d.status,
  revision: d.revision,
  steps: d.steps,
});

// 같은 planId의 계획 카드는 하나 — 있으면 그 자리에서 스냅샷으로 교체, 없으면 append.
const upsertPlanBlock = (blocks: ThreadMessage[], d: PlanUpdatedEventData): ThreadMessage[] => {
  const idx = blocks.findIndex((b) => b.kind === 'plan' && b.planId === d.plan_id);
  if (idx >= 0) {
    const copy = [...blocks];
    copy[idx] = planToBlock(d);
    return copy;
  }
  return [...blocks, planToBlock(d)];
};

// Reconstruct the full agentic trace from persisted messages so a reloaded
// conversation looks like the live turn: reasoning (assistant `metadata.thinking`)
// → plan card (metadata `plan`, 마지막 스냅샷 승리) → tool call cards (assistant
// `tool_calls`) → results (role='tool', matched by `metadata.tool_call_id`) →
// answer (assistant `content`).
const toThread = (msgs: RawMessage[]): ThreadMessage[] => {
  let out: ThreadMessage[] = [];
  // Only calls whose persisted result says source="bridge" are wrappers. A real
  // connector/plugin is allowed to be named `tool_call`; unwrapping by name alone
  // would make that card change shape after reload.
  const bridgeCallIds = new Set(
    msgs
      .filter((m) => m.role === 'tool' && m.metadata?.tool_source === 'bridge')
      .map((m) => m.metadata?.tool_call_id)
      .filter((id): id is string => typeof id === 'string'),
  );
  for (const m of msgs) {
    if (m.role === 'user') {
      // 루프가 주입한 합성 메시지(빈 응답 넛지 등)는 사용자가 친 말이 아니다 — 숨긴다.
      if (m.content && !m.metadata?.synthetic) out.push({ kind: 'user', text: m.content, sentAt: m.ts });
    } else if (m.role === 'assistant') {
      if (m.metadata?.thinking) out.push({ kind: 'thinking', duration: '', text: m.metadata.thinking, streaming: false });
      if (m.metadata?.plan) out = upsertPlanBlock(out, m.metadata.plan);
      // update_plan·start_plan의 화면 표면은 계획 카드다 — 별도 도구 카드는 노이즈.
      const realCalls = (m.tool_calls ?? []).map((tc) => (
        bridgeCallIds.has(tc.id) ? unwrapBridgeToolCall(tc) : tc
      )).filter(
        (tc) => tc.tool_name !== 'update_plan' && tc.tool_name !== 'start_plan',
      );
      // 본문 + 도구 호출이 같이 있으면 그 본문은 행동의 preamble(rationale) — 첫
      // 도구 카드의 캡션으로 붙인다(라이브의 tool_call 리듀서와 대칭). 본문만
      // (도구 없음) 있으면 그건 최종 답변 → agent 버블. 이 규칙이 리로드와
      // 라이브가 같은 그림을 그리게 한다.
      const rationale = m.content && realCalls.length > 0 ? m.content : undefined;
      realCalls.forEach((tc, i) => {
        out.push({
          kind: 'toolcall',
          callId: tc.id,
          tool: tc.tool_name,
          args: tc.arguments ?? {},
          status: 'done',
          rationale: i === 0 ? rationale : undefined,
        });
      });
      if (m.content && realCalls.length === 0) out.push({ kind: 'agent', text: m.content, sentAt: m.ts });
    } else if (m.role === 'tool') {
      // update_plan 결과 메시지가 실은 최신 계획 스냅샷 → 카드 갱신.
      if (m.metadata?.plan) {
        out = upsertPlanBlock(out, m.metadata.plan);
        continue;
      }
      // attach this result to its tool call card (by id, else the latest open one)
      const callId = m.metadata?.tool_call_id;
      for (let i = out.length - 1; i >= 0; i--) {
        const b = out[i];
        if (b.kind === 'toolcall' && (callId ? b.callId === callId : b.result == null && b.error == null)) {
          out[i] = { ...b, result: m.content };
          break;
        }
      }
    }
  }
  return out;
};

// 트리거 코드 → 사람이 읽는 한 줄 라벨 (LEARNING_LOOP 5.2.2, T4는 Phase A.1,
// T5는 Self-Improvement v3 — 대화 중 에이전트가 propose_skill로 직접 발의).
const getTriggerLabels = (): Record<PatchTrigger, string> => ({
  T1a: i18n.t('chat:trigger.T1a'),
  T1b: i18n.t('chat:trigger.T1b'),
  T1c: i18n.t('chat:trigger.T1c'),
  T3: i18n.t('chat:trigger.T3'),
  T4: i18n.t('chat:trigger.T4'),
  T5: i18n.t('chat:trigger.T5'),
});

// Map a parked approval (recovered after an SSE reconnect via
// GET /sessions/{id}/pending-approvals, CONTRACTS 1.2) into a thread card so the
// prompt re-renders. Approval state lives on the server, independent of the SSE
// connection — the client just re-paints what's still pending.
const pendingToThread = (p: PendingApproval): ThreadMessage | null => {
  const t = i18n.t.bind(i18n);
  if (p.kind === 'tool_approval') {
    const d = p.payload as ToolApprovalRequiredData;
    return {
      kind: 'approval',
      approvalId: p.approval_id,
      tool: d.tool_name,
      label: d.tool_name,
      scope: { name: d.tool_name, mode: t('chat:tools.executionMode') },
      reason: d.reason ?? t('chat:tools.defaultReason'),
    };
  }
  const d = p.payload as SkillPatchProposedData;
  const isNew = d.from_version === 0; // Phase A.1: 생성도 patch(from_version=0)로 온다
  return {
    kind: 'patch',
    approvalId: p.approval_id,
    skillId: d.skill_id,
    skillName: d.skill_id,
    // from_version은 0이 유효값(신규 생성)이라 `d.from_version ? ... : ...`(0을
    // falsy로 오판)를 쓰면 안 된다 — undefined만 "값 없음"으로 취급한다.
    fromVer: isNew ? t('chat:patch.newVersion') : d.from_version !== undefined ? `v${d.from_version}` : t('chat:patch.currentVersion'),
    toVer: d.to_version !== undefined ? `v${d.to_version}` : t('chat:patch.improvedVersion'),
    changelog: d.changelog,
    trigger: d.trigger,
    triggerLabel: getTriggerLabels()[d.trigger] ?? d.trigger,
    diff: d.diff.lines.map((l) => ({ t: l.type === 'context' ? 'ctx' : l.type, text: l.text })),
    // 서버가 준 원문을 그대로 쓴다(버그#0 견고성 개선) — diff.lines 재구성은 폴백만.
    proposedBody:
      d.proposed_body ??
      d.diff.lines
        .filter((l) => l.type !== 'remove')
        .map((l) => l.text)
        .join('\n'),
    warnings: d.warnings,
  };
};

/**
 * SkillsPopover — composer command palette for "이 대화에서 쓸 수 있는 것".
 * Lists what the agent can use, grouped by type (스킬 / 도구 / 플러그인), each
 * as a "/" slash command. Clicking inserts the command into the composer.
 * Plugins are bundles: expandable to the slash-command skills they provide.
 */
const SkillsPopover: React.FC<{
  onInsert: (command: string) => void;
  onClose: () => void;
  onManage: () => void;
  webSearchEnabled: boolean;
  onWebSearchChange: (enabled: boolean) => void;
  /** Called when the user presses Escape — lets the parent return focus to the composer */
  onEscape?: () => void;
  /** Pre-fill the search box (used when triggered by inline "/" typing) */
  initialQuery?: string;
  /** When true the popover is triggered inline — suppress the outside-click close
   *  on the textarea itself so the user can keep typing to filter */
  inlineMode?: boolean;
}> = ({ onInsert, onClose, onManage, webSearchEnabled, onWebSearchChange, onEscape, initialQuery = '', inlineMode = false }) => {
  const { skills, installed } = useLearning();
  const { t } = useTranslation('chat');
  const ref = useRef<HTMLDivElement>(null);
  const [q, setQ] = useState(initialQuery);

  // Sync external query changes (user keeps typing after "/")
  useEffect(() => {
    setQ(initialQuery);
  }, [initialQuery]);
  // Plugins start expanded so their slash commands are visible at a glance.
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(installed.filter((t) => t.kind === 'plugin' && t.enabled).map((t) => t.id)),
  );

  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        // In inline mode the textarea is outside the popover but should NOT close it
        // (the user might still be typing). Close is handled by the parent instead.
        if (!inlineMode) onClose();
      }
    };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, [onClose, inlineMode]);

  const query = q.trim().toLowerCase();
  const hit = (...parts: (string | undefined)[]) =>
    !query || parts.some((p) => p?.toLowerCase().includes(query));

  // Active-only, grouped by type
  const skillItems = skills.filter((s) => s.enabled && s.command && hit(s.name, s.command));
  const toolItems = installed.filter((t) => t.enabled && t.kind === 'tool' && hit(t.name, t.command, t.meta));
  const pluginItems = installed
    .filter((t) => t.enabled && t.kind === 'plugin')
    .map((p) => {
      // When the plugin name itself matches, surface all of its commands.
      const nameHit = hit(p.name);
      const provides = (p.provides ?? []).filter((pr) => nameHit || hit(pr.command, pr.desc));
      return { plugin: p, provides };
    })
    .filter(({ plugin, provides }) => provides.length > 0 || hit(plugin.name));

  const empty = skillItems.length === 0 && toolItems.length === 0 && pluginItems.length === 0;

  // 키보드 탐색: 화면에 보이는 삽입 가능 명령을 렌더 순서 그대로 평탄화
  const visibleCommands: string[] = [
    ...skillItems.map((s) => s.command!),
    ...toolItems.map((t) => t.command ?? t.id),
    ...pluginItems.flatMap(({ plugin, provides }) =>
      (query ? true : expanded.has(plugin.id)) ? provides.map((pr) => pr.command) : [],
    ),
  ];
  const [active, setActive] = useState(0);
  const activeIdx = Math.min(active, Math.max(visibleCommands.length - 1, 0));
  useEffect(() => setActive(0), [query]);

  // 그룹별 시작 인덱스 (visibleCommands 평탄화 순서와 동일)
  const toolOffset = skillItems.length;
  const pluginOffset = toolOffset + toolItems.length;
  const pluginStarts: number[] = [];
  {
    let acc = pluginOffset;
    for (const { plugin, provides } of pluginItems) {
      pluginStarts.push(acc);
      if (query ? true : expanded.has(plugin.id)) acc += provides.length;
    }
  }

  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [activeIdx]);

  const insert = (command: string) => {
    onClose();
    onInsert(command);
  };
  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // 카테고리별 액센트 (globals.css 도메인 액센트와 동기)
  const ACCENT = {
    skill: 'var(--accent-skill)',
    tool: 'var(--accent-connector)',
    plugin: 'var(--accent-plugin)',
  } as const;

  // A clickable slash-command row
  const CmdRow: React.FC<{
    icon: Parameters<typeof Icon>[0]['name'];
    command: string;
    sub: string;
    tint: string;
    index: number;
    indent?: boolean;
  }> = ({ icon, command, sub, tint, index, indent }) => {
    const isActive = index === activeIdx;
    return (
      <button
        data-active={isActive || undefined}
        onClick={() => insert(command)}
        onMouseEnter={() => setActive(index)}
        className={`relative flex items-center gap-2.5 w-full py-1.5 pr-2.5 text-left transition-colors ${
          indent ? 'pl-8' : 'pl-3'
        } ${isActive ? 'bg-layer-hover-02' : ''}`}
      >
        {isActive && (
          <span className="absolute left-0 top-1 bottom-1 w-0.5 rounded-full" style={{ background: tint }} />
        )}
        <span
          className="w-6 h-6 rounded-md flex items-center justify-center flex-shrink-0"
          style={{ color: tint, background: `color-mix(in srgb, ${tint} 13%, transparent)` }}
        >
          <Icon name={icon} size={13} />
        </span>
        <div className="flex-1 min-w-0">
          <div className="font-mono text-body-short-01 text-text-primary truncate">
            <span className="text-text-helper">/</span>
            {command}
          </div>
          <div className="text-caption-01 text-text-helper truncate">{sub}</div>
        </div>
        <span
          className={`text-label-micro font-mono text-text-helper transition-opacity flex-shrink-0 ${
            isActive ? 'opacity-100' : 'opacity-0'
          }`}
        >
          {t('skills.insertHint')}
        </span>
      </button>
    );
  };

  const GroupLabel: React.FC<{ children: React.ReactNode; count: number; tint: string }> = ({
    children,
    count,
    tint,
  }) => (
    <div className="flex items-center gap-1.5 px-3 pt-2.5 pb-1">
      <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: tint }} />
      <span className="text-label-micro font-semibold uppercase tracking-wider text-text-helper">{children}</span>
      <span className="text-label-micro font-mono text-text-helper">{count}</span>
    </div>
  );

  return (
    <div
      ref={ref}
      className="absolute z-30 w-[340px] bottom-full mb-2 left-0 bg-layer-02/95 backdrop-blur-md border border-border-subtle-01 rounded-xl shadow-elevation-3 overflow-hidden flex flex-col"
      style={{ animation: 'popoverIn 140ms cubic-bezier(0.16, 1, 0.3, 1)' }}
    >
      {/* Header + search */}
      <div className="px-3 pt-3 pb-2 flex-shrink-0">
        <div className="flex items-center gap-1.5 mb-2">
          <Icon name="skill" size={14} className="text-icon-primary" />
          <span className="text-body-short-01 font-semibold text-text-primary">{t('skills.popoverTitle')}</span>
          <span className="ml-auto text-label-micro font-mono text-text-helper">
            <span className="text-text-secondary">/</span> {t('skills.popoverCallHint')}
          </span>
        </div>
        <div className="flex items-center gap-1.5 px-2 h-7 bg-layer-01 border border-border-subtle-01 rounded-md focus-within:border-border-strong-02 transition-colors">
          <Icon name="search" size={13} className="text-icon-secondary flex-shrink-0" />
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                e.preventDefault();
                onClose();
                onEscape?.();
              } else if (e.key === 'ArrowDown') {
                e.preventDefault();
                setActive((a) => Math.min(a + 1, Math.max(visibleCommands.length - 1, 0)));
              } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                setActive((a) => Math.max(a - 1, 0));
              } else if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                e.preventDefault();
                const cmd = visibleCommands[activeIdx];
                if (cmd) insert(cmd);
              }
            }}
            placeholder={t('skills.searchPlaceholder')}
            className="flex-1 min-w-0 bg-transparent border-none outline-none text-caption-01 text-text-primary placeholder:text-text-placeholder"
          />
        </div>
      </div>

      {/* Body */}
      <div ref={listRef} className="max-h-[332px] overflow-y-auto pb-1">
        <div className="mx-2 mb-1 flex items-center gap-2.5 rounded-lg border border-border-subtle-01 bg-layer-01 px-2.5 py-2">
          <span className={`flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md ${webSearchEnabled ? 'bg-blue-60/15 text-blue-30' : 'bg-layer-02 text-icon-secondary'}`}>
            <Icon name="search" size={14} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-caption-01 font-medium text-text-primary">{t('skills.webSearch')}</div>
            <div className="truncate text-label-micro text-text-helper">{t('skills.webSearchHint')}</div>
          </div>
          <Toggle
            checked={webSearchEnabled}
            onChange={onWebSearchChange}
            aria-label={t('skills.webSearch')}
          />
        </div>
        {empty && (
          <div className="flex flex-col items-center gap-1.5 px-3 py-6 text-center">
            <Icon name="search" size={18} className="text-icon-secondary opacity-50" />
            <div className="text-caption-01 text-text-helper">{t('skills.emptyResult')}</div>
          </div>
        )}

        {skillItems.length > 0 && (
          <div>
            <GroupLabel count={skillItems.length} tint={ACCENT.skill}>{t('skills.groupSkills')}</GroupLabel>
            {skillItems.map((s, i) => (
              <CmdRow
                key={s.id}
                icon="skill"
                command={s.command!}
                sub={t('skills.usageCount', { ver: s.ver, count: s.uses })}
                tint={ACCENT.skill}
                index={i}
              />
            ))}
          </div>
        )}

        {toolItems.length > 0 && (
          <div>
            <GroupLabel count={toolItems.length} tint={ACCENT.tool}>{t('skills.groupTools')}</GroupLabel>
            {toolItems.map((tool, i) => (
              <CmdRow
                key={tool.id}
                icon={tool.icon}
                command={tool.command ?? tool.id}
                sub={tool.meta}
                tint={ACCENT.tool}
                index={toolOffset + i}
              />
            ))}
          </div>
        )}

        {pluginItems.length > 0 && (
          <div>
            <GroupLabel count={pluginItems.length} tint={ACCENT.plugin}>{t('skills.groupPlugins')}</GroupLabel>
            {pluginItems.map(({ plugin, provides }, pi) => {
              const open = query ? true : expanded.has(plugin.id);
              return (
                <div key={plugin.id}>
                  <button
                    onClick={() => toggle(plugin.id)}
                    className="flex items-center gap-2.5 w-full px-3 py-1.5 text-left hover:bg-layer-hover-02 transition-colors"
                  >
                    <span
                      className="w-6 h-6 rounded-md flex items-center justify-center flex-shrink-0"
                      style={{
                        color: ACCENT.plugin,
                        background: `color-mix(in srgb, ${ACCENT.plugin} 13%, transparent)`,
                      }}
                    >
                      <Icon name={plugin.icon} size={13} />
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="text-body-short-01 text-text-primary truncate">{plugin.name}</div>
                      <div className="text-caption-01 text-text-helper truncate">
                        {t('skills.pluginProvides', { count: provides.length, meta: plugin.meta })}
                      </div>
                    </div>
                    <Icon
                      name={open ? 'chevron-up' : 'chevron-down'}
                      size={13}
                      className="text-icon-secondary opacity-60 flex-shrink-0"
                    />
                  </button>
                  {open &&
                    provides.map((pr, j) => (
                      <CmdRow
                        key={pr.command}
                        icon="skill"
                        command={pr.command}
                        sub={pr.desc}
                        tint={ACCENT.plugin}
                        index={pluginStarts[pi] + j}
                        indent
                      />
                    ))}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="flex items-center border-t border-border-subtle-01 flex-shrink-0">
        <button
          onClick={() => {
            onClose();
            onManage();
          }}
          className="flex items-center gap-1.5 px-3 py-2.5 text-caption-01 text-link-primary hover:bg-layer-hover-02 transition-colors"
        >
          <Icon name="settings" size={14} /> {t('skills.manageLink')}
        </button>
        <div className="ml-auto flex items-center gap-1 pr-3 text-label-micro text-text-helper select-none">
          <Kbd>↑↓</Kbd> {t('skills.navHint').replace('↑↓ ', '')}
          <Kbd>↵</Kbd> {t('skills.enterHint').replace('↵ ', '')}
          <Kbd>esc</Kbd> {t('skills.escHint').replace('esc ', '')}
        </div>
      </div>

      <style>{`
        @keyframes popoverIn {
          from { opacity: 0; transform: translateY(6px) scale(0.98); }
          to { opacity: 1; transform: none; }
        }
      `}</style>
    </div>
  );
};

const APPROVAL_ICONS: Record<ApprovalMode, IconName> = {
  ask: 'lock',
  approve_for_me: 'check-circle',
  full_access: 'warning',
};

/** Codex-style per-turn approval policy selector. Tool cards remain unchanged;
 * this only decides whether an approval gate is automatic or interactive. */
const ApprovalModePicker: React.FC<{
  value: ApprovalMode;
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  onChange: (value: ApprovalMode) => void;
}> = ({ value, open, onToggle, onClose, onChange }) => {
  const { t } = useTranslation('chat');
  const ref = useRef<HTMLDivElement>(null);
  const approvalOptions: Array<{ value: ApprovalMode; label: string; description: string; icon: IconName }> = [
    { value: 'ask',            label: t('approval.ask.label'),          description: t('approval.ask.description'),          icon: APPROVAL_ICONS.ask },
    { value: 'approve_for_me', label: t('approval.approveForMe.label'), description: t('approval.approveForMe.description'), icon: APPROVAL_ICONS.approve_for_me },
    { value: 'full_access',    label: t('approval.fullAccess.label'),   description: t('approval.fullAccess.description'),   icon: APPROVAL_ICONS.full_access },
  ];
  const selected = approvalOptions.find((option) => option.value === value) ?? approvalOptions[0];

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) onClose();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [onClose, open]);

  const fullAccess = value === 'full_access';
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={onToggle}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={`flex h-6 items-center gap-1.5 rounded-full border px-2 text-label-micro font-medium transition-colors ${
          fullAccess
            ? 'border-yellow-50/35 bg-yellow-50/10 text-yellow-30 hover:bg-yellow-50/15'
            : open
              ? 'border-interactive/40 bg-interactive/15 text-blue-30'
              : 'border-border-subtle-01 text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary'
        }`}
      >
        <Icon name={selected.icon} size={12} />
        {selected.label}
        <Icon name={open ? 'chevron-up' : 'chevron-down'} size={10} className="opacity-70" />
      </button>

      {open && (
        <div
          role="listbox"
          aria-label={t('approval.listboxLabel')}
          className="absolute bottom-full left-0 z-40 mb-2 w-[340px] max-w-[calc(100vw-40px)] overflow-hidden rounded-lg border border-border-subtle-01 bg-layer-02/95 p-1.5 shadow-elevation-2 backdrop-blur-md"
          style={{ animation: 'popoverIn 140ms cubic-bezier(0.16, 1, 0.3, 1)' }}
        >
          <div className="flex items-center gap-2 px-2 pb-1.5 pt-1">
            <span className="text-caption-01 font-semibold text-text-primary">{t('approval.permissionTitle')}</span>
            <span className="ml-auto text-label-micro text-text-helper">{t('approval.permissionHint')}</span>
          </div>
          {approvalOptions.map((option) => {
            const active = value === option.value;
            const danger = option.value === 'full_access';
            return (
              <button
                key={option.value}
                type="button"
                role="option"
                aria-selected={active}
                onClick={() => {
                  onChange(option.value);
                  onClose();
                }}
                className={`relative flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left transition-colors ${
                  active ? 'bg-layer-selected-01' : 'hover:bg-layer-hover-02'
                }`}
              >
                {active && <span className={`absolute bottom-2 left-0 top-2 w-0.5 rounded-full ${danger ? 'bg-yellow-30' : 'bg-interactive'}`} />}
                <span className={`flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md ${
                  danger ? 'bg-yellow-50/10 text-yellow-30' : active ? 'bg-blue-60/15 text-blue-30' : 'bg-layer-01 text-icon-secondary'
                }`}>
                  <Icon name={option.icon} size={14} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-caption-01 font-medium text-text-primary">{option.label}</span>
                  <span className="block truncate text-label-micro leading-4 text-text-helper">{option.description}</span>
                </span>
                {active && <Icon name="check" size={14} className={`flex-shrink-0 ${danger ? 'text-yellow-30' : 'text-link-primary'}`} />}
              </button>
            );
          })}
          <div className="mx-2 mt-1 border-t border-border-subtle-01 px-0.5 pb-0.5 pt-1.5 text-label-micro leading-4 text-text-helper">
            {t('approval.blockedNote')}
          </div>
        </div>
      )}
    </div>
  );
};

/* ── 빈 채팅 스타터 화면 ──
 * 새 대화에서는 사용자가 "무엇부터 봐야 하지?"라고 느끼지 않도록 질문 하나와
 * 예시 두 개만 보여준다. 학습 기록·스킬·사용 횟수는 오른쪽 Learning panel과
 * composer의 + 메뉴가 담당한다. 예시 클릭도 즉시 실행하지 않고 입력 초안만
 * 채워, 사용자가 내용을 확인한 뒤 직접 전송할 수 있게 한다.
 */
const ChatStarter: React.FC<{
  onPick: (text: string) => void;
  active: boolean;
}> = ({ onPick, active }) => {
  const { t } = useTranslation('chat');
  const [companionPopping, setCompanionPopping] = useState(false);
  const companionPopFrame = useRef<number | null>(null);

  const popCompanion = useCallback(() => {
    if (companionPopFrame.current !== null) {
      window.cancelAnimationFrame(companionPopFrame.current);
    }
    setCompanionPopping(false);
    companionPopFrame.current = window.requestAnimationFrame(() => {
      setCompanionPopping(true);
      companionPopFrame.current = null;
    });
  }, []);

  // ChatScreen은 다른 화면에서도 스트림 보존을 위해 mount 상태로 숨겨진다.
  // 따라서 mount가 아니라 실제 Chat route 진입을 기준으로 한 번 재생한다.
  useEffect(() => {
    if (active) popCompanion();
    else setCompanionPopping(false);

    return () => {
      if (companionPopFrame.current !== null) {
        window.cancelAnimationFrame(companionPopFrame.current);
        companionPopFrame.current = null;
      }
    };
  }, [active, popCompanion]);

  type StarterSuggestion = {
    icon: IconName;
    title: string;
    prompt: string;
    accent: string;
    accentBg: string;
  };

  const suggestions: Record<'morning' | 'lunch' | 'afternoon' | 'dinner' | 'afterWork' | 'lateNight' | 'ppt', StarterSuggestion> = {
    morning: {
      icon: 'calendar',
      title: t('starter.morning'),
      prompt: t('starter.morningPrompt'),
      accent: 'text-blue-50',
      accentBg: 'bg-blue-60/10',
    },
    lunch: {
      icon: 'sparkle',
      title: t('starter.lunch'),
      prompt: t('starter.lunchPrompt'),
      accent: 'text-orange-30',
      accentBg: 'bg-orange-40/10',
    },
    afternoon: {
      icon: 'idea',
      title: t('starter.afternoon'),
      prompt: t('starter.afternoonPrompt'),
      accent: 'text-purple-50',
      accentBg: 'bg-purple-60/10',
    },
    dinner: {
      icon: 'sparkle',
      title: t('starter.dinner'),
      prompt: t('starter.dinnerPrompt'),
      accent: 'text-orange-30',
      accentBg: 'bg-orange-40/10',
    },
    afterWork: {
      icon: 'growth',
      title: t('starter.afterWork'),
      prompt: t('starter.afterWorkPrompt'),
      accent: 'text-teal-40',
      accentBg: 'bg-teal-60/10',
    },
    lateNight: {
      icon: 'idea',
      title: t('starter.lateNight'),
      prompt: t('starter.lateNightPrompt'),
      accent: 'text-purple-40',
      accentBg: 'bg-purple-60/10',
    },
    ppt: {
      icon: 'document',
      title: t('starter.ppt'),
      prompt: t('starter.pptPrompt'),
      accent: 'text-blue-50',
      accentBg: 'bg-blue-60/10',
    },
  };

  const hour = new Date().getHours();
  const STARTERS: StarterSuggestion[] =
    hour < 5
      ? [suggestions.lateNight, suggestions.ppt]
      : hour < 11
        ? [suggestions.morning, suggestions.ppt]
        : hour < 14
          ? [suggestions.lunch, suggestions.ppt]
          : hour < 17
            ? [suggestions.afternoon, suggestions.ppt]
            : hour < 20
              ? [suggestions.dinner, suggestions.afterWork]
              : hour < 23
                ? [suggestions.afterWork, suggestions.ppt]
                : [suggestions.lateNight, suggestions.ppt];

  return (
    <div className="mx-auto w-full max-w-[640px] pb-6 text-center">
      <button
        type="button"
        onClick={popCompanion}
        onAnimationEnd={() => setCompanionPopping(false)}
        className={`starter-companion mx-auto mb-4 block h-16 w-[72px] outline-none ${companionPopping ? 'is-popping' : ''}`}
        aria-label={t('starter.companionLabel')}
        title={t('starter.companionLabel')}
      >
        <CompanionCloudIcon withBurst className="h-full w-full overflow-visible" />
      </button>
      <h1 className="mb-8 text-heading-04 font-light text-text-primary">{t('starter.greeting')}</h1>

      <div className="grid grid-cols-1 gap-3 text-left sm:grid-cols-2">
        {STARTERS.map((starter, index) => (
          <button
            key={starter.title}
            type="button"
            onClick={() => onPick(starter.prompt)}
            aria-label={`${starter.title}: ${starter.prompt}`}
            className="group flex h-[92px] flex-col items-start justify-between rounded-xl border border-border-subtle-01 bg-layer-01/35 p-4 transition-colors duration-150 hover:border-border-strong-01 hover:bg-layer-hover-01"
            style={{ animation: 'starterCardIn 220ms cubic-bezier(0.16, 1, 0.3, 1) both', animationDelay: `${index * 35}ms` }}
          >
            <span className={`flex h-7 w-7 items-center justify-center rounded-lg ${starter.accentBg} ${starter.accent}`}>
              <Icon name={starter.icon} size={15} />
            </span>
            <span className="text-body-short-01 font-medium text-text-primary">{starter.title}</span>
          </button>
        ))}
      </div>

      <style>{`
        .starter-companion {
          color: var(--cds-text-secondary);
          -webkit-tap-highlight-color: transparent;
        }
        .starter-companion__puff {
          transform-box: fill-box;
          transform-origin: center;
          transition: transform 240ms cubic-bezier(0.16, 1, 0.3, 1), filter 240ms ease;
        }
        .starter-companion__burst {
          opacity: 0;
          transform-box: fill-box;
          transform-origin: center;
          transform: scale(0.72) rotate(-8deg);
          transition: opacity 180ms ease, transform 240ms cubic-bezier(0.16, 1, 0.3, 1);
        }
        .starter-companion:hover .starter-companion__puff,
        .starter-companion:focus-visible .starter-companion__puff {
          transform: scale(1.07) rotate(2deg);
          filter: drop-shadow(0 4px 9px rgba(120, 169, 255, 0.16));
        }
        .starter-companion:hover .starter-companion__burst,
        .starter-companion:focus-visible .starter-companion__burst {
          opacity: 0.55;
          transform: scale(0.9) rotate(0deg);
        }
        .starter-companion.is-popping .starter-companion__puff {
          animation: starterCompanionPop 520ms cubic-bezier(0.16, 1, 0.3, 1);
        }
        .starter-companion.is-popping .starter-companion__burst {
          animation: starterCompanionBurst 520ms cubic-bezier(0.16, 1, 0.3, 1);
        }
        @keyframes starterCompanionPop {
          0% { transform: scale(1) rotate(0deg); }
          22% { transform: scale(0.82) rotate(-5deg); }
          58% { transform: scale(1.18) rotate(5deg); }
          78% { transform: scale(0.97) rotate(-1deg); }
          100% { transform: scale(1) rotate(0deg); }
        }
        @keyframes starterCompanionBurst {
          0%, 18% { opacity: 0; transform: scale(0.6) rotate(-12deg); }
          55% { opacity: 0.9; transform: scale(1.16) rotate(8deg); }
          100% { opacity: 0; transform: scale(1.35) rotate(14deg); }
        }
        @keyframes starterCardIn {
          from { opacity: 0; transform: translateY(6px); }
          to { opacity: 1; transform: none; }
        }
      `}</style>
    </div>
  );
};

const ChatTaskProgress = () => (
  <div
    className="mb-2 flex h-6 items-center gap-1.5 px-0.5 text-caption-02 text-text-secondary"
    role="status"
    aria-live="polite"
    aria-label="Pondering"
  >
    <Icon name="sparkle" size={14} className="flex-shrink-0 text-support-warning animate-pulse" />
    <span>Pondering<span className="rice-ellipsis" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span></span>
  </div>
);

function ChatScreen({ active = true }: { active?: boolean }) {
  const { jumpToSkill, setSkills, setDetails, panelScrollRef, refreshSuggestions, mirrorParkedProposal, dropParkedMirror, resolvedApprovals } = useLearning();
  const navigate = useNavigate();
  const toast = useToast();
  const { t } = useTranslation('chat');

  const [thread, setThreadState] = useState<ThreadMessage[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSessionId, setActiveSessionId] = useState('');
  // Live SSE events are session-scoped. Keeping a small per-session render cache
  // prevents a turn that started in room A from mutating room B after selection.
  // It also preserves the incremental thread while the ChatScreen is hidden.
  const activeSessionIdRef = useRef('');
  const threadRef = useRef<ThreadMessage[]>([]);
  const threadCacheRef = useRef<Map<string, ThreadMessage[]>>(new Map([['', []]]));

  const setThread = (update: ThreadStateUpdate) => {
    const next = typeof update === 'function' ? update(threadRef.current) : update;
    threadRef.current = next;
    threadCacheRef.current.set(activeSessionIdRef.current, next);
    setThreadState(next);
  };

  const updateThreadFor = (sessionId: string, update: ThreadStateUpdate) => {
    const previous = threadCacheRef.current.get(sessionId) ?? [];
    const next = typeof update === 'function' ? update(previous) : update;
    threadCacheRef.current.set(sessionId, next);
    if (activeSessionIdRef.current === sessionId) {
      threadRef.current = next;
      setThreadState(next);
    }
  };

  const showSessionThread = (sessionId: string, next: ThreadMessage[]) => {
    activeSessionIdRef.current = sessionId;
    threadRef.current = next;
    threadCacheRef.current.set(sessionId, next);
    setActiveSessionId(sessionId);
    setThreadState(next);
  };
  // Reactive mirror of api.isEnabled. The connect probe (App bootstrap) is async,
  // so we subscribe rather than read once — when it flips on, we load the real
  // sessions persisted in state.db into the sidebar.
  const [connected, setConnected] = useState(api.isEnabled);
  // Runtime owns this path (and can relocate it with RICE_HOME), so never
  // reconstruct it from the browser's home directory.
  const [workspacePath, setWorkspacePath] = useState<string | null>(null);
  const [workspaceSaving, setWorkspaceSaving] = useState(false);
  // which session's thread is currently mounted (gates the scroll-to-message effect)
  const [loadedSession, setLoadedSession] = useState('');
  const [highlightMsg, setHighlightMsg] = useState<number | null>(null);
  const [pendingScroll, setPendingScroll] = useState<{ session: string; index: number } | null>(null);
  const [draft, setDraft] = useState('');
  // A leading slash-command is lifted OUT of the textarea into its own chip.
  // `command` holds the command token (e.g. "quarterly_sales_summary") or null;
  // `draft` then holds only the trailing arguments/instructions.
  const [command, setCommand] = useState<string | null>(null);
  const [isEmpty, setIsEmpty] = useState(true);
  const [sessionSidebarOpen, setSessionSidebarOpen] = useState(true);
  const [learningPanelOpen, setLearningPanelOpen] = useState(true);
  const [settingsPanelOpen, setSettingsPanelOpen] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [approvalMenuOpen, setApprovalMenuOpen] = useState(false);
  const [approvalMode, setApprovalMode] = useState<ApprovalMode>(() => {
    try {
      const saved = window.localStorage.getItem('rice.chat.approvalMode');
      if (saved === 'ask' || saved === 'approve_for_me' || saved === 'full_access') return saved;
    } catch {
      // localStorage can be unavailable in hardened browser contexts.
    }
    return 'ask';
  });
  const [webSearchEnabled, setWebSearchEnabled] = useState(() => {
    try {
      return window.localStorage.getItem('rice.chat.webSearchEnabled') !== 'false';
    } catch {
      return true;
    }
  });
  // "이 대화를 스킬로 저장" 팝오버 — 선택 instruction 입력 후 /learn 스트림 시작
  const [learnOpen, setLearnOpen] = useState(false);
  const [learnInstruction, setLearnInstruction] = useState('');
  // a live (runtime) turn is streaming — gates the composer so we can't double-send,
  // and drives the "생성 중" affordance on the send button.
  const [busy, setBusy] = useState(false);
  const [workProgress, setWorkProgress] = useState<LiveTurnProgress | null>(null);
  // session pending delete-confirmation (ConfirmDialog target), null = closed
  const [confirmDelete, setConfirmDelete] = useState<Session | null>(null);
  const [modelMode, setModelMode] = useState<'saas' | 'local'>('saas');
  const [currentModel, setCurrentModel] = useState('claude-sonnet-4-6');
  // 등록 모델·키는 런타임 state.db(SQLite)에 저장된다 — 연결 시 GET /settings로 복원.
  const [modelSettings, setModelSettings] = useState(() => {
    try {
      const saved = window.localStorage.getItem('rice.chat.showThinking');
      return { ...defaultModelSettings, showThinking: saved == null ? true : saved === 'true' };
    } catch {
      return defaultModelSettings;
    }
  });

  // GET/PUT /settings 응답을 현재 채팅 UI에 적용한다. 최초 복원뿐 아니라
  // App 레벨 온보딩이 저장한 결과도 같은 경로로 즉시 동기화한다.
  const applyRuntimeModelSettings = useCallback((raw: unknown) => {
    const data = raw as RuntimeModelSettings | undefined;
    const prov = data?.provider;
    if (!prov) return;

    setModelMode(prov.kind === 'ollama' ? 'local' : 'saas');
    if (prov.model) setCurrentModel(prov.model);
    setModelSettings((prev) => ({
      ...prev,
      registeredModels: data?.registered_models ?? [],
      ...(prov.base_url ? { endpoint: prov.base_url } : {}),
      ...(prov.temperature !== undefined ? { temperature: prov.temperature } : {}),
      ...(prov.show_thinking !== undefined ? { showThinking: prov.show_thinking } : {}),
    }));

    if (prov.show_thinking !== undefined) {
      try {
        window.localStorage.setItem('rice.chat.showThinking', String(prov.show_thinking));
      } catch {
        // 메모리 상태는 이번 앱 세션 동안 계속 적용된다.
      }
    }
  }, []);

  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    try {
      window.localStorage.setItem('rice.chat.approvalMode', approvalMode);
    } catch {
      // The selection still applies for this mounted app session.
    }
  }, [approvalMode]);

  useEffect(() => {
    try {
      window.localStorage.setItem('rice.chat.webSearchEnabled', String(webSearchEnabled));
    } catch {
      // Keep the in-memory selection when persistence is unavailable.
    }
  }, [webSearchEnabled]);

  // composer 자동 높이 — 내용만큼 자라고 max-h(200px)에서 내부 스크롤로 전환
  useEffect(() => {
    const el = composerRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [draft, command]);
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Real backend session id for the live (runtime-connected) path. Created lazily
  // on first send; reused for the rest of this mounted ChatScreen. Independent of
  // the scripted demo sessions in the sidebar.
  const liveSessionId = useRef<string | null>(null);
  // 새 세션의 첫 턴 완료 후 제목 생성(POST /title)이 필요할 때만 true — 기존 세션 연속 전송 시 skip.
  const needsTitleRefresh = useRef<Set<string>>(new Set());
  // handle to the in-flight SSE turn so the user can stop it (see stopTurn)
  const streamHandleRef = useRef<{ abort: () => void } | null>(null);
  // Unlike liveSessionId (the room currently selected), this is immutable for
  // the lifetime of the active request and is therefore the correct abort target.
  const runningSessionIdRef = useRef<string | null>(null);
  // Monotonic request identity prevents a late onClose/createSession resolution
  // from clearing or starting a newer turn in the same room.
  const turnRequestRef = useRef(0);

  const sessionPanel = useResizable({ initial: 264, min: 200, max: 420, storageKey: 'rice.panel.sessions' });
  const learningPanel = useResizable({ initial: 320, min: 240, max: 480, storageKey: 'rice.panel.learning', invert: true });

  // 모델 카탈로그는 없다(SaaS=직접 등록, Local=ollama에서 라이브 조회).
  // 헤더에 보여줄 이름은 현재 선택된 모델 식별자 그대로 사용한다.
  const selectedModelName = currentModel || (modelMode === 'local' ? t('model.noLocalSelection') : t('model.noSelection'));
  const activeSession = sessions.find((s) => s.id === activeSessionId);

  // 모델 선택 저장: 로컬 상태 갱신 + 런타임에 영속화(configured=true).
  // 설정 패널과 온보딩 화면 양쪽에서 동일하게 사용한다.
  const persistModelSelection = ({ modelId, settings, mode }: { modelId: string; settings: typeof modelSettings; mode: 'saas' | 'local' }) => {
    setCurrentModel(modelId);
    setModelSettings(settings);
    setModelMode(mode);
    try {
      window.localStorage.setItem('rice.chat.showThinking', String(settings.showThinking));
    } catch {
      // 메모리 상태는 현재 앱 세션 동안 계속 적용된다.
    }
    if (connected) {
      // SaaS는 등록 모델의 provider로 런타임 kind를 정한다. base_url/project_id 같은
      // 연결정보는 provider_models(state.db)에서 모델별로 읽으므로 config엔 안 넣는다.
      const activeEntry = settings.registeredModels.find((r) => r.modelId === modelId);
      const kind = mode === 'local' ? 'ollama' : providerKind(activeEntry?.provider);
      // 활성 모델 선택 + 등록 모델 목록(키 포함)을 런타임 state.db에 영속화.
      // configured:true → 다음 실행부터 온보딩을 건너뛰고 저장된 모델을 쓴다 (C-1).
      void api.updateSettings({
        provider: {
          kind,
          model: modelId,
          temperature: settings.temperature,
          base_url: mode === 'local' ? settings.endpoint ?? null : null,
          configured: true,
          // 로컬 LLM의 thinking 스트림 노출 토글 — 백엔드가 이 값으로 thinking
          // SSE·메타데이터를 게이팅한다(SaaS는 kind로 무시). 프론트 표시 필터와
          // 짝을 이뤄 백엔드가 노출의 단일 권위가 된다.
          show_thinking: settings.showThinking,
        },
        registered_models: settings.registeredModels,
      });
    }
  };

  // Keep `connected` in sync with the (async) runtime connect probe.
  useEffect(() => {
    setConnected(api.isEnabled);
    return api.onEnabledChange(setConnected);
  }, []);

  useEffect(
    () => api.onSettingsChange(applyRuntimeModelSettings),
    [applyRuntimeModelSettings],
  );

  // Make the effective output location discoverable directly below the composer.
  // The native picker selects a new single sandbox root; the runtime applies it
  // atomically on restart so one in-flight turn never spans two folders.
  useEffect(() => {
    if (!connected) {
      setWorkspacePath(null);
      return;
    }
    void api.getWorkspaceInfo().then((res) => {
      if (res.success && res.data?.path) {
        setWorkspacePath(res.data.path);
      }
    });
  }, [connected]);

  const chooseWorkspaceFolder = () => {
    if (workspaceSaving) return;
    const picker = (
      window as { electronAPI?: { dialog?: { selectDirectory: (defaultPath?: string) => Promise<string | null> } } }
    ).electronAPI?.dialog?.selectDirectory;
    if (!picker) {
      toast.notify({ message: t('folderOnlyDesktop'), variant: 'info' });
      return;
    }
    setWorkspaceSaving(true);
    void picker(workspacePath || undefined)
      .then(async (path) => {
        if (!path) return;
        const res = await api.updateWorkspaceLocation(path);
        if (!res.success || !res.data?.path) {
          toast.notify({ message: t('workspaceSaveFailed', { error: res.error ?? 'workspace' }), variant: 'error' });
          return;
        }
        setWorkspacePath(res.data.path);
        toast.notify({ message: t('workspaceSaveSuccess'), variant: 'success', duration: 4500 });
      })
      .catch(() => toast.notify({ message: t('workspaceFolderOpenFailed'), variant: 'error' }))
      .finally(() => setWorkspaceSaving(false));
  };

  const openWorkspaceFolder = () => {
    if (!workspacePath) return;
    const opener = (
      window as { electronAPI?: { dialog?: { openDirectory: (dirPath: string) => Promise<boolean> } } }
    ).electronAPI?.dialog?.openDirectory;
    if (!opener) {
      toast.notify({ message: t('folderOnlyDesktop'), variant: 'info' });
      return;
    }
    void opener(workspacePath).catch(() =>
      toast.notify({ message: t('workspaceFolderOpenFailed'), variant: 'error' }),
    );
  };

  // When connected, restore persisted settings (model/provider/endpoint) from
  // config.yaml via GET /settings, then load the session list.
  useEffect(() => {
    if (!connected) return;
    let cancelled = false;

    // 1) Restore model settings saved by PUT /settings, then health-check when local
    void api.getSettings().then(async (res) => {
      if (cancelled || !res.success) return;
      const data = res.data as RuntimeModelSettings;
      const prov = data?.provider;
      if (prov) {
        applyRuntimeModelSettings(data);

        // Local 모드일 때만: 설정된 모델이 실제 Ollama에 남아 있는지 확인
        if (prov.kind === 'ollama' && prov.model) {
          const hc = await api.checkOllamaModelHealth();
          if (cancelled) return;
          if (!hc.success) {
            toast.notify({ message: t('error.ollamaUnreachable'), variant: 'error' });
          } else if (hc.data && !hc.data.exists) {
            toast.notify({
              message: t('error.ollamaModelMissing', { model: prov.model }),
              variant: 'warning',
              duration: 8000,
            });
          }
        }
      }
    });

    // 2) Load sessions (independent of settings restore)
    void api.listSessions().then((res) => {
      if (cancelled || !res.success) return;
      const list = ((res.data as RawSession[]) ?? []).map(toSession);
      setSessions(list);
      // On initial load always start at a blank New Chat so the user isn't
      // dropped into someone else's (or their own previous) conversation.
      // The only exception is a runtime reconnect that happened while a turn
      // was actively streaming — in that case runningSessionIdRef still holds
      // the session id and we restore it together with any parked approvals
      // (§5.1).
      const recovering = runningSessionIdRef.current;
      if (recovering && list.some((s) => s.id === recovering)) {
        void openLiveSession(recovering).then(() => {
          if (!cancelled) void recoverPendingApprovals(recovering);
        });
      } else {
        newChat();
      }
    });

    return () => {
      cancelled = true;
    };
    // openLiveSession/newChat are stable enough for the MVP; we intentionally run
    // this only on connect transitions.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected]);

  // 스마트 오토스크롤: 바닥 근처에 있을 때만 새 메시지를 따라가고,
  // 위로 스크롤해 읽는 중이면 위치를 지키고 "새 메시지" 플로팅 버튼을 띄운다.
  const [atBottom, setAtBottom] = useState(true);
  const [hasNewBelow, setHasNewBelow] = useState(false);
  const NEAR_BOTTOM_PX = 120;

  const handleThreadScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
    setAtBottom(nearBottom);
    if (nearBottom) setHasNewBelow(false);
  };

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (atBottom) {
      el.scrollTop = el.scrollHeight;
    } else {
      setHasNewBelow(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [thread]);

  const jumpToBottom = () => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    setAtBottom(true);
    setHasNewBelow(false);
  };

  // after the target session's thread is mounted, scroll to + pulse the
  // message a proposal's evidence pointed at (gated on loadedSession so we
  // never query the previous session's DOM).
  useEffect(() => {
    if (!pendingScroll || loadedSession !== pendingScroll.session) return;
    const el = scrollRef.current?.querySelector<HTMLElement>(`[data-msg-index="${pendingScroll.index}"]`);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      setHighlightMsg(pendingScroll.index);
      if (highlightTimer.current) clearTimeout(highlightTimer.current);
      highlightTimer.current = setTimeout(() => setHighlightMsg(null), 2400);
    }
    setPendingScroll(null);
  }, [pendingScroll, loadedSession, thread]);

  // 교정("가르치기") = 정정 내용을 실제 사용자 턴으로 보낸다. 에이전트가 그에 맞춰
  // 답하고, 런타임 학습 루프(observe)가 선호/메모리를 추출한다 — mock 아님, 실 경로.
  const handleCorrect = (text: string) => {
    if (busy || !api.isEnabled) return;
    runLiveTurn(text);
  };

  // approve a skill-improvement (patch) from chat → actually apply it to the
  // learning store: version++, keep the old version in history, reflect the
  // body change (steps/permissions). Mirrors LEARNING_LOOP 5.2.4 so that "쓸수록
  // 개선됨"이 My Skills에서 눈으로 확인된다 (정성적 증거). The real runtime path is
  // POST /skills/{id}/patch with approval_id (CONTRACTS 1.2) — see api.respondSkillPatch.
  const handleApprovePatch = (m: Extract<ThreadMessage, { kind: 'patch' }>, editedBody?: string) => {
    setSkills((arr) =>
      arr.map((s) =>
        s.id === m.skillId ? { ...s, ver: m.toVer, patches: (s.patches ?? 0) + 1, trend: 'trend_improved', enabled: true } : s,
      ),
    );
    const addedRules = (m.newRules ?? []).filter((r) => r.added);
    setDetails((d) => {
      const cur = d[m.skillId];
      if (!cur) return d;
      const note = `${m.changelog}${editedBody ? ` · ${t('patch.editApplied')}` : ''}`;
      return {
        ...d,
        [m.skillId]: {
          ...cur,
          lastUsed: 'last_used_just_now',
          rules: [
            ...cur.rules,
            ...addedRules.map((r, i) => ({ id: `pr${Date.now()}-${i}`, text: r.text, source: t('learning.sourcePatch'), on: true })),
          ],
          versions: [
            { ver: m.toVer, when: 'last_used_just_now', note, current: true, rules: m.newRules ?? [] },
            ...cur.versions.map((v) => ({ ...v, current: false })),
          ],
        },
      };
    });
    // contract response (parking match by approval_id) — no-op until runtime is up.
    // 만료된 제안(파킹 타임아웃)은 서버가 이미 제안함으로 옮겼다(Self-Improvement v3)
    // — 409가 오면 카드 대신 제안함에서 결정하라고 안내한다.
    void api.respondSkillPatch(m.skillId, { approval_id: m.approvalId, decision: 'approve', edited_body: editedBody }).then((res) => {
      if (!res.success && connected) {
        toast.notify({ message: t('error.patchExpired'), duration: 5000 });
        void refreshSuggestions();
      }
    });
    // 채팅 카드에서 결정 → 제안함에 미러돼 있던 안전망 사본을 걷어낸다(이중 표시 방지).
    dropParkedMirror(m.approvalId);
    jumpToSkill(m.skillId);
  };

  // 명시적 거부 → 서버 파킹 즉시 해소 + ledger에 'rejected' 기록(반복 거부 쿨다운의
  // 원천, Self-Improvement v3). 이전엔 거부가 로컬 카드 상태만 바꿔 서버는 타임아웃까지
  // 기다렸다 — v3에선 타임아웃이 제안함으로 이동하므로, 명시적 거부를 서버에 알리지
  // 않으면 사용자가 거부한 제안이 제안함에 다시 나타나는 모순이 생긴다.
  const handleRejectPatch = (m: Extract<ThreadMessage, { kind: 'patch' }>) => {
    void api.respondSkillPatch(m.skillId, { approval_id: m.approvalId, decision: 'reject' });
    dropParkedMirror(m.approvalId);
  };

  // tool-execution approval (HITL) → POST the decision back to the runtime,
  // matched server-side by approval_id (CONTRACTS 1.2). Card state is local; this
  // is the real wire (no-op offline).
  const handleToolApproval = (m: Extract<ThreadMessage, { kind: 'approval' }>, decision: 'approve' | 'reject') => {
    void api.respondToolApproval(activeSessionId, { approval_id: m.approvalId, decision });
  };

  // 재생성 = 마지막 사용자 메시지에 대한 답변을 다시 생성. 서버가 이전 어시스턴트
  // 턴을 교체(delete_after)하므로, 화면에서도 직전 턴 블록을 걷어내고 새로 스트리밍한다.
  const regenerate = () => {
    if (busy || !api.isEnabled || !liveSessionId.current) return;
    const sessionId = liveSessionId.current;
    const requestId = ++turnRequestRef.current;
    sealAnswer(sessionId);
    setBusy(true);
    runningSessionIdRef.current = sessionId;
    setWorkProgress({ sessionId });
    updateThreadFor(sessionId, (p) => {
      let end = p.length; // strip trailing assistant turn (blocks after the last user msg)
      for (let i = p.length - 1; i >= 0; i--) {
        if (p[i].kind === 'user') break;
        end = i;
      }
      return [...p.slice(0, end), { kind: 'thinking', duration: '', text: '', streaming: true, startedAt: Date.now() }];
    });
    streamHandleRef.current = sseClient.regenerate(sessionId, approvalMode, webSearchEnabled, buildStreamHandlers(sessionId, requestId));
  };

  // ── live-turn thread reducers (multi-round: thinking ↔ tool ↔ approval ↔ answer) ──
  // Each is a setThread updater so it reads the latest thread; events arrive
  // sequentially over SSE and mutate the trailing blocks.

  // Append to the trailing streaming thinking block, or start a new one.
  const appendThinking = (sessionId: string, delta: string) =>
    updateThreadFor(sessionId, (p) => {
      const copy = [...p];
      const last = copy[copy.length - 1];
      if (last?.kind === 'thinking' && last.streaming) {
        copy[copy.length - 1] = { ...last, text: (last.text ?? '') + delta };
      } else {
        copy.push({ kind: 'thinking', duration: '', text: delta, streaming: true, startedAt: Date.now() });
      }
      return copy;
    });

  // Close the trailing thinking block (stamp duration); drop it if it stayed empty.
  const finalizeThinking = (sessionId: string) =>
    updateThreadFor(sessionId, (p) => {
      const copy = [...p];
      const last = copy[copy.length - 1];
      if (last?.kind === 'thinking' && last.streaming) {
        if (!last.text) copy.pop();
        else {
          const secs = Math.max(1, Math.round((Date.now() - (last.startedAt ?? Date.now())) / 1000));
          copy[copy.length - 1] = { ...last, streaming: false, duration: t('thinking.durationSeconds', { count: secs }) };
        }
      }
      return copy;
    });

  // 모델 응답을 다시 기다리기 시작하는 지점(계획 확정 후 등)에서 가벼운 "작업 중"
  // 표시(빈 streaming thinking 블록)를 심는다 — 추론을 스트리밍하지 않는 상용
  // 모델도 경과 초 카운터가 계속 흐르게 하기 위함. 실제 콘텐츠(답변/도구/추론)가
  // 오면 finalizeThinking이 즉시 걷어낸다. 이미 streaming 중이면 no-op.
  const seedWaiting = (sessionId: string) =>
    updateThreadFor(sessionId, (p) => {
      const last = p[p.length - 1];
      if (last?.kind === 'thinking' && last.streaming) return p;
      return [...p, { kind: 'thinking', duration: '', text: '', streaming: true, startedAt: Date.now() }];
    });

  // plan_updated: 같은 planId 카드를 스냅샷으로 통째 교체 (없으면 이 지점에 생성).
  const upsertPlan = (sessionId: string, d: PlanUpdatedEventData) =>
    updateThreadFor(sessionId, (p) => upsertPlanBlock(p, d));

  // ── smooth answer streaming ("typewriter") ────────────────────────────────
  // Deltas can arrive as either tiny character trickles or large bursts. Rendering
  // them verbatim makes the text shimmer; draining by a fixed, bounded chunk gives
  // a calmer reading rhythm while a short buffer groups one-character trickles.
  const answerRef = useRef<{ sessionId: string; target: string; shown: number; active: boolean; complete: boolean; lastFeedAt: number }>({
    sessionId: '',
    target: '',
    shown: 0,
    active: false,
    complete: false,
    lastFeedAt: 0,
  });
  const answerTimerRef = useRef<number | null>(null);

  const stopAnswerPump = () => {
    if (answerTimerRef.current != null) {
      window.clearInterval(answerTimerRef.current);
      answerTimerRef.current = null;
    }
  };

  const pumpTick = () => {
    const a = answerRef.current;
    if (a.shown >= a.target.length) {
      if (a.active && a.complete) {
        updateThreadFor(a.sessionId, (p) => {
          const copy = [...p];
          const idx = copy.map((block) => block.kind).lastIndexOf('agent');
          if (idx >= 0) copy[idx] = { ...(copy[idx] as Extract<ThreadMessage, { kind: 'agent' }>), streaming: false };
          return copy;
        });
        a.active = false;
      }
      stopAnswerPump();
      return;
    }
    const backlog = a.target.length - a.shown;
    if (backlog < 5 && Date.now() - a.lastFeedAt < 80) return;
    const step = backlog > 260 ? 24 : backlog > 140 ? 16 : backlog > 64 ? 10 : backlog > 24 ? 6 : 3;
    a.shown = Math.min(a.target.length, a.shown + step);
    const text = a.target.slice(0, a.shown);
    updateThreadFor(a.sessionId, (p) => {
      const copy = [...p];
      const last = copy[copy.length - 1];
      if (last?.kind === 'agent') {
        copy[copy.length - 1] = { ...last, text };
        return copy;
      }
      // Another card was appended over our bubble (tool/effect) — render this
      // segment in full at its original spot and stop; a new segment starts fresh.
      const idx = copy.map((b) => b.kind).lastIndexOf('agent');
      if (idx >= 0) {
        copy[idx] = { ...(copy[idx] as { kind: 'agent'; text: string }), text: a.target, streaming: false };
        stopAnswerPump();
        a.active = false;
        return copy;
      }
      return p;
    });
  };

  const ensureAnswerPump = () => {
    if (answerTimerRef.current == null) answerTimerRef.current = window.setInterval(pumpTick, 33);
  };

  // Feed a streamed delta (or the authoritative final `content`) into the pump.
  const feedAnswer = (sessionId: string, d: { delta?: string; content?: string }) => {
    const a = answerRef.current;
    // authoritative `content`로 확정된 버블 뒤에 도착한 델타는 새 세그먼트다
    // (reconcile 후 작업이 재개된 턴) — 확정된 답에 이어붙이지 않는다.
    if (a.active && a.complete && typeof d.delta === 'string') sealAnswer(sessionId);
    if (!a.active) {
      a.active = true;
      a.sessionId = sessionId;
      a.target = '';
      a.shown = 0;
      a.complete = false;
      a.lastFeedAt = Date.now();
      updateThreadFor(sessionId, (p) => [...p, { kind: 'agent', text: '', streaming: true, sentAt: Date.now() }]);
    }
    if (typeof d.content === 'string') {
      // 백엔드가 이 본문을 턴의 확정 답으로 선언(일반 final, 또는 계획 reconcile
      // 시점의 조기 확정) — 타자기 꼬리가 다 그려지면 버블이 스스로 봉인되어,
      // 뒤따르는 도구 라운드가 이 답을 캡션으로 걷어가지 못한다.
      a.target = d.content;
      a.complete = true;
    } else a.target += d.delta ?? '';
    a.lastFeedAt = Date.now();
    ensureAnswerPump();
  };

  // Close the current answer segment: render it in full now, stop the pump, and
  // reset so the next text starts a fresh bubble. Called at every interruption
  // (a tool round, a new user turn, stream error/close) — but NOT on `done`, so a
  // just-finished answer types its tail out smoothly instead of snapping to full.
  const sealAnswer = (sessionId?: string) => {
    const a = answerRef.current;
    if (sessionId != null && a.active && a.sessionId !== sessionId) return;
    if (a.active && a.shown < a.target.length) {
      const text = a.target;
      updateThreadFor(a.sessionId, (p) => {
        const copy = [...p];
        const idx = copy.map((b) => b.kind).lastIndexOf('agent');
        if (idx >= 0) copy[idx] = { ...(copy[idx] as { kind: 'agent'; text: string }), text, streaming: false };
        return copy;
      });
    }
    a.active = false;
    a.target = '';
    a.shown = 0;
    a.complete = false;
    a.lastFeedAt = 0;
    stopAnswerPump();
  };

  // Network `done` can arrive while the visual typewriter still has a tail.
  // Mark Markdown stable only after that tail has drained, so incomplete table
  // rows remain buffered instead of flashing as pipe-delimited prose.
  const finishAnswerStream = (sessionId: string) => {
    const a = answerRef.current;
    if (!a.active || a.sessionId !== sessionId) return;
    a.complete = true;
    ensureAnswerPump();
  };

  // Lift the in-progress answer buffer as the rationale for an imminent tool call
  // (#1 rationale primitive). The interstitial text the model streamed before acting
  // IS its one-line "why" — return the full buffered text (authoritative, even if the
  // typewriter hasn't caught up) and tear the pump down WITHOUT sealing a standalone
  // bubble; the caller re-homes it as the tool card's caption. Null if nothing was
  // streamed (model didn't narrate) → card renders with no caption, as before.
  const takeStreamingAnswer = (sessionId: string): string | null => {
    const a = answerRef.current;
    if (!a.active || a.sessionId !== sessionId) return null;
    // 확정(complete)된 본문은 rationale이 아니라 이 턴의 답이다 — 캡션으로
    // 걷어가지 말고 제자리에 봉인한다(reconcile 직후 도구 라운드가 최종 답을
    // 삼키는 것을 방지).
    if (a.complete) {
      sealAnswer(sessionId);
      return null;
    }
    const text = a.target.trim();
    a.active = false;
    a.target = '';
    a.shown = 0;
    a.complete = false;
    a.lastFeedAt = 0;
    stopAnswerPump();
    return text || null;
  };

  // Hard reset the live-render state without flushing — for switching sessions or
  // starting a new chat, where a running pump must not write into the thread we're
  // about to replace.
  const resetLiveRender = () => {
    stopAnswerPump();
    answerRef.current = { sessionId: '', target: '', shown: 0, active: false, complete: false, lastFeedAt: 0 };
  };

  // Stop the pump if the component unmounts mid-stream.
  useEffect(() => () => stopAnswerPump(), []);

  const pushAgentText = (sessionId: string, text: string) =>
    updateThreadFor(sessionId, (p) => [...p, { kind: 'agent', text }]);

  // memory_written / skill_patch_proposed / skill_patched 렌더링 — 메인 턴 스트림과
  // /learning-review·/learn 스트림(Phase A) 양쪽에서 재사용한다. 어느 스트림에서
  // 왔든 렌더링 로직은 동일해야 한다(같은 SSE 이벤트 어휘를 공유).
  const handleMemoryWritten = (d: {
    memory_type: 'fact' | 'preference';
    content: string;
    status?: 'active' | 'quarantined';
    derived_from_external?: boolean;
  }) => {
    // 외부 콘텐츠 유래 메모리는 격리(quarantined) 상태 — 즉시 주입되지
    // 않고 승인 대기다. 토스트로 "기억됨"이라 오인시키지 않고 학습 패널로 안내한다.
    if (d.status === 'quarantined') {
      toast.notify({
        message: t('error.memoryQuarantined', { content: d.content.slice(0, 48) }),
        duration: 5000,
      });
      return;
    }
    const label = d.memory_type === 'fact' ? t('error.memoryFact') : t('error.memoryPreference');
    toast.notify({ message: t('error.memoryStored', { label, content: d.content.slice(0, 60) }), duration: 4000 });
  };

  const handleSkillPatchProposed = (d: SkillPatchProposedData, sessionId = activeSessionIdRef.current) => {
    // HITL gate — diff 카드 렌더링. 스트림은 계속 흐르지만 사용자 결정은
    // POST /skills/{id}/patch 로 별도 전송 (pendingToThread 로직 재사용).
    const card = pendingToThread({ approval_id: d.approval_id, kind: 'skill_patch', payload: d });
    if (card) updateThreadFor(sessionId, (p) => [...p, card]);
    // 안전망(Self-Improvement v3): 발생 세션과 무관하게 제안함에도 즉시 미러한다 —
    // 사용자가 세션을 옮기면 채팅 카드를 놓칠 수 있어서다. 채팅 카드/제안함 어느
    // 쪽에서 결정해도 서로 정리된다(dropParkedMirror ↔ resolvedApprovals).
    mirrorParkedProposal(d, sessionId);
  };

  const handleSkillPatched = (d: { skill_id: string; version: number }) => {
    // 이전에 승인된 patch(또는 신규 생성 — from_version=0, Phase A.1)가 서버에서
    // 적용 완료됨. 로컬 skills 상태 동기화 — id가 없으면(=방금 생성된 스킬,
    // skill_created 이벤트는 더 이상 없음) map이 조용히 no-op하지 않도록 삽입한다.
    let wasNew = false;
    setSkills((arr) => {
      if (arr.some((s) => s.id === d.skill_id)) {
        return arr.map((s) =>
          s.id === d.skill_id ? { ...s, ver: `v${d.version}`, patches: (s.patches ?? 0) + 1, trend: 'trend_improved_short' } : s,
        );
      }
      wasNew = true;
      return [
        ...arr,
        {
          id: d.skill_id,
          name: d.skill_id,
          ver: `v${d.version}`,
          uses: 0,
          trend: 'trend_just_created',
          enabled: true,
          patches: 0,
          command: d.skill_id,
          state: 'active' as const,
        },
      ];
    });
    if (wasNew) {
      setDetails((prev) =>
        prev[d.skill_id]
          ? prev
          : {
              ...prev,
              [d.skill_id]: {
                summary: d.skill_id,
                createdFrom: 'created_from_chat_auto',
                lastUsed: 'last_used_just_now',
                shared: false,
                rules: [],
                versions: [{ ver: `v${d.version}`, when: 'last_used_just_now', note: t('learning.noteAutoCreated'), current: true }],
                // 'idle' = 런타임 백드 표식. 없으면 SkillDetailView의 runtimeBacked가
                // false로 굳어 loadRuntimeSkillDetail(설명·분석·버전 fetch)이 스킵되고,
                // 재부팅(loadSkills→runtimeSkillToDetail이 'idle' 부여) 전까지 stub만 보인다.
                historyStatus: 'idle',
                usage: [{ kind: 'chat', name: activeSession?.title ?? t('conversation'), icon: 'chat' as const }],
                permissions: [],
              },
            },
      );
      jumpToSkill(d.skill_id);
    }
  };

  // "이 대화를 스킬로 저장" — 명시적 사용자 트리거 (Phase C, learn_prompt.py 이식).
  // /learning-review와 달리 게이트 없이 항상 시도한다. 같은 이벤트 어휘를 공유하므로
  // 렌더링은 위의 shared handler 3종을 그대로 재사용 — 이 함수는 스트림을 열고
  // 진행 상황만 toast로 알린다. instruction(선택)은 학습 팝오버에서 사용자가 직접
  // 입력한 초점("배포 단계만 정리해줘")이며 빈 문자열이면 대화 전체를 자동 증류한다.
  const learnFromConversation = (instruction = '') => {
    const sid = liveSessionId.current;
    if (!connected || !sid) return;
    setLearnOpen(false);
    toast.notify({ message: t('learn.processing'), duration: 3000 });
    let proposed = false;
    sseClient.learnFromConversation(sid, instruction, {
      onEvent: (e: SSEEvent) => {
        if (e.type === 'memory_written') {
          handleMemoryWritten(e.data as MemoryWrittenEventData);
        } else if (e.type === 'skill_patch_proposed') {
          proposed = true;
          // 캡처한 sid(학습 대상 세션)를 넘긴다 — 기본 폴백(activeSessionIdRef)이면
          // learn 스트림 중 A→B로 옮겼을 때 카드가 B에 그려지는 세션 혼선이 난다.
          handleSkillPatchProposed(e.data as SkillPatchProposedData, sid);
        } else if (e.type === 'skill_patched') {
          handleSkillPatched(e.data as { skill_id: string; version: number });
        } else if (e.type === 'done' && !proposed) {
          toast.notify({ message: t('learn.nothingFound'), duration: 3000 });
        } else if (e.type === 'error') {
          const d = e.data as { message?: string };
          toast.notify({ message: t('learn.failed', { message: d.message ?? apiErrorMessage(null) }), duration: 4000 });
        }
      },
      onError: (msg) => toast.notify({ message: t('learn.connectionError', { message: msg }), duration: 4000 }),
    });
  };

  // Live path: stream a real turn from the local runtime over SSE.
  // SSE event handlers for a live turn — shared by sendLive (new turn) and
  // regenerateLive (re-answer). The multi-round reducer (thinking ↔ tool ↔
  // approval ↔ answer) lives here so both entry points behave identically.
  const finishLiveTurn = (sessionId: string, requestId: number) => {
    if (turnRequestRef.current !== requestId || runningSessionIdRef.current !== sessionId) return;
    runningSessionIdRef.current = null;
    streamHandleRef.current = null;
    setBusy(false);
    setWorkProgress(null);
  };

  const buildStreamHandlers = (sessionId: string, requestId: number) => ({
    onEvent: (e: SSEEvent) => {
      if (turnRequestRef.current !== requestId) return;
      if (e.type === 'thinking') {
        const d = e.data as { text?: string };
        if (typeof d.text === 'string') appendThinking(sessionId, d.text);
      } else if (e.type === 'plan_updated') {
        // 계획 생성/전이/수정 — 진행 중이던 답변 세그먼트를 닫고, 플래너 생각 블록도
        // 닫은 뒤 계획 카드를 업서트한다. 계획→답변 생성 사이의 침묵 구간에도
        // 경과 카운터가 흐르도록 대기 표시를 심는다.
        sealAnswer(sessionId);
        finalizeThinking(sessionId);
        const plan = e.data as PlanUpdatedEventData;
        upsertPlan(sessionId, plan);
        seedWaiting(sessionId);
      } else if (e.type === 'tool_call') {
        // a tool round begins → close the reasoning block, push a running card.
        finalizeThinking(sessionId);
        const d = e.data as { tool_call_id: string; tool_name: string; arguments?: Record<string, unknown> };
        // update_plan·start_plan의 화면 표면은 계획 카드(plan_updated) — 도구 카드는
        // 만들지 않는다. 그 앞의 preamble은 평범한 답변 세그먼트로 흘려보낸다.
        if (d.tool_name === 'update_plan' || d.tool_name === 'start_plan') {
          sealAnswer(sessionId);
          return;
        }
        // 이 행동 직전에 스트리밍된 본문 = 행동의 rationale(#1). 떠도는 버블 대신
        // 카드의 캡션으로 재배치한다: 버퍼를 걷어오고, 펌프가 만든 진행형 agent
        // 버블을 제거한 뒤, rationale을 실은 실행 카드를 append한다.
        const rationale = takeStreamingAnswer(sessionId);
        updateThreadFor(sessionId, (p) => {
          const base = rationale != null && p[p.length - 1]?.kind === 'agent' ? p.slice(0, -1) : p;
          return [
            ...base,
            {
              kind: 'toolcall',
              callId: d.tool_call_id,
              tool: d.tool_name,
              args: d.arguments ?? {},
              status: 'running',
              rationale: rationale ?? undefined,
            },
          ];
        });
      } else if (e.type === 'tool_approval_required') {
        // HITL gate — render the approval card; the backend stream stays parked
        // until the user posts a decision (handleToolApproval), then resumes.
        sealAnswer(sessionId);
        const d = e.data as { approval_id: string; tool_name: string; arguments?: Record<string, unknown>; reason?: string };
        const desc = describeTool(d.tool_name, d.arguments ?? {});
        updateThreadFor(sessionId, (p) => [
          ...p,
          {
            kind: 'approval',
            approvalId: d.approval_id,
            tool: d.tool_name,
            label: desc.label,
            scope: { name: d.tool_name, mode: t('tools.executionMode') },
            reason: d.reason ?? desc.reason,
            approvedText: desc.approved,
            deniedText: desc.denied,
          },
        ]);
      } else if (e.type === 'tool_result') {
        const d = e.data as { tool_call_id: string; ok: boolean; result?: unknown; error?: string };
        updateThreadFor(sessionId, (p) => {
          const copy = [...p];
          for (let i = copy.length - 1; i >= 0; i--) {
            const b = copy[i];
            if (b.kind === 'toolcall' && b.callId === d.tool_call_id) {
              copy[i] = {
                ...b,
                status: d.ok ? 'done' : 'error',
                result: d.ok ? (typeof d.result === 'string' ? d.result : JSON.stringify(d.result, null, 2)) : undefined,
                error: d.ok ? undefined : (d.error ?? 'error'),
              };
              break;
            }
          }
          return copy;
        });
        // 도구 결과를 받은 뒤 모델이 다음 판단을 하는 침묵 구간에도 카운터가 흐르게
        // 대기 표시를 심는다(이미 streaming 중이면 no-op). 다음 콘텐츠가 걷어낸다.
        seedWaiting(sessionId);
      } else if (e.type === 'message') {
        finalizeThinking(sessionId);
        feedAnswer(sessionId, e.data as { delta?: string; content?: string });
      } else if (e.type === 'memory_written') {
        handleMemoryWritten(e.data as MemoryWrittenEventData);
      } else if (e.type === 'skill_patch_proposed') {
        handleSkillPatchProposed(e.data as SkillPatchProposedData, sessionId);
      } else if (e.type === 'skill_patched') {
        handleSkillPatched(e.data as { skill_id: string; version: number });
      } else if (e.type === 'done') {
        // NOTE: no sealAnswer() here — let the typewriter finish its tail smoothly
        // after the network is done (that trailing type-out is the desired feel).
        finishAnswerStream(sessionId);
        finalizeThinking(sessionId);
        finishLiveTurn(sessionId, requestId);
        // 턴 종료마다 제안함 재동기화 — 서버가 타임아웃/연결끊김으로 inbox에 옮긴
        // 제안(§9.2)이 라이브 미러를 대체하고, 미결정 미러는 병합이 그대로 보존한다.
        if (connected) void refreshSuggestions();
        const sid = sessionId;
        // First turn of a new session → ask the runtime to name it from the opening
        // exchange. Separate, non-blocking call (POST /title): it does NOT hold up
        // this stream, and we patch just that row's title in place — no list refetch.
        if (connected && needsTitleRefresh.current.has(sid)) {
          needsTitleRefresh.current.delete(sid);
          if (sid)
            void api.generateTitle(sid).then((res) => {
              const title = res.success ? (res.data as { title?: string } | undefined)?.title : undefined;
              if (title) setSessions((prev) => prev.map((s) => (s.id === sid ? { ...s, title } : s)));
            });
        }
        // Gated LLM skill/memory review (Phase A) — another separate, non-blocking
        // call right alongside the title one, same reasoning: an LLM call must
        // never sit inline in this stream. 서버가 턴 종료 시 영속화한
        // 리뷰 잡을 exactly-once로 claim하므로 여기선 그저 트리거만 한다 — 중복
        // 호출/재시도/탭 종료 모두 안전. Fast no-op when there's no pending job
        // or the gate declines — most turns just get back a bare `done`.
        if (connected && sid) {
          sseClient.reviewLearning(sid, {
            onEvent: (re: SSEEvent) => {
              if (re.type === 'memory_written') handleMemoryWritten(re.data as MemoryWrittenEventData);
              else if (re.type === 'skill_patch_proposed') handleSkillPatchProposed(re.data as SkillPatchProposedData, sid);
              else if (re.type === 'skill_patched') handleSkillPatched(re.data as { skill_id: string; version: number });
            },
          });
        }
      } else if (e.type === 'error') {
        sealAnswer(sessionId);
        finalizeThinking(sessionId);
        const d = e.data as { message?: string };
        pushAgentText(sessionId, t('error.turnError', { message: d.message ?? 'error' }));
        finishLiveTurn(sessionId, requestId);
      }
    },
    onError: (msg: string) => {
      if (turnRequestRef.current !== requestId) return;
      sealAnswer(sessionId);
      finalizeThinking(sessionId);
      pushAgentText(sessionId, t('error.connectionError', { message: msg }));
      finishLiveTurn(sessionId, requestId);
    },
    onClose: () => {
      if (turnRequestRef.current !== requestId) return;
      sealAnswer(sessionId);
      finalizeThinking(sessionId);
      finishLiveTurn(sessionId, requestId);
    },
  });

  const sendLive = async (q: string, originSessionId: string, requestId: number) => {
    let sessionId = liveSessionId.current;
    // Ensure a real backend session exists (lazy, reused across sends).
    if (!sessionId) {
      const res = await api.createSession();
      if (turnRequestRef.current !== requestId) {
        // Stop was pressed while the session POST was pending. Remove the empty
        // server row so it does not reappear as a ghost conversation on restart.
        if (res.success && res.data?.id) void api.deleteSession(res.data.id);
        return;
      }
      if (res.success && res.data?.id) {
        const id = res.data.id;
        sessionId = id;
        const pendingThread = threadCacheRef.current.get(originSessionId) ?? [];
        threadCacheRef.current.delete(originSessionId);
        threadCacheRef.current.set(id, pendingThread);
        // A very fast room switch can happen while POST /sessions is pending.
        // Only move the visible selection if this is still the originating room.
        if (activeSessionIdRef.current === originSessionId) {
          liveSessionId.current = id;
          showSessionThread(id, pendingThread);
        }
        needsTitleRefresh.current.add(id); // 첫 턴 완료 후 제목 생성 필요
        // Show the new chat in the sidebar IMMEDIATELY (ChatGPT-style) with a
        // truncated first-message label instead of waiting for the turn to end.
        // The real LLM title (POST /title) replaces this once the first turn ends.
        const provisional = q.replace(/\s+/g, ' ').trim().slice(0, 40) || t('untitledConversation');
        setSessions((prev) => [{ id, title: provisional, timestamp: new Date(), messageCount: 0 }, ...prev]);
      } else {
        finalizeThinking(originSessionId);
        pushAgentText(originSessionId, t('error.sessionCreateFailed', { message: res.error ?? 'unknown' }));
        runningSessionIdRef.current = null;
        setBusy(false);
        setWorkProgress(null);
        return;
      }
    }
    if (!sessionId) return;
    runningSessionIdRef.current = sessionId;
    setWorkProgress({ sessionId });
    streamHandleRef.current = sseClient.streamText(sessionId, q, approvalMode, webSearchEnabled, buildStreamHandlers(sessionId, requestId));
  };

  // Open a DB-backed session: load its persisted messages and make it the live
  // target so subsequent sends continue the same conversation.
  const openLiveSession = async (id: string) => {
    const cached = threadCacheRef.current.get(id);
    showSessionThread(id, cached ?? []);
    liveSessionId.current = id;
    setIsEmpty(false);
    // The runtime persists a ReAct turn atomically at completion. While this room
    // is streaming, its in-memory cache is newer than GET /messages and must win.
    if (runningSessionIdRef.current === id && cached) {
      setLoadedSession(id);
      return;
    }
    const res = await api.getMessages(id);
    const msgs = res.success ? ((res.data as { messages?: RawMessage[] })?.messages ?? []) : [];
    const next = toThread(msgs);
    threadCacheRef.current.set(id, next);
    if (activeSessionIdRef.current === id) {
      threadRef.current = next;
      setThreadState(next);
      setLoadedSession(id);
    }
  };

  // Re-fetch approvals still parked on the server for a session and re-render their
  // cards (CONTRACTS 1.2 / RUNTIME_DESIGN 5.1). A parked approval exists ONLY while a
  // turn is suspended awaiting an answer, so this recovers something only when the
  // connection dropped mid-turn (e.g. the app reloaded with a prompt still open) —
  // hence it's tied to (re)connect, NOT to session selection (an idle room is always
  // empty). The liveSessionId guard drops a stale paint if the user navigated away
  // while the request was in flight.
  const recoverPendingApprovals = async (sessionId: string) => {
    const res = await api.getPendingApprovals(sessionId);
    if (!res.success || !res.data?.length) return;
    updateThreadFor(sessionId, (prev) => {
      const have = new Set(
        prev.flatMap((mm) => (mm.kind === 'approval' || mm.kind === 'patch' ? [mm.approvalId] : [])),
      );
      const recovered = res
        .data!.filter((p) => !have.has(p.approval_id))
        .map(pendingToThread)
        .filter((mm): mm is ThreadMessage => mm !== null);
      return recovered.length ? [...prev, ...recovered] : prev;
    });
    // 재연결/재시작로 복구된 아직-파킹 중인 스킬 제안도 제안함에 미러(안전망) — 다른
    // 세션을 보고 있으면 복구된 카드를 놓칠 수 있다. mirrorParkedProposal은 approval_id로
    // 멱등이라 중복/이미결정분은 건너뛴다. (payload에 top-level approval_id를 실어준다)
    res.data
      .filter((p) => p.kind === 'skill_patch')
      .forEach((p) =>
        mirrorParkedProposal({ ...(p.payload as SkillPatchProposedData), approval_id: p.approval_id }, sessionId),
      );
  };

  // Sidebar selection: load from DB when connected, else the scripted demo thread.
  const handleSelectSession = (id: string) => {
    // 이미 열린 방을 다시 클릭하면 무시: 불필요한 재조회를 막고, 스트리밍 중이면
    // DB 히스토리가 진행 중인 라이브 thread를 덮어쓰는 것도 방지한다.
    if (id === activeSessionIdRef.current) return;
    if (connected) void openLiveSession(id);
    else {
      activeSessionIdRef.current = id;
      setActiveSessionId(id);
    }
  };

  const renameSession = async (id: string, title: string): Promise<boolean> => {
    const nextTitle = title.trim();
    if (!nextTitle) return false;
    if (connected) {
      const res = await api.renameSession(id, nextTitle);
      if (!res.success) {
        toast.notify({
          message: t('sessionSidebar.renameFailed', { error: apiErrorMessage(res) }),
          variant: 'error',
        });
        return false;
      }
    }
    // A manual name is authoritative; don't spend a later post-turn request on
    // generating an AI title for this session.
    needsTitleRefresh.current.delete(id);
    setSessions((previous) => previous.map((session) => (
      session.id === id ? { ...session, title: nextTitle } : session
    )));
    return true;
  };

  // Delete: confirm first (ConfirmDialog), then remove server-side + locally.
  const requestDeleteSession = (id: string) => {
    const s = sessions.find((x) => x.id === id);
    if (s) setConfirmDelete(s);
  };

  const performDelete = async () => {
    const target = confirmDelete;
    setConfirmDelete(null);
    if (!target) return;
    if (connected) {
      const res = await api.deleteSession(target.id);
      if (!res.success) return; // leave the list as-is on failure
    }
    const remaining = sessions.filter((s) => s.id !== target.id);
    setSessions(remaining);
    // If the deleted session was open, move to the next most recent or a blank chat.
    threadCacheRef.current.delete(target.id);
    if (activeSessionIdRef.current === target.id || liveSessionId.current === target.id) {
      liveSessionId.current = null;
      if (connected && remaining.length > 0) void openLiveSession(remaining[0].id);
      else newChat();
    }
  };

  // Stop an in-flight turn: close the SSE stream client-side AND tell the runtime
  // to abort (orchestrator stops the loop + cancels parked approvals).
  const stopTurn = () => {
    const runningSessionId = runningSessionIdRef.current;
    streamHandleRef.current?.abort();
    streamHandleRef.current = null;
    turnRequestRef.current += 1;
    if (runningSessionId) void api.abort(runningSessionId);
    if (runningSessionId) finalizeThinking(runningSessionId);
    runningSessionIdRef.current = null;
    setBusy(false);
    setWorkProgress(null);
  };

  // Push the user bubble + a live "생각 중…" placeholder, then stream the turn.
  // Shared by send() and handleCorrect() (a correction is just a user turn).
  const runLiveTurn = (q: string) => {
    const originSessionId = liveSessionId.current ?? activeSessionIdRef.current;
    const requestId = ++turnRequestRef.current;
    setIsEmpty(false);
    sealAnswer(originSessionId); // flush any lingering typewriter tail from the previous turn
    setBusy(true);
    runningSessionIdRef.current = originSessionId || null;
    setWorkProgress({ sessionId: originSessionId });
    updateThreadFor(originSessionId, (p) => [
      ...p,
      { kind: 'user', text: q, sentAt: Date.now() },
      { kind: 'thinking', duration: '', text: '', streaming: true, startedAt: Date.now() },
    ]);
    void sendLive(q, originSessionId, requestId);
  };

  // overrideText: 스타터 프롬프트 카드처럼 draft state를 거치지 않고 즉시 보낼 때 사용
  // (setDraft 직후 같은 틱에서 send()를 부르면 draft가 아직 반영 전이라 stale하게 읽힌다)
  const send = (overrideText?: string) => {
    if (busy) return; // a live turn is still streaming — ignore re-entrant sends
    const body = (overrideText ?? draft).trim();
    if (!command && !body) return;
    const q = command ? `/${command}${body ? ' ' + body : ''}` : body;
    setDraft('');
    setCommand(null);
    setIsEmpty(false);

    // Runtime connected → real streaming turn.
    if (api.isEnabled) {
      runLiveTurn(q);
      return;
    }

    // Offline (design demo) → scripted mock turn.
    const runId = 'r' + Date.now();
    setThread((p) => [...p, { kind: 'user', text: q, sentAt: Date.now() }]);

    // thinking trace
    setTimeout(() => {
      setThread((p) => [
        ...p,
        {
          kind: 'thinking',
          duration: t('mock.thinkingDuration'),
          steps: [
            { label: t('mock.stepInterpret'), detail: q.length > 40 ? q.slice(0, 40) + '…' : q },
            { label: t('mock.stepPlan'), detail: t('mock.stepPlanDetail'), tool: 'plan.build' },
            { label: t('mock.stepSelectTool'), detail: t('mock.stepSelectToolDetail'), tool: 'tool.select' },
            { label: t('mock.stepExecute'), detail: t('mock.stepExecuteDetail'), tool: 'tool.execute' },
          ],
        },
      ]);
    }, 200);

    // running tool card
    setTimeout(() => {
      setThread((p) => [
        ...p,
        {
          kind: 'toolrun',
          id: runId,
          label: t('mock.toolrunLabel'),
          status: 'running',
          steps: 0,
          time: '',
          open: true,
          calls: [
            { tool: 'plan.build', label: t('mock.callPlanLabel'), status: 'done', time: '0.4s', params: { goal: q.slice(0, 40) }, result: t('mock.callPlanResult') },
            { tool: 'tool.select', label: t('mock.callSelectLabel'), status: 'done', time: '0.3s', params: { from: 'installed' }, result: t('mock.callSelectResult') },
            { tool: 'tool.execute', label: t('mock.callExecuteLabel'), status: 'running', time: '', params: {}, result: '' },
          ],
        },
      ]);
    }, 350);

    // finish run
    setTimeout(() => {
      setThread((p) =>
        p.map((m) =>
          m.kind === 'toolrun' && m.id === runId
            ? {
                ...m,
                status: 'done',
                steps: 5,
                time: t('mock.runTime'),
                open: false,
                calls: m.calls.map((c) => ({
                  ...c,
                  status: 'done' as const,
                  time: c.time || '0.9s',
                  label: c.label === t('mock.callExecuteLabel') ? t('mock.callExecuteLabelDone') : c.label,
                  result: c.result || t('mock.callDone'),
                })),
              }
            : m,
        ),
      );
    }, 1500);

    // answer
    setTimeout(() => {
      setThread((p) => [
        ...p,
        {
          kind: 'agent',
          text: t('mock.agentAnswer'),
        },
      ]);
    }, 1700);
  };

  const newChat = () => {
    if (!busy) resetLiveRender();
    setIsEmpty(true);
    showSessionThread('', []);
    // Start a fresh backend session — otherwise the next send reuses the previous
    // liveSessionId and the model still sees the whole prior history (the "새 대화가
    // 다 기억함" bug). A new session id is created lazily on the next send.
    liveSessionId.current = null;
    // A running turn belongs to its captured session and continues in the
    // background; the global progress surface remains visible above this draft.
  };

  const manageFromPopover = () => {
    panelScrollRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
  };

  // open the conversation a proposal's evidence came from, then scroll to + highlight it
  const openConversation = (conversationId: string, messageIndex?: number) => {
    if (connected) void openLiveSession(conversationId);
    else {
      activeSessionIdRef.current = conversationId;
      setActiveSessionId(conversationId);
    }
    if (messageIndex != null) setPendingScroll({ session: conversationId, index: messageIndex });
  };

  // Insert a "/command" from the skills popover as a command chip; the textarea
  // is left for the arguments only.
  const insertSkill = (cmd: string) => {
    setCommand(cmd);
    requestAnimationFrame(() => composerRef.current?.focus());
  };

  const clearCommand = () => {
    setCommand(null);
    requestAnimationFrame(() => composerRef.current?.focus());
  };

  // 글로벌 커맨드 팔레트(Cmd+K)에서 오는 "스킬 실행"·"새 대화" 요청을 받는다.
  useEffect(() => {
    const onRunSkill = (e: Event) => {
      const cmd = (e as CustomEvent<{ command: string }>).detail?.command;
      if (cmd) insertSkill(cmd);
    };
    const onNewChat = () => newChat();
    window.addEventListener(RUN_SKILL_EVENT, onRunSkill);
    window.addEventListener(NEW_CHAT_EVENT, onNewChat);
    return () => {
      window.removeEventListener(RUN_SKILL_EVENT, onRunSkill);
      window.removeEventListener(NEW_CHAT_EVENT, onNewChat);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Find a slash-command token at or just before the cursor.
  // Returns { token, slashIndex } when the text immediately before `cursor`
  // looks like "/word" (no space after the slash yet), otherwise null.
  const findSlashToken = (value: string, cursor: number) => {
    const before = value.slice(0, cursor);
    // Must be preceded by start-of-string or whitespace so we don't trigger on URLs
    const m = before.match(/(^|[\s\n])\/([A-Za-z0-9_-]*)$/);
    if (!m) return null;
    const slashIndex = before.lastIndexOf('/');
    return { token: m[2], slashIndex };
  };

  // Textarea onChange: detect an in-progress "/command" token at the cursor and
  // open the inline popover. When the user adds a space after the token, promote
  // it to the command chip and strip it from the draft.
  const handleDraftChange = (value: string, cursor?: number) => {
    const pos = cursor ?? value.length;
    if (!command) {
      // Completed command: "/word<space>" anywhere in the draft → promote to chip
      const completedM = value.match(/(^|[\s\n])\/([A-Za-z0-9_-]+)([ \t]+)([\s\S]*)$/);
      if (completedM && value.slice(0, pos).endsWith('/' + completedM[2] + completedM[3])) {
        const before = value.slice(0, value.lastIndexOf('/' + completedM[2]));
        const after = completedM[4];
        setCommand(completedM[2]);
        setDraft((before + after).trimStart());
        setSkillsOpen(false);
        return;
      }
      // In-progress "/word" at cursor → open inline popover
      if (findSlashToken(value, pos)) {
        setSkillsOpen(true);
      } else if (skillsOpen) {
        setSkillsOpen(false);
      }
    }
    setDraft(value);
  };

  // Query string shown in the inline popover (text typed after the "/")
  const inlineQuery = (() => {
    if (command) return '';
    const pos = composerRef.current?.selectionStart ?? draft.length;
    const result = findSlashToken(draft, pos);
    return result ? result.token : '';
  })();

  // Whether the inline popover should be shown (used in both render + button guard)
  const isInlineSlash = !command && !!findSlashToken(draft, composerRef.current?.selectionStart ?? draft.length);

  // The per-answer action bar belongs to the END of an assistant turn — one bar
  // per response, not one after every intermediate narration block (a rich turn
  // interleaves several agent messages with thinking/tool cards). So we mark only
  // the *last* agent message of each turn (no later agent before the next user
  // message), then push the bar below any trailing skill/memory tiles it produced
  // (e.g. "새 스킬을 학습했어요") so it sits at the very bottom.
  // 제안함에서 결정된 파킹 제안의 낡은 채팅 카드는 렌더에서 걷어낸다(라이브 한정 —
  // 리로드 시엔 해소된 제안이 pending-approvals에 없어 자연히 사라진다). 평소엔
  // resolvedApprovals가 비어 원본 thread를 그대로 쓴다(제로 비용).
  const renderThread = resolvedApprovals.size
    ? thread.filter((m) => !(m.kind === 'patch' && resolvedApprovals.has(m.approvalId)))
    : thread;
  const actionsCopyAt: Record<number, string> = {};
  // index → epoch ms of the agent message that owns the action bar
  const actionsTimeAt: Record<number, number> = {};
  renderThread.forEach((m, i) => {
    if (m.kind !== 'agent') return;
    // skip intermediate narration: another agent message follows in this same turn
    for (let j = i + 1; j < renderThread.length; j++) {
      if (renderThread[j].kind === 'user') break;
      if (renderThread[j].kind === 'agent') return;
    }
    let end = i;
    while (end + 1 < renderThread.length && (renderThread[end + 1].kind === 'memory' || renderThread[end + 1].kind === 'patch' || renderThread[end + 1].kind === 'approval')) end++;
    actionsCopyAt[end] = m.text;
    if (m.sentAt) actionsTimeAt[end] = m.sentAt;
  });

  // group consecutive non-user messages into one assistant turn; each user
  // message is its own turn. The turn then renders one "RICE" identity
  // over all its blocks (thinking / tool-run / answer / memory).
  const turns: { key: string; items: { m: ThreadMessage; index: number }[] }[] = [];
  renderThread.forEach((m, i) => {
    const last = turns[turns.length - 1];
    if (m.kind !== 'user' && last && last.items[0].m.kind !== 'user') {
      last.items.push({ m, index: i });
    } else {
      turns.push({ key: `turn-${i}`, items: [{ m, index: i }] });
    }
  });

  return (
    <FileViewerProvider>
      <div className="relative flex flex-1 min-w-0 overflow-hidden">
        {/* Session Sidebar */}
        <SessionSidebar
          sessions={sessions}
          activeSessionId={activeSessionId}
          onSelectSession={handleSelectSession}
          onNewSession={newChat}
          onRenameSession={renameSession}
          onDeleteSession={requestDeleteSession}
          isOpen={sessionSidebarOpen}
          width={sessionPanel.width}
          modelName={selectedModelName}
          modelMode={modelMode}
          onOpenModelSettings={() => setSettingsPanelOpen(true)}
        />
        {sessionSidebarOpen && (
          <ResizeHandle
            onPointerDown={sessionPanel.startResize}
            onDoubleClick={sessionPanel.resetWidth}
            isResizing={sessionPanel.isResizing}
            value={sessionPanel.width} min={sessionPanel.min} max={sessionPanel.max} onResizeBy={sessionPanel.resizeBy}
            aria-label="Resize sessions panel"
          />
        )}

        {/* Main */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden bg-background">
          {/* Header — faint divider (was a hard #393939 line); left padding clears the
              fixed toggle only when the session panel is collapsed. */}
          <div
            className={`h-11 flex-shrink-0 flex items-center gap-3 pr-4 border-b border-shell-border electron-drag ${
              sessionSidebarOpen ? 'pl-4' : isElectron ? 'pl-[100px]' : 'pl-[76px]'
            }`}
          >
            {!isEmpty && (
              <span className="truncate text-body-short-01 font-semibold text-text-primary">
                {activeSession?.title || t('conversation')}
              </span>
            )}
          </div>

          {/* Thread */}
          <div className="relative flex-1 min-h-0">
            <div ref={scrollRef} onScroll={handleThreadScroll} className="h-full overflow-y-auto">
              <div className={`mx-auto w-full max-w-[920px] px-5 py-4 ${isEmpty ? 'flex min-h-full items-center' : ''}`}>
                {isEmpty ? (
                  <ChatStarter
                    active={active}
                    onPick={(text) => {
                      setDraft(text);
                      requestAnimationFrame(() => composerRef.current?.focus());
                    }}
                  />
                ) : (
                  turns.map((t) => (
                    <ChatTurn
                      key={t.key}
                      items={t.items}
                      onCorrect={handleCorrect}
                      onJumpSkill={jumpToSkill}
                      onRegenerate={regenerate}
                      onApprovePatch={handleApprovePatch}
                      onRejectPatch={handleRejectPatch}
                      onRespondApproval={handleToolApproval}
                      highlightIndex={highlightMsg}
                      actionsCopyAt={actionsCopyAt}
                      actionsTimeAt={actionsTimeAt}
                      showThinking={modelMode !== 'local' || modelSettings.showThinking}
                    />
                  ))
                )}
              </div>
            </div>

            {/* 읽는 흐름을 가리지 않는 compact floating pill. 새 메시지는 blue dot으로
                상태를 알리고, 버튼 전체를 강한 primary blue로 칠하지 않는다. */}
            {!isEmpty && !atBottom && (
              <button
                onClick={jumpToBottom}
                aria-label={hasNewBelow ? t('scrollToNew') : t('scrollToBottom')}
                title={hasNewBelow ? t('scrollToNew') : t('scrollToBottom')}
                className={`absolute bottom-3 left-1/2 flex h-[30px] w-[30px] -translate-x-1/2 items-center justify-center rounded-full border shadow-elevation-2 backdrop-blur-md transition-all ${
                  hasNewBelow
                    ? 'border-blue-50/60 bg-layer-03/95 text-blue-30 hover:border-blue-40 hover:bg-layer-hover-02'
                    : 'border-border-subtle-02 bg-layer-02/95 text-text-secondary hover:border-border-strong-01 hover:bg-layer-hover-02'
                }`}
                style={{ animation: 'jumpBtnIn 160ms cubic-bezier(0.16, 1, 0.3, 1)' }}
              >
                <Icon name="chevron-down" size={14} />
              </button>
            )}
          </div>

          <style>{`
            @keyframes jumpBtnIn {
              from { opacity: 0; transform: translate(-50%, 6px); }
              to { opacity: 1; transform: translate(-50%, 0); }
            }
          `}</style>

          {/* Composer */}
          <div className="flex-shrink-0 px-5 pt-2 pb-5">
            <div className="w-full max-w-[920px] mx-auto relative">
              {workProgress && <ChatTaskProgress />}
              {/* Inline slash-command popover — floats above the composer box */}
              {skillsOpen && isInlineSlash && (
                <SkillsPopover
                  onInsert={(cmd) => {
                    // Strip the "/token" from wherever it sits in the draft
                    const pos = composerRef.current?.selectionStart ?? draft.length;
                    const found = findSlashToken(draft, pos);
                    if (found) {
                      const stripped = draft.slice(0, found.slashIndex) + draft.slice(pos);
                      setDraft(stripped.trimStart());
                    } else {
                      setDraft('');
                    }
                    insertSkill(cmd);
                    setSkillsOpen(false);
                  }}
                  onClose={() => setSkillsOpen(false)}
                  onEscape={() => composerRef.current?.focus()}
                  onManage={() => { setSkillsOpen(false); manageFromPopover(); }}
                  webSearchEnabled={webSearchEnabled}
                  onWebSearchChange={setWebSearchEnabled}
                  initialQuery={inlineQuery}
                  inlineMode
                />
              )}
              <div
                data-tour="chat-input"
                className="bg-layer-01 border border-border-strong-01 rounded-xl transition-colors duration-150 focus-within:border-focus"
              >
                {/* Active slash-command chip — the command lives here, not in the textarea */}
                {command && (
                  <div
                    className="flex items-center gap-2 px-3.5 pt-3 pb-1"
                    style={{ animation: 'chipIn 160ms cubic-bezier(0.16, 1, 0.3, 1)' }}
                  >
                    <span className="inline-flex items-center gap-1.5 pl-1 pr-1 py-1 bg-interactive/15 border border-interactive/40 rounded-lg text-caption-01 select-none">
                      <span
                        className="w-5 h-5 rounded-md flex items-center justify-center flex-shrink-0"
                        style={{ background: 'var(--gradient-skill)' }}
                      >
                        <Icon name="skill" size={11} className="text-white" />
                      </span>
                      <span className="font-mono font-medium text-blue-30">/{command}</span>
                      <button
                        onClick={clearCommand}
                        className="ml-0.5 p-0.5 rounded-md text-blue-30/70 hover:text-white hover:bg-interactive/40 transition-colors"
                        title={t('removeCommandTitle')}
                      >
                        <Icon name="close" size={11} />
                      </button>
                    </span>
                    <span className="text-caption-01 text-text-helper">{t('skillCommandHint')}</span>
                  </div>
                )}
                <textarea
                  ref={composerRef}
                  value={draft}
                  onChange={(e) => handleDraftChange(e.target.value, e.target.selectionStart ?? undefined)}
                  onKeyDown={(e) => {
                    // `isComposing` guard: while a Korean/IME composition is being
                    // committed, Enter fires a keydown too — without this the turn
                    // sends twice (the message + the trailing jamo). Skip those.
                    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                      e.preventDefault();
                      send();
                    }
                    // Backspace at the start of empty args removes the command chip
                    if (e.key === 'Backspace' && command && !draft) {
                      e.preventDefault();
                      setCommand(null);
                    }
                    // Escape closes the inline slash-command popover
                    if (e.key === 'Escape' && skillsOpen) {
                      e.preventDefault();
                      setSkillsOpen(false);
                    }
                  }}
                  placeholder={command ? t('placeholderWithCommand') : t('placeholder')}
                  rows={1}
                  className="w-full resize-none border-none outline-none bg-transparent text-text-primary text-body-short-02 leading-5 px-4 pt-3.5 pb-1 min-h-[56px] max-h-[200px]"
                />
                <div className="flex items-center gap-1.5 px-2.5 pb-2.5">
                  <div className="relative">
                    <Tooltip label={t('addSkillTool')}>
                      <button
                        type="button"
                        onClick={() => {
                          setApprovalMenuOpen(false);
                          setSkillsOpen((o) => !o);
                        }}
                        aria-label={t('addSkillTool')}
                        className={`flex h-7 w-7 items-center justify-center rounded-md transition-colors ${
                        skillsOpen
                            ? 'bg-interactive/15 text-blue-30'
                            : 'text-icon-secondary hover:bg-layer-hover-01 hover:text-icon-primary'
                        }`}
                      >
                        <Icon name="add" size={15} />
                      </button>
                    </Tooltip>
                    {/* Button-triggered popover — only when NOT in inline "/" mode */}
                    {skillsOpen && !isInlineSlash && (
                      <SkillsPopover
                        onInsert={insertSkill}
                        onClose={() => setSkillsOpen(false)}
                        onEscape={() => composerRef.current?.focus()}
                        onManage={manageFromPopover}
                        webSearchEnabled={webSearchEnabled}
                        onWebSearchChange={setWebSearchEnabled}
                      />
                    )}
                  </div>
                  <ApprovalModePicker
                    value={approvalMode}
                    open={approvalMenuOpen}
                    onToggle={() => {
                      setSkillsOpen(false);
                      setLearnOpen(false);
                      setApprovalMenuOpen((open) => !open);
                    }}
                    onClose={() => setApprovalMenuOpen(false)}
                    onChange={setApprovalMode}
                  />
                  {connected && liveSessionId.current && (
                    <div className="relative">
                        <Tooltip label={t('learn.tooltip')}>
                        {/* 호출(스킬·도구 chip)과 달리 이 버튼은 '학습' — AI 액센트(purple)로
                            성격을 구분한다 (AI 문법의 그라디언트 끝 색) */}
                        <button
                          onClick={() => setLearnOpen((o) => !o)}
                          className={`p-1.5 rounded-lg transition-colors ${
                            learnOpen
                              ? 'bg-layer-selected-01 text-purple-40'
                              : 'text-purple-50 hover:text-purple-40 hover:bg-layer-hover-01'
                          }`}
                        >
                          <Icon name="sparkle" size={16} />
                        </button>
                      </Tooltip>
                      {learnOpen && (
                        <div className="absolute bottom-10 left-0 z-30 w-72 bg-layer-02 border border-border-subtle-01 rounded-lg shadow-elevation-2 p-3">
                          <div className="flex items-center gap-1.5 mb-2">
                            <Icon name="sparkle" size={14} className="text-purple-40" />
                            <span className="text-body-short-01 font-semibold text-text-primary">{t('learn.saveTitle')}</span>
                            <span className="ai-label ml-auto">
                              <span className="ai-label__text">AI</span>
                            </span>
                          </div>
                          <textarea
                            value={learnInstruction}
                            onChange={(e) => setLearnInstruction(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' && !e.shiftKey) {
                                e.preventDefault();
                                learnFromConversation(learnInstruction.trim());
                                setLearnInstruction('');
                              } else if (e.key === 'Escape') {
                                setLearnOpen(false);
                              }
                            }}
                            placeholder={t('learn.placeholder')}
                            rows={2}
                            autoFocus
                            className="w-full bg-field-01 border border-border-subtle-01 rounded-md px-2.5 py-2 text-body-short-01 text-text-primary placeholder:text-text-placeholder resize-none focus:outline-none focus:border-interactive"
                          />
                          <div className="flex items-center justify-end gap-1.5 mt-2">
                            <button
                              onClick={() => setLearnOpen(false)}
                              className="h-7 px-2.5 rounded-md text-caption-01 text-text-secondary hover:bg-layer-hover-01 transition-colors"
                            >
                              {t('learn.cancel')}
                            </button>
                            <button
                              onClick={() => {
                                learnFromConversation(learnInstruction.trim());
                                setLearnInstruction('');
                              }}
                              className="inline-flex items-center gap-1 h-7 px-2.5 rounded-md text-caption-01 font-semibold bg-interactive text-white hover:bg-interactive-hover transition-colors"
                            >
                              <Icon name="sparkle" size={12} /> {t('learn.submit')}
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                  <div className="flex-1" />
                  <Tooltip label={busy ? t('stopTooltip') : t('sendTooltip')}>
                    <button
                      onClick={() => (busy ? stopTurn() : send())}
                      disabled={!busy && !command && !draft.trim()}
                      className={`w-7 h-7 rounded-full flex items-center justify-center transition-opacity duration-150 ${
                        busy
                          ? 'bg-layer-02 hover:bg-layer-hover-02 border border-border-subtle-01 cursor-pointer'
                          : command || draft.trim()
                          ? 'hover:opacity-90 cursor-pointer'
                          : 'bg-layer-02 cursor-not-allowed opacity-50'
                      }`}
                      style={!busy && (command || draft.trim()) ? { background: 'var(--gradient-skill)' } : undefined}
                    >
                      <Icon name={busy ? 'close' : 'send'} size={13} className={busy ? 'text-text-primary' : 'text-white'} />
                    </button>
                  </Tooltip>
                </div>
              </div>
              {workspacePath && (
                <div className="mt-3 flex min-w-0 max-w-full items-center gap-1 px-1 text-caption-01 text-text-helper">
                  <Tooltip label={t('saveLocationOpenTooltip')}>
                    <button
                      type="button"
                      onClick={openWorkspaceFolder}
                      className="flex flex-shrink-0 items-center gap-1.5 rounded-md py-1 pr-1 transition-colors hover:text-text-primary"
                      aria-label={t('saveLocationOpenAriaLabel')}
                    >
                      <Icon name="folder" size={18} className="flex-shrink-0 text-icon-secondary" />
                      <span className="text-text-secondary">{t('saveLocation')}</span>
                    </button>
                  </Tooltip>
                  <Tooltip label={t('saveLocationChangeTooltip', { path: workspacePath })}>
                    <button
                      type="button"
                      onClick={chooseWorkspaceFolder}
                      disabled={workspaceSaving}
                      className="min-w-0 truncate rounded-md py-1 font-mono transition-colors hover:text-text-primary hover:underline"
                      aria-label={t('saveLocationChangeAriaLabel', { path: workspacePath })}
                    >
                      {workspacePath}
                    </button>
                  </Tooltip>
                </div>
              )}
              <style>{`
                @keyframes chipIn {
                  from { opacity: 0; transform: translateY(3px) scale(0.97); }
                  to { opacity: 1; transform: none; }
                }
                @keyframes riceLearningPanelEnter {
                  from { opacity: 0; transform: translateX(20px); }
                  to { opacity: 1; transform: translateX(0); }
                }
                @keyframes riceLearningToggleRotate {
                  from { transform: rotate(-100deg); }
                  to { transform: rotate(0); }
                }
                .rice-learning-panel-enter {
                  animation: riceLearningPanelEnter 260ms cubic-bezier(0.16, 1, 0.3, 1) both;
                }
                .rice-learning-toggle-active {
                  animation: riceLearningToggleRotate 280ms cubic-bezier(0.16, 1, 0.3, 1) both;
                }
              `}</style>
            </div>
          </div>
        </div>

        {/* Learning Panel — its width expands from the right, so the chat area
            is pushed in rather than covered. */}
        <div
          className="relative flex-shrink-0 overflow-hidden transition-[width] duration-[280ms] ease-[cubic-bezier(0.16,1,0.3,1)]"
          style={{ width: learningPanelOpen ? learningPanel.width + 6 : 0 }}
        >
          {learningPanelOpen && (
            <div className="rice-learning-panel-enter flex h-full">
              <ResizeHandle
                onPointerDown={learningPanel.startResize}
                onDoubleClick={learningPanel.resetWidth}
                isResizing={learningPanel.isResizing}
                value={learningPanel.width} min={learningPanel.min} max={learningPanel.max} onResizeBy={learningPanel.resizeBy}
                aria-label="Resize learning loop panel"
              />
              <LearningPanel width={learningPanel.width} onOpenConversation={openConversation} onNavigate={navigate} />
            </div>
          )}
        </div>

        {/* Fixed chat-list toggle — rendered LAST on purpose. In Electron the drag-region
            hit test is resolved in DOM order (later wins), so a no-drag button placed before
            the sidebar/header drag strips gets re-covered and the OS swallows the click as a
            window move. As the final child its no-drag hole wins → reliably clickable. It's a
            small window-drag strip pinned by the macOS traffic lights, staying put whether the
            session panel is open or collapsed. */}
        <div
          className="absolute top-0 left-0 z-30 flex h-11 items-center gap-0.5 electron-drag"
          style={{ paddingLeft: isElectron ? 30 : 8 }}
        >
          <Tooltip
            label={sessionSidebarOpen ? t('sessionSidebarClose') : t('sessionSidebarOpen')}
            side="bottom-start"
          >
            <button
              type="button"
              onClick={() => setSessionSidebarOpen((o) => !o)}
              className="electron-no-drag flex h-6 w-6 items-center justify-center rounded-md text-icon-secondary hover:bg-layer-hover-01 hover:text-icon-primary transition-colors"
              aria-label={sessionSidebarOpen ? t('sessionSidebarClose') : t('sessionSidebarOpen')}
            >
              <Icon name="sidebar" size={17} />
            </button>
          </Tooltip>
          <Tooltip label={t('quickLaunch')} side="bottom">
            <button
              type="button"
              onClick={openCommandPalette}
              className="electron-no-drag flex h-6 w-6 items-center justify-center rounded-md text-icon-secondary transition-colors hover:bg-layer-hover-01 hover:text-icon-primary"
              aria-label={t('quickLaunchAriaLabel')}
            >
              <Icon name="search" size={17} />
            </button>
          </Tooltip>
        </div>

        {/* 패널의 기능(renew)이 아니라 레이아웃 열기/닫기임을 모양으로 명시한다. */}
        <div className="absolute top-0 right-0 z-30 flex h-11 items-center pr-2 electron-drag">
          <Tooltip
            label={learningPanelOpen ? t('learningPanelClose') : t('learningPanelOpen')}
            side="bottom-end"
          >
            <button
              type="button"
              onClick={() => setLearningPanelOpen((open) => !open)}
              data-tour="learning-loop"
              className="electron-no-drag flex h-[28px] w-[28px] items-center justify-center rounded-md text-icon-secondary transition-colors hover:bg-layer-hover-01 hover:text-icon-primary"
              aria-label={learningPanelOpen ? t('learningPanelClose') : t('learningPanelOpen')}
            >
              <Icon name={learningPanelOpen ? 'side-panel-close' : 'side-panel-open'} size={17} />
            </button>
          </Tooltip>
        </div>
      </div>

      {/* Model Settings Panel (기어 아이콘) */}
      <ModelSettingsPanel
        isOpen={settingsPanelOpen}
        onClose={() => setSettingsPanelOpen(false)}
        currentModel={currentModel}
        onSave={(payload) => {
          persistModelSelection(payload);
          setSettingsPanelOpen(false);
        }}
        settings={modelSettings}
        modelMode={modelMode}
      />

      {/* Delete-session confirmation (irreversible) */}
      <ConfirmDialog
        open={!!confirmDelete}
        title={t('delete.title')}
        description={
          <>
            <span className="font-medium text-text-primary">{confirmDelete?.title}</span> {t('delete.description')}
          </>
        }
        confirmLabel={t('delete.confirm')}
        tone="danger"
        confirmIcon="trash"
        onConfirm={performDelete}
        onClose={() => setConfirmDelete(null)}
      />
    </FileViewerProvider>
  );
}

export default ChatScreen;

// Made with Bob
