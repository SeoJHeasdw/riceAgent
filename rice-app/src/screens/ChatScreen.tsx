import { useEffect, useRef, useState } from 'react';
import { SessionSidebar, type Session } from '../components/carbon/SessionSidebar';
import { ModelSettingsPanel } from '../components/carbon/ModelSettingsPanel';
import { LearningPanel } from '../components/carbon/LearningPanel';
import { ChatTurn } from '../components/carbon/ChatMessage';
import { useResizable } from '../hooks/useResizable';
import { useLearning } from '../lib/learning/LearningContext';
import type { Skill, ThreadMessage, UserSignal } from '../lib/learningData';
import { defaultModelSettings } from '../lib/mockData';
import { RUN_SKILL_EVENT, NEW_CHAT_EVENT, openCommandPalette } from '../lib/commandBridge';
import { api } from '../lib/api';
import { sseClient } from '../lib/sse';
import type { ApprovalMode, PatchTrigger, PendingApproval, PlanUpdatedEventData, ToolApprovalRequiredData, SkillPatchProposedData, MemoryWrittenEventData, SSEEvent } from '../lib/types';
import { Icon, Kbd, ResizeHandle, ConfirmDialog, Tooltip } from '../components/ui';
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
  metadata?: { thinking?: string; tool_call_id?: string; plan?: PlanUpdatedEventData; synthetic?: string } | null;
};

type ThreadStateUpdate = ThreadMessage[] | ((previous: ThreadMessage[]) => ThreadMessage[]);

type LiveTurnProgress = {
  sessionId: string;
};

// Human-readable, per-tool copy for the HITL approval card — derived client-side
// from the tool name + args, so the confirmation line is contextual with ZERO
// added latency (no extra model call). Extend the map as builtins grow.
const describeTool = (tool: string, args: Record<string, unknown>): { label: string; reason: string; approved: string; denied: string } => {
  const path = typeof args.path === 'string' ? (args.path as string) : undefined;
  switch (tool) {
    case 'write_file':
      return {
        label: path ? `${path} 작성` : '파일 작성',
        reason: '워크스페이스에 파일을 생성·수정합니다.',
        approved: path ? `${path} 파일을 작성했어요` : '파일을 작성했어요',
        denied: '파일을 작성하지 않았어요',
      };
    case 'read_file':
      return {
        label: path ? `${path} 읽기` : '파일 읽기',
        reason: '워크스페이스 파일을 읽습니다.',
        approved: path ? `${path} 파일을 읽었어요` : '파일을 읽었어요',
        denied: '읽지 않았어요',
      };
    default:
      return { label: tool, reason: '실행 전 승인이 필요합니다.', approved: `${tool} 실행했어요`, denied: '실행하지 않았어요' };
  }
};

const toSession = (s: RawSession): Session => ({
  id: s.id,
  title: s.title || '제목 없는 대화',
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
  for (const m of msgs) {
    if (m.role === 'user') {
      // 루프가 주입한 합성 메시지(빈 응답 넛지 등)는 사용자가 친 말이 아니다 — 숨긴다.
      if (m.content && !m.metadata?.synthetic) out.push({ kind: 'user', text: m.content, sentAt: m.ts });
    } else if (m.role === 'assistant') {
      if (m.metadata?.thinking) out.push({ kind: 'thinking', duration: '', text: m.metadata.thinking, streaming: false });
      if (m.metadata?.plan) out = upsertPlanBlock(out, m.metadata.plan);
      // update_plan·start_plan의 화면 표면은 계획 카드다 — 별도 도구 카드는 노이즈.
      const realCalls = (m.tool_calls ?? []).filter(
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
const TRIGGER_LABELS: Record<PatchTrigger, string> = {
  T1a: '사용자 교정',
  T1b: '실패 후 개선',
  T1c: '누락 보완',
  T3: '정리 통합',
  T4: '신규 제안',
  T5: '에이전트 제안',
};

// Map a parked approval (recovered after an SSE reconnect via
// GET /sessions/{id}/pending-approvals, CONTRACTS 1.2) into a thread card so the
// prompt re-renders. Approval state lives on the server, independent of the SSE
// connection — the client just re-paints what's still pending.
const pendingToThread = (p: PendingApproval): ThreadMessage | null => {
  if (p.kind === 'tool_approval') {
    const d = p.payload as ToolApprovalRequiredData;
    return {
      kind: 'approval',
      approvalId: p.approval_id,
      tool: d.tool_name,
      label: d.tool_name,
      scope: { name: d.tool_name, mode: '실행' },
      reason: d.reason ?? '실행 전 승인이 필요합니다.',
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
    fromVer: isNew ? '(신규)' : d.from_version !== undefined ? `v${d.from_version}` : '현재',
    toVer: d.to_version !== undefined ? `v${d.to_version}` : '개선',
    changelog: d.changelog,
    trigger: d.trigger,
    triggerLabel: TRIGGER_LABELS[d.trigger] ?? d.trigger,
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
          ↵ 삽입
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
          <span className="text-body-short-01 font-semibold text-text-primary">이 대화에서 쓸 수 있는 것</span>
          <span className="ml-auto text-label-micro font-mono text-text-helper">
            <span className="text-text-secondary">/</span> 로 호출
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
            placeholder="스킬·도구·전문가 검색"
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
            <div className="text-caption-01 font-medium text-text-primary">웹 검색 허용</div>
            <div className="truncate text-label-micro text-text-helper">이 대화의 다음 요청부터 적용</div>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={webSearchEnabled}
            aria-label="웹 검색 허용"
            onClick={() => onWebSearchChange(!webSearchEnabled)}
            className={`relative h-[18px] w-8 flex-shrink-0 rounded-full transition-colors ${webSearchEnabled ? 'bg-interactive' : 'bg-layer-03 ring-1 ring-inset ring-border-strong-01'}`}
          >
            <span className={`absolute top-0.5 h-3.5 w-3.5 rounded-full bg-white shadow-elevation-1 transition-[left] ${webSearchEnabled ? 'left-4' : 'left-0.5'}`} />
          </button>
        </div>
        {empty && (
          <div className="flex flex-col items-center gap-1.5 px-3 py-6 text-center">
            <Icon name="search" size={18} className="text-icon-secondary opacity-50" />
            <div className="text-caption-01 text-text-helper">검색 결과가 없어요</div>
          </div>
        )}

        {skillItems.length > 0 && (
          <div>
            <GroupLabel count={skillItems.length} tint={ACCENT.skill}>스킬</GroupLabel>
            {skillItems.map((s, i) => (
              <CmdRow
                key={s.id}
                icon="skill"
                command={s.command!}
                sub={`${s.ver} · ${s.uses}회 사용`}
                tint={ACCENT.skill}
                index={i}
              />
            ))}
          </div>
        )}

        {toolItems.length > 0 && (
          <div>
            <GroupLabel count={toolItems.length} tint={ACCENT.tool}>도구</GroupLabel>
            {toolItems.map((t, i) => (
              <CmdRow
                key={t.id}
                icon={t.icon}
                command={t.command ?? t.id}
                sub={t.meta}
                tint={ACCENT.tool}
                index={toolOffset + i}
              />
            ))}
          </div>
        )}

        {pluginItems.length > 0 && (
          <div>
            <GroupLabel count={pluginItems.length} tint={ACCENT.plugin}>플러그인 · 전문가</GroupLabel>
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
                        제공 스킬 {provides.length} · {plugin.meta}
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
          <Icon name="settings" size={14} /> 학습 패널에서 관리
        </button>
        <div className="ml-auto flex items-center gap-1 pr-3 text-label-micro text-text-helper select-none">
          <Kbd>↑↓</Kbd> 탐색
          <Kbd>↵</Kbd> 삽입
          <Kbd>esc</Kbd> 닫기
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

const APPROVAL_OPTIONS: Array<{
  value: ApprovalMode;
  label: string;
  description: string;
  icon: IconName;
}> = [
  {
    value: 'ask',
    label: 'Ask for approval',
    description: '파일 변경과 외부 도구 실행 전에 항상 확인합니다.',
    icon: 'lock',
  },
  {
    value: 'approve_for_me',
    label: 'Approve for me',
    description: '일반 작업은 자동 승인하고 위험 가능성이 있는 동작만 묻습니다.',
    icon: 'check-circle',
  },
  {
    value: 'full_access',
    label: 'Full access',
    description: '승인 카드 없이 허용된 도구를 바로 실행합니다.',
    icon: 'warning',
  },
];

/** Codex-style per-turn approval policy selector. Tool cards remain unchanged;
 * this only decides whether an approval gate is automatic or interactive. */
const ApprovalModePicker: React.FC<{
  value: ApprovalMode;
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  onChange: (value: ApprovalMode) => void;
}> = ({ value, open, onToggle, onClose, onChange }) => {
  const ref = useRef<HTMLDivElement>(null);
  const selected = APPROVAL_OPTIONS.find((option) => option.value === value) ?? APPROVAL_OPTIONS[0];

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
          aria-label="도구 승인 방식"
          className="absolute bottom-full left-0 z-40 mb-2 w-[340px] max-w-[calc(100vw-40px)] overflow-hidden rounded-lg border border-border-subtle-01 bg-layer-02/95 p-1.5 shadow-elevation-2 backdrop-blur-md"
          style={{ animation: 'popoverIn 140ms cubic-bezier(0.16, 1, 0.3, 1)' }}
        >
          <div className="flex items-center gap-2 px-2 pb-1.5 pt-1">
            <span className="text-caption-01 font-semibold text-text-primary">실행 권한</span>
            <span className="ml-auto text-label-micro text-text-helper">다음 요청부터 적용</span>
          </div>
          {APPROVAL_OPTIONS.map((option) => {
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
            차단한 도구와 스킬 변경 승인은 이 설정과 별도로 유지됩니다.
          </div>
        </div>
      )}
    </div>
  );
};

/* ── 빈 채팅 스타터 화면 — 백지 대신 바로 눌러 보낼 수 있는 예시 카드 ──
 * 일반 프롬프트 3개 + (스킬을 등록해 뒀다면) 가장 많이 쓴 스킬 실행 카드 1개를
 * 섞어 보여준다. 클릭 즉시 전송되어 데모/온보딩 첫 인상에서 제품 정체성
 * (스킬로 반복 작업을 학습·자동화)을 바로 보여준다.
 */
const GENERIC_STARTERS: { icon: IconName; title: string; prompt: string }[] = [
  { icon: 'flow', title: '오늘 할 일 정리', prompt: '오늘 처리할 작업을 우선순위대로 정리해줘' },
  { icon: 'idea', title: '아이디어 브레인스토밍', prompt: '새로운 아이디어를 함께 브레인스토밍해줘' },
  { icon: 'document', title: '글 다듬기', prompt: '내가 붙여넣을 글을 다듬고 피드백을 줘' },
];

const ChatStarter: React.FC<{
  skills: Skill[];
  signals: UserSignal[];
  onPick: (text: string) => void;
}> = ({ skills, signals, onPick }) => {
  const usableSkills = [...skills]
    .filter((s) => s.enabled && s.command)
    .sort((a, b) => b.uses - a.uses);
  const topSkill = usableSkills[0];
  // 프롬프트 카드에 들어간 1위를 빼고, 나머지를 낮은 밀도의 chip으로
  const skillChips = usableSkills.slice(1, 5);
  // "RICE가 기억하는 것" — 신뢰도 높은 신호 위주로 3개까지만
  const rememberedSignals = [...signals].sort((a, b) => b.conf - a.conf).slice(0, 3);

  const cards = [
    ...GENERIC_STARTERS,
    ...(topSkill
      ? [{ icon: 'skill' as IconName, title: `${topSkill.name} 실행`, prompt: `/${topSkill.command}` }]
      : []),
  ];

  return (
    <div className="max-w-xl mx-auto pt-16 text-center">
      <div
        className="w-10 h-10 mx-auto mb-4 flex items-center justify-center rounded-xl"
        style={{ background: 'var(--gradient-skill)' }}
      >
        <Icon name="model" size={20} className="text-white" />
      </div>
      <div className="text-heading-04 font-light text-text-primary mb-2">무엇을 도와드릴까요?</div>
      <div className="text-body-short-02 text-text-helper mb-7">
        쓸수록 당신의 방식에 맞춰 학습하고, 반복 작업은 스킬로 저장합니다.
      </div>

      <div className="grid grid-cols-2 gap-2.5 text-left">
        {cards.map((c, i) => (
          <button
            key={c.title}
            onClick={() => onPick(c.prompt)}
            className="group flex items-start gap-3 p-3.5 rounded-xl border border-border-subtle-01 bg-layer-01 hover:border-border-strong-01 transition-colors duration-150"
            style={{ animation: `starterCardIn 220ms cubic-bezier(0.16, 1, 0.3, 1) both`, animationDelay: `${i * 30}ms` }}
          >
            <span className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 text-icon-secondary bg-layer-02 border border-border-subtle-02 group-hover:text-icon-primary transition-colors">
              <Icon name={c.icon} size={16} />
            </span>
            <div className="min-w-0">
              <div className="text-body-short-01 font-medium text-text-primary truncate">{c.title}</div>
              <div className="text-caption-01 text-text-helper truncate">{c.prompt}</div>
            </div>
          </button>
        ))}
      </div>

      {/* 최근 스킬 chip — 클릭 즉시 실행. 낮은 밀도, 색 없는 표면 */}
      {skillChips.length > 0 && (
        <div className="flex flex-wrap items-center justify-center gap-1.5 mt-5">
          {skillChips.map((s) => (
            <button
              key={s.id}
              onClick={() => onPick(`/${s.command}`)}
              className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-full border border-border-subtle-01 bg-transparent text-caption-01 font-mono text-text-secondary hover:text-text-primary hover:border-border-strong-01 transition-colors"
            >
              /{s.command}
              <span className="text-text-helper font-sans">{s.uses}회</span>
            </button>
          ))}
        </div>
      )}

      {/* RICE가 기억하는 것 — 학습된(AI) 순간에만 쓰는 좌측 그라디언트 문법 */}
      {rememberedSignals.length > 0 && (
        <div className="mt-6 text-left border border-dashed border-border-subtle-01 rounded-xl px-4 py-3 bg-interactive/[0.04]">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">
              RICE가 기억하는 것
            </span>
            <span className="ai-label">
              <span className="ai-label__text">AI</span>
            </span>
          </div>
          <div className="flex flex-col gap-1.5">
            {rememberedSignals.map((u) => (
              <div key={u.id} className="border-l border-border-subtle-02 pl-2.5">
                <span className="text-caption-01 text-text-helper">{u.label} · </span>
                <span className="text-caption-02 text-text-secondary">{u.value}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <style>{`
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

function ChatScreen() {
  const { skills, signals, jumpToSkill, setSkills, setDetails, panelScrollRef, refreshSuggestions } = useLearning();
  const toast = useToast();

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
  const selectedModelName = currentModel || (modelMode === 'local' ? '로컬 모델 미선택' : '모델 미선택');
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
      toast.notify({ message: '폴더 선택은 RICE 데스크톱 앱에서 사용할 수 있어요.', variant: 'info' });
      return;
    }
    setWorkspaceSaving(true);
    void picker(workspacePath || undefined)
      .then(async (path) => {
        if (!path) return;
        const res = await api.updateWorkspaceLocation(path);
        if (!res.success || !res.data?.path) {
          toast.notify({ message: `저장 위치를 바꾸지 못했어요 · ${res.error ?? 'workspace'}`, variant: 'error' });
          return;
        }
        setWorkspacePath(res.data.path);
        toast.notify({ message: '저장 위치를 변경했어요. 다음 메시지부터 적용됩니다.', variant: 'success', duration: 4500 });
      })
      .catch(() => toast.notify({ message: '폴더 선택창을 열 수 없어요.', variant: 'error' }))
      .finally(() => setWorkspaceSaving(false));
  };

  // When connected, restore persisted settings (model/provider/endpoint) from
  // config.yaml via GET /settings, then load the session list.
  useEffect(() => {
    if (!connected) return;
    let cancelled = false;

    // 1) Restore model settings saved by PUT /settings, then health-check when local
    void api.getSettings().then(async (res) => {
      if (cancelled || !res.success) return;
      const data = res.data as {
        provider?: { kind?: string; model?: string; base_url?: string; temperature?: number; configured?: boolean; show_thinking?: boolean };
        registered_models?: RegisteredModel[];
      };
      const prov = data?.provider;
      // SaaS 등록 모델(키 포함)을 state.db에서 복원
      const registered = data?.registered_models ?? [];
      if (prov) {
        const restoredMode: 'saas' | 'local' = prov.kind === 'ollama' ? 'local' : 'saas';
        setModelMode(restoredMode);
        if (prov.model) setCurrentModel(prov.model);
        setModelSettings((prev) => ({
          ...prev,
          registeredModels: registered,
          ...(prov.base_url ? { endpoint: prov.base_url } : {}),
          ...(prov.temperature !== undefined ? { temperature: prov.temperature } : {}),
          // thinking 스트림 노출은 백엔드 config가 단일 권위 — 저장된 값으로 복원
          // (없으면 이전 localStorage 시드 유지).
          ...(prov.show_thinking !== undefined ? { showThinking: prov.show_thinking } : {}),
        }));
        if (prov.show_thinking !== undefined) {
          try {
            window.localStorage.setItem('rice.chat.showThinking', String(prov.show_thinking));
          } catch {
            // 메모리 상태는 이번 앱 세션 동안 계속 적용된다.
          }
        }

        // Local 모드일 때만: 설정된 모델이 실제 Ollama에 남아 있는지 확인
        if (prov.kind === 'ollama' && prov.model) {
          const hc = await api.checkOllamaModelHealth();
          if (cancelled) return;
          if (!hc.success) {
            toast.notify({ message: 'Ollama에 연결할 수 없어요. 엔드포인트를 확인하세요.', variant: 'error' });
          } else if (hc.data && !hc.data.exists) {
            toast.notify({
              message: `설정된 모델 "${prov.model}"이 Ollama에 없어요. 모델 설정을 열어 다시 선택하세요.`,
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
      if (list.length > 0) {
        // (Re)connect recovery: open the most recent session, then re-paint any
        // approval still parked on the server — covers an app reload / runtime
        // reconnect that happened while a turn was awaiting an answer (§5.1).
        void openLiveSession(list[0].id).then(() => {
          if (!cancelled) void recoverPendingApprovals(list[0].id);
        });
      } else newChat();
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

  // chat "스킬로 저장" → actually register the skill in the learning store
  // (so it appears in the learning panel and the My Skills screen immediately).
  const saveSkillFromChat = (m: Extract<ThreadMessage, { kind: 'skill' }>) => {
    setSkills((arr) => {
      // already exists → count this as a reuse/confirmation, re-enable it
      if (arr.some((s) => s.id === m.skillId)) {
        return arr.map((s) => (s.id === m.skillId ? { ...s, enabled: true, uses: s.uses + 1, trend: '+1 방금' } : s));
      }
      const sv = m.save;
      return [
        ...arr,
        { id: m.skillId, name: m.skill, ver: m.ver, uses: 0, trend: '새로 생성', enabled: true, command: sv?.command },
      ];
    });
    // build a detail record for a newly-created skill from the save payload
    if (m.save) {
      const sv = m.save;
      setDetails((d) =>
        d[m.skillId]
          ? d
          : {
              ...d,
              [m.skillId]: {
                summary: sv.summary,
                createdFrom: '대화에서 저장',
                lastUsed: '방금',
                shared: false,
                rules: (sv.rules ?? []).map((t, i) => ({ id: `csr${Date.now()}-${i}`, text: t, source: '대화에서 학습', on: true })),
                versions: [
                  {
                    ver: m.ver,
                    when: '방금',
                    note: '대화에서 저장',
                    current: true,
                    rules: (sv.rules ?? []).map((t) => ({ text: t, added: true })),
                  },
                ],
                usage: [{ kind: '대화', name: activeSession?.title ?? '대화에서 생성', icon: 'chat' }],
                permissions: sv.permissions ?? [],
                steps: sv.steps,
              },
            },
      );
    }
    jumpToSkill(m.skillId);
  };

  // approve a skill-improvement (patch) from chat → actually apply it to the
  // learning store: version++, keep the old version in history, reflect the
  // body change (steps/permissions). Mirrors LEARNING_LOOP 5.2.4 so that "쓸수록
  // 개선됨"이 My Skills에서 눈으로 확인된다 (정성적 증거). The real runtime path is
  // POST /skills/{id}/patch with approval_id (CONTRACTS 1.2) — see api.respondSkillPatch.
  const handleApprovePatch = (m: Extract<ThreadMessage, { kind: 'patch' }>, editedBody?: string) => {
    setSkills((arr) =>
      arr.map((s) =>
        s.id === m.skillId ? { ...s, ver: m.toVer, patches: (s.patches ?? 0) + 1, trend: '개선됨 방금', enabled: true } : s,
      ),
    );
    const addedRules = (m.newRules ?? []).filter((r) => r.added);
    setDetails((d) => {
      const cur = d[m.skillId];
      if (!cur) return d;
      const note = `${m.changelog}${editedBody ? ' · 편집 반영' : ''}`;
      return {
        ...d,
        [m.skillId]: {
          ...cur,
          lastUsed: '방금',
          rules: [
            ...cur.rules,
            ...addedRules.map((r, i) => ({ id: `pr${Date.now()}-${i}`, text: r.text, source: '개선(patch)에서 추가', on: true })),
          ],
          versions: [
            { ver: m.toVer, when: '방금', note, current: true, rules: m.newRules ?? [] },
            ...cur.versions.map((v) => ({ ...v, current: false })),
          ],
          // reflect the body change (data source moved) in procedure + permissions
          steps: cur.steps?.map((st) =>
            st.tool === 'db2.query'
              ? { ...st, tool: 'wxd.query', label: 'watsonx.data 매출 조회', desc: 'watsonx.data.sales 에서 기간별 매출 추출' }
              : st,
          ),
          permissions: cur.permissions.map((p) => (p.name === 'db2-prod.sales' ? { ...p, name: 'watsonx.data.sales' } : p)),
        },
      };
    });
    // contract response (parking match by approval_id) — no-op until runtime is up.
    // 만료된 제안(파킹 타임아웃)은 서버가 이미 제안함으로 옮겼다(Self-Improvement v3)
    // — 409가 오면 카드 대신 제안함에서 결정하라고 안내한다.
    void api.respondSkillPatch(m.skillId, { approval_id: m.approvalId, decision: 'approve', edited_body: editedBody }).then((res) => {
      if (!res.success && connected) {
        toast.notify({ message: '⏳ 카드가 만료되어 제안함으로 이동했어요 · 학습 패널에서 결정할 수 있습니다', duration: 5000 });
        void refreshSuggestions();
      }
    });
    jumpToSkill(m.skillId);
  };

  // 명시적 거부 → 서버 파킹 즉시 해소 + ledger에 'rejected' 기록(반복 거부 쿨다운의
  // 원천, Self-Improvement v3). 이전엔 거부가 로컬 카드 상태만 바꿔 서버는 타임아웃까지
  // 기다렸다 — v3에선 타임아웃이 제안함으로 이동하므로, 명시적 거부를 서버에 알리지
  // 않으면 사용자가 거부한 제안이 제안함에 다시 나타나는 모순이 생긴다.
  const handleRejectPatch = (m: Extract<ThreadMessage, { kind: 'patch' }>) => {
    void api.respondSkillPatch(m.skillId, { approval_id: m.approvalId, decision: 'reject' });
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
          copy[copy.length - 1] = { ...last, streaming: false, duration: `${secs}초` };
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
    if (!a.active) {
      a.active = true;
      a.sessionId = sessionId;
      a.target = '';
      a.shown = 0;
      a.complete = false;
      a.lastFeedAt = Date.now();
      updateThreadFor(sessionId, (p) => [...p, { kind: 'agent', text: '', streaming: true, sentAt: Date.now() }]);
    }
    if (typeof d.content === 'string') a.target = d.content;
    else a.target += d.delta ?? '';
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
        message: `🔒 외부 콘텐츠 유래 메모리 격리됨 · 학습 패널에서 승인/삭제: ${d.content.slice(0, 48)}`,
        duration: 5000,
      });
      return;
    }
    const label = d.memory_type === 'fact' ? '사실 기억됨' : '선호 기억됨';
    toast.notify({ message: `💾 ${label}: ${d.content.slice(0, 60)}`, duration: 4000 });
  };

  const handleSkillPatchProposed = (d: SkillPatchProposedData, sessionId = activeSessionIdRef.current) => {
    // HITL gate — diff 카드 렌더링. 스트림은 계속 흐르지만 사용자 결정은
    // POST /skills/{id}/patch 로 별도 전송 (pendingToThread 로직 재사용).
    const card = pendingToThread({ approval_id: d.approval_id, kind: 'skill_patch', payload: d });
    if (card) updateThreadFor(sessionId, (p) => [...p, card]);
  };

  const handleSkillPatched = (d: { skill_id: string; version: number }) => {
    // 이전에 승인된 patch(또는 신규 생성 — from_version=0, Phase A.1)가 서버에서
    // 적용 완료됨. 로컬 skills 상태 동기화 — id가 없으면(=방금 생성된 스킬,
    // skill_created 이벤트는 더 이상 없음) map이 조용히 no-op하지 않도록 삽입한다.
    let wasNew = false;
    setSkills((arr) => {
      if (arr.some((s) => s.id === d.skill_id)) {
        return arr.map((s) =>
          s.id === d.skill_id ? { ...s, ver: `v${d.version}`, patches: (s.patches ?? 0) + 1, trend: '개선됨' } : s,
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
          trend: '방금 생성',
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
                createdFrom: '대화에서 자동 생성',
                lastUsed: '방금',
                shared: false,
                rules: [],
                versions: [{ ver: `v${d.version}`, when: '방금', note: '자동 생성', current: true }],
                usage: [{ kind: '대화', name: activeSession?.title ?? '대화', icon: 'chat' as const }],
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
    toast.notify({ message: '✨ 대화를 스킬로 정리하는 중…', duration: 3000 });
    let proposed = false;
    sseClient.learnFromConversation(sid, instruction, {
      onEvent: (e: SSEEvent) => {
        if (e.type === 'memory_written') {
          handleMemoryWritten(e.data as MemoryWrittenEventData);
        } else if (e.type === 'skill_patch_proposed') {
          proposed = true;
          handleSkillPatchProposed(e.data as SkillPatchProposedData);
        } else if (e.type === 'skill_patched') {
          handleSkillPatched(e.data as { skill_id: string; version: number });
        } else if (e.type === 'done' && !proposed) {
          toast.notify({ message: '이 대화에서 저장할 만한 절차를 찾지 못했어요.', duration: 3000 });
        } else if (e.type === 'error') {
          const d = e.data as { message?: string };
          toast.notify({ message: `⚠️ 학습 실패: ${d.message ?? '알 수 없는 오류'}`, duration: 4000 });
        }
      },
      onError: (msg) => toast.notify({ message: `⚠️ 연결 오류: ${msg}`, duration: 4000 }),
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
            scope: { name: d.tool_name, mode: '실행' },
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
                error: d.ok ? undefined : d.error ?? '실패',
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
        pushAgentText(sessionId, `⚠️ 오류: ${d.message ?? '실행 실패'}`);
        finishLiveTurn(sessionId, requestId);
      }
    },
    onError: (msg: string) => {
      if (turnRequestRef.current !== requestId) return;
      sealAnswer(sessionId);
      finalizeThinking(sessionId);
      pushAgentText(sessionId, `⚠️ 연결 오류: ${msg}`);
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
        const provisional = q.replace(/\s+/g, ' ').trim().slice(0, 40) || '제목 없는 대화';
        setSessions((prev) => [{ id, title: provisional, timestamp: new Date(), messageCount: 0 }, ...prev]);
      } else {
        finalizeThinking(originSessionId);
        pushAgentText(originSessionId, `⚠️ 런타임 세션 생성 실패: ${res.error ?? '알 수 없는 오류'}`);
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
          duration: '9초',
          steps: [
            { label: '요청 해석', detail: q.length > 40 ? q.slice(0, 40) + '…' : q },
            { label: '실행 계획 수립', detail: '필요한 단계로 절차 구성', tool: 'plan.build' },
            { label: '도구·스킬 선택', detail: '설치된 도구·학습한 스킬에서 매칭', tool: 'tool.select' },
            { label: '실행', detail: '선택한 도구로 작업 수행', tool: 'tool.execute' },
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
          label: '요청 처리',
          status: 'running',
          steps: 0,
          time: '',
          open: true,
          calls: [
            { tool: 'plan.build', label: '단계 계획 수립', status: 'done', time: '0.4s', params: { goal: q.slice(0, 40) }, result: '4단계 계획' },
            { tool: 'tool.select', label: '필요한 도구 선택', status: 'done', time: '0.3s', params: { from: 'installed' }, result: '2개 선택' },
            { tool: 'tool.execute', label: '실행 중…', status: 'running', time: '', params: {}, result: '' },
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
                time: '2.0초',
                open: false,
                calls: m.calls.map((c) => ({
                  ...c,
                  status: 'done' as const,
                  time: c.time || '0.9s',
                  label: c.label.replace('…', ' 완료'),
                  result: c.result || '완료',
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
          text: '처리했어요. 학습한 선호(한국어·간결한 불릿·단위 포함)를 적용했습니다.',
          usedSkill: { id: 's1', label: '분기 매출 요약', ver: 'v3' },
        },
      ]);
    }, 1700);

    // learned
    setTimeout(() => {
      setThread((p) => [
        ...p,
        {
          kind: 'skill',
          title: '새 스킬을 학습했어요',
          skill: '요청 처리 루틴',
          skillId: 's1',
          ver: 'v1',
          desc: '이 작업의 절차를 스킬로 저장했어요. 다음엔 더 적은 단계로 처리합니다.',
          meta: '경험에서 생성 · 세션을 넘어 유지',
        },
      ]);
      setSkills((arr) => arr.map((s) => (s.id === 's1' ? { ...s, uses: s.uses + 1, trend: '+1 방금' } : s)));
    }, 2300);
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
  const actionsCopyAt: Record<number, string> = {};
  // index → epoch ms of the agent message that owns the action bar
  const actionsTimeAt: Record<number, number> = {};
  thread.forEach((m, i) => {
    if (m.kind !== 'agent') return;
    // skip intermediate narration: another agent message follows in this same turn
    for (let j = i + 1; j < thread.length; j++) {
      if (thread[j].kind === 'user') break;
      if (thread[j].kind === 'agent') return;
    }
    let end = i;
    while (end + 1 < thread.length && (thread[end + 1].kind === 'skill' || thread[end + 1].kind === 'memory' || thread[end + 1].kind === 'patch' || thread[end + 1].kind === 'approval')) end++;
    actionsCopyAt[end] = m.text;
    if (m.sentAt) actionsTimeAt[end] = m.sentAt;
  });

  // group consecutive non-user messages into one assistant turn; each user
  // message is its own turn. The turn then renders one "RICE" identity
  // over all its blocks (thinking / tool-run / answer / skill / memory).
  const turns: { key: string; items: { m: ThreadMessage; index: number }[] }[] = [];
  thread.forEach((m, i) => {
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
            <span className="text-body-short-01 font-semibold text-text-primary truncate">
              {isEmpty ? '새 대화' : activeSession?.title || '대화'}
            </span>

          </div>

          {/* Thread */}
          <div className="relative flex-1 min-h-0">
            <div ref={scrollRef} onScroll={handleThreadScroll} className="h-full overflow-y-auto">
              <div className="w-full max-w-[920px] mx-auto px-5 py-4">
                {isEmpty ? (
                  <ChatStarter skills={skills} signals={signals} onPick={(text) => send(text)} />
                ) : (
                  turns.map((t) => (
                    <ChatTurn
                      key={t.key}
                      items={t.items}
                      onCorrect={handleCorrect}
                      onJumpSkill={jumpToSkill}
                      onRegenerate={regenerate}
                      onSaveSkill={saveSkillFromChat}
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
                aria-label={hasNewBelow ? '새 메시지로 이동' : '맨 아래로 이동'}
                title={hasNewBelow ? '새 메시지로 이동' : '맨 아래로 이동'}
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
                        title="명령어 제거 (Backspace)"
                      >
                        <Icon name="close" size={11} />
                      </button>
                    </span>
                    <span className="text-caption-01 text-text-helper">스킬 명령 · 추가 지시사항은 선택입니다</span>
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
                  placeholder={command ? '추가 지시사항 입력 (선택)' : 'RICE에게 메시지…'}
                  rows={1}
                  className="w-full resize-none border-none outline-none bg-transparent text-text-primary text-body-short-02 leading-5 px-4 pt-3.5 pb-1 min-h-[56px] max-h-[200px]"
                />
                <div className="flex items-center gap-1.5 px-2.5 pb-2.5">
                  <div className="relative">
                    <Tooltip label="스킬·도구 추가">
                      <button
                        type="button"
                        onClick={() => {
                          setApprovalMenuOpen(false);
                          setSkillsOpen((o) => !o);
                        }}
                        aria-label="스킬·도구 추가"
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
                      <Tooltip label="이 대화를 스킬로 저장">
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
                            <span className="text-body-short-01 font-semibold text-text-primary">이 대화를 스킬로 저장</span>
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
                            placeholder="무엇을 중심으로 정리할까요? (비워두면 대화 전체를 자동 증류)"
                            rows={2}
                            autoFocus
                            className="w-full bg-field-01 border border-border-subtle-01 rounded-md px-2.5 py-2 text-body-short-01 text-text-primary placeholder:text-text-placeholder resize-none focus:outline-none focus:border-interactive"
                          />
                          <div className="flex items-center justify-end gap-1.5 mt-2">
                            <button
                              onClick={() => setLearnOpen(false)}
                              className="h-7 px-2.5 rounded-md text-caption-01 text-text-secondary hover:bg-layer-hover-01 transition-colors"
                            >
                              취소
                            </button>
                            <button
                              onClick={() => {
                                learnFromConversation(learnInstruction.trim());
                                setLearnInstruction('');
                              }}
                              className="inline-flex items-center gap-1 h-7 px-2.5 rounded-md text-caption-01 font-semibold bg-interactive text-white hover:bg-interactive-hover transition-colors"
                            >
                              <Icon name="sparkle" size={12} /> 스킬로 정리
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                  <div className="flex-1" />
                  <Tooltip label={busy ? '생성 중지' : '전송'}>
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
                <div className="mt-3 flex items-center px-1">
                  <Tooltip label={`생성 파일 저장 위치 변경 · ${workspacePath}`}>
                    <button
                      type="button"
                      onClick={chooseWorkspaceFolder}
                      disabled={workspaceSaving}
                      className="flex min-w-0 max-w-full items-center gap-2 rounded-md py-1 text-caption-01 text-text-helper transition-colors hover:text-text-primary"
                      aria-label={`생성 파일 저장 위치 변경: ${workspacePath}`}
                    >
                      <Icon name="folder" size={18} className="flex-shrink-0 text-icon-secondary" />
                      <span className="flex-shrink-0 text-text-secondary">저장 위치</span>
                      <span className="truncate font-mono">{workspacePath}</span>
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
              <LearningPanel width={learningPanel.width} onOpenConversation={openConversation} />
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
          <button
            type="button"
            onClick={() => setSessionSidebarOpen((o) => !o)}
            className="electron-no-drag flex h-6 w-6 items-center justify-center rounded-md text-icon-secondary hover:bg-layer-hover-01 hover:text-icon-primary transition-colors"
            aria-label={sessionSidebarOpen ? '대화 목록 접기' : '대화 목록 펼치기'}
            title={sessionSidebarOpen ? '대화 목록 접기' : '대화 목록 펼치기'}
          >
            <Icon name="sidebar" size={17} />
          </button> 
          <Tooltip label="빠른 실행 (⌘K)">
            <button
              type="button"
              onClick={openCommandPalette}
              className="electron-no-drag flex h-6 w-6 items-center justify-center rounded-md text-icon-secondary transition-colors hover:bg-layer-hover-01 hover:text-icon-primary"
              aria-label="빠른 실행 열기"
            >
              <Icon name="search" size={17} />
            </button>
          </Tooltip>
        </div>

        {/* 패널의 기능(renew)이 아니라 레이아웃 열기/닫기임을 모양으로 명시한다. */}
        <div className="absolute top-0 right-0 z-30 flex h-11 items-center pr-2 electron-drag">
          <Tooltip label={learningPanelOpen ? '학습 루프 닫기' : '학습 루프 열기'}>
            <button
              type="button"
              onClick={() => setLearningPanelOpen((open) => !open)}
              data-tour="learning-loop"
              className="electron-no-drag flex h-[28px] w-[28px] items-center justify-center rounded-md text-icon-secondary transition-colors hover:bg-layer-hover-01 hover:text-icon-primary"
              aria-label={learningPanelOpen ? '학습 루프 닫기' : '학습 루프 열기'}
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
        title="대화를 삭제할까요?"
        description={
          <>
            <span className="font-medium text-text-primary">{confirmDelete?.title}</span> 대화와 모든 메시지가 영구
            삭제됩니다. 되돌릴 수 없어요.
          </>
        }
        confirmLabel="삭제"
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
