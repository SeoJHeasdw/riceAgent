import { useEffect, useRef, useState } from 'react';
import { SessionSidebar, type Session } from '../components/carbon/SessionSidebar';
import { ModelSettingsPanel } from '../components/carbon/ModelSettingsPanel';
import { LearningPanel } from '../components/carbon/LearningPanel';
import { ChatTurn } from '../components/carbon/ChatMessage';
import { useResizable } from '../hooks/useResizable';
import { useLearning } from '../lib/learning/LearningContext';
import { CONVERSATIONS, THREADS, placeholderThread, type ThreadMessage } from '../lib/learningData';
import { mockModels, mockModelSettings } from '../lib/mockData';
import { api } from '../lib/api';
import type { PendingApproval, ToolApprovalRequiredData, SkillPatchProposedData } from '../lib/types';
import { Icon, ResizeHandle } from '../components/ui';

/**
 * ChatScreen - Claude-Desktop-style chat with the learning-loop surface:
 * multi-step tool-call cards, the memory-written indicator, clickable
 * used-skill badges, a per-answer action bar, and an interactive learning
 * panel on the right.
 */

// Korean demo sessions derived from the conversation list
const groupOffset: Record<string, number> = {
  오늘: 2 * 3600000,
  '지난 7일': 3 * 86400000,
  이전: 40 * 86400000,
};
const demoSessions: Session[] = CONVERSATIONS.map((c, i) => ({
  id: c.id,
  title: c.title,
  timestamp: new Date(Date.now() - (groupOffset[c.group] ?? 86400000) - i * 600000),
  messageCount: 6 + ((i * 5) % 14),
}));

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
  return {
    kind: 'patch',
    approvalId: p.approval_id,
    skillId: d.skill_id,
    skillName: d.skill_id,
    fromVer: d.from_version ? `v${d.from_version}` : '현재',
    toVer: d.to_version ? `v${d.to_version}` : '개선',
    changelog: d.changelog,
    trigger: d.trigger,
    triggerLabel: '복구됨',
    diff: d.diff.lines.map((l) => ({ t: l.type === 'context' ? 'ctx' : l.type, text: l.text })),
    proposedBody: d.diff.lines
      .filter((l) => l.type !== 'remove')
      .map((l) => l.text)
      .join('\n'),
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
  /** Called when the user presses Escape — lets the parent return focus to the composer */
  onEscape?: () => void;
  /** Pre-fill the search box (used when triggered by inline "/" typing) */
  initialQuery?: string;
  /** When true the popover is triggered inline — suppress the outside-click close
   *  on the textarea itself so the user can keep typing to filter */
  inlineMode?: boolean;
}> = ({ onInsert, onClose, onManage, onEscape, initialQuery = '', inlineMode = false }) => {
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

  // A clickable slash-command row
  const CmdRow: React.FC<{
    icon: Parameters<typeof Icon>[0]['name'];
    command: string;
    sub: string;
    indent?: boolean;
  }> = ({ icon, command, sub, indent }) => (
    <button
      onClick={() => insert(command)}
      className={`group/row flex items-center gap-2.5 w-full py-1.5 pr-2.5 text-left hover:bg-layer-hover-02 transition-colors ${
        indent ? 'pl-9' : 'pl-3'
      }`}
    >
      <Icon name={icon} size={15} className="text-icon-secondary opacity-80 flex-shrink-0" />
      <div className="flex-1 min-w-0">
        <div className="font-mono text-body-short-01 text-text-primary truncate">
          <span className="text-text-helper">/</span>
          {command}
        </div>
        <div className="text-caption-01 text-text-helper truncate">{sub}</div>
      </div>
      <span className="text-[10px] font-mono text-text-helper opacity-0 group-hover/row:opacity-100 transition-opacity flex-shrink-0">
        ↵ 삽입
      </span>
    </button>
  );

  const GroupLabel: React.FC<{ children: React.ReactNode; count: number }> = ({ children, count }) => (
    <div className="flex items-center gap-1.5 px-3 pt-2.5 pb-1">
      <span className="text-[10px] font-semibold uppercase tracking-wider text-text-helper">{children}</span>
      <span className="text-[10px] font-mono text-text-helper">{count}</span>
    </div>
  );

  return (
    <div
      ref={ref}
      className={`absolute z-30 w-[320px] bg-layer-02 border border-border-subtle-01 rounded-xl shadow-elevation-3 overflow-hidden flex flex-col ${
        inlineMode ? 'bottom-full mb-2 left-0' : 'bottom-11 left-0'
      }`}
    >
      {/* Header + search */}
      <div className="px-3 pt-3 pb-2 flex-shrink-0">
        <div className="flex items-center gap-1.5 mb-2">
          <Icon name="skill" size={14} className="text-icon-primary" />
          <span className="text-body-short-01 font-semibold text-text-primary">이 대화에서 쓸 수 있는 것</span>
          <span className="ml-auto text-[10px] font-mono text-text-helper">
            <span className="text-text-secondary">/</span> 로 호출
          </span>
        </div>
        <div className="flex items-center gap-1.5 px-2 h-7 bg-layer-01 border border-border-subtle-01 rounded-md focus-within:border-focus transition-colors">
          <Icon name="search" size={13} className="text-icon-secondary flex-shrink-0" />
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); onClose(); onEscape?.(); } }}
            placeholder="스킬·도구·전문가 검색"
            className="flex-1 min-w-0 bg-transparent border-none outline-none text-caption-01 text-text-primary placeholder:text-text-placeholder"
          />
        </div>
      </div>

      {/* Body */}
      <div className="max-h-[332px] overflow-y-auto pb-1">
        {empty && <div className="px-3 py-6 text-center text-caption-01 text-text-helper">검색 결과가 없어요</div>}

        {skillItems.length > 0 && (
          <div>
            <GroupLabel count={skillItems.length}>스킬</GroupLabel>
            {skillItems.map((s) => (
              <CmdRow key={s.id} icon="skill" command={s.command!} sub={`${s.ver} · ${s.uses}회 사용`} />
            ))}
          </div>
        )}

        {toolItems.length > 0 && (
          <div>
            <GroupLabel count={toolItems.length}>도구</GroupLabel>
            {toolItems.map((t) => (
              <CmdRow key={t.id} icon={t.icon} command={t.command ?? t.id} sub={t.meta} />
            ))}
          </div>
        )}

        {pluginItems.length > 0 && (
          <div>
            <GroupLabel count={pluginItems.length}>플러그인 · 전문가</GroupLabel>
            {pluginItems.map(({ plugin, provides }) => {
              const open = query ? true : expanded.has(plugin.id);
              return (
                <div key={plugin.id}>
                  <button
                    onClick={() => toggle(plugin.id)}
                    className="flex items-center gap-2.5 w-full px-3 py-1.5 text-left hover:bg-layer-hover-02 transition-colors"
                  >
                    <Icon name={plugin.icon} size={15} className="text-icon-secondary opacity-80 flex-shrink-0" />
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
                    provides.map((pr) => (
                      <CmdRow key={pr.command} icon="skill" command={pr.command} sub={pr.desc} indent />
                    ))}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Footer */}
      <button
        onClick={() => {
          onClose();
          onManage();
        }}
        className="flex items-center gap-1.5 w-full px-3 py-2.5 border-t border-border-subtle-01 text-caption-01 text-link-primary hover:bg-layer-hover-02 transition-colors flex-shrink-0"
      >
        <Icon name="settings" size={14} /> 학습 패널에서 관리
      </button>
    </div>
  );
};

function ChatScreen() {
  const { jumpToSkill, setSignals, setSkills, setDetails, panelScrollRef } = useLearning();

  const [thread, setThread] = useState<ThreadMessage[]>(THREADS.c1);
  const [sessions] = useState<Session[]>(demoSessions);
  const [activeSessionId, setActiveSessionId] = useState('c1');
  // which session's thread is currently mounted (gates the scroll-to-message effect)
  const [loadedSession, setLoadedSession] = useState('c1');
  const [highlightMsg, setHighlightMsg] = useState<number | null>(null);
  const [pendingScroll, setPendingScroll] = useState<{ session: string; index: number } | null>(null);
  const [draft, setDraft] = useState('');
  // A leading slash-command is lifted OUT of the textarea into its own chip.
  // `command` holds the command token (e.g. "quarterly_sales_summary") or null;
  // `draft` then holds only the trailing arguments/instructions.
  const [command, setCommand] = useState<string | null>(null);
  const [isEmpty, setIsEmpty] = useState(false);
  const [sessionSidebarOpen, setSessionSidebarOpen] = useState(true);
  const [learningPanelOpen, setLearningPanelOpen] = useState(true);
  const [settingsPanelOpen, setSettingsPanelOpen] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [modelMode, setModelMode] = useState<'saas' | 'local'>('saas');
  const [currentModel, setCurrentModel] = useState('claude-sonnet-4-5');
  const [modelSettings, setModelSettings] = useState(mockModelSettings);

  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const sessionPanel = useResizable({ initial: 264, min: 200, max: 420, storageKey: 'rice.panel.sessions' });
  const learningPanel = useResizable({ initial: 320, min: 240, max: 480, storageKey: 'rice.panel.learning', invert: true });

  const availableModels = mockModels[modelMode];
  const selectedModel = availableModels.find((m) => m.id === currentModel) || availableModels[0];
  const activeSession = sessions.find((s) => s.id === activeSessionId);

  // load the scripted thread for the active session when switched from the sidebar
  useEffect(() => {
    setThread(THREADS[activeSessionId] ?? placeholderThread(activeSession?.title ?? '이 작업'));
    setLoadedSession(activeSessionId);
    setIsEmpty(false);
  }, [activeSessionId, activeSession?.title]);

  // SSE reconnect recovery: re-fetch approvals still parked on the server and
  // re-render their cards (CONTRACTS 1.2). No-op offline / in the scripted demo.
  useEffect(() => {
    let cancelled = false;
    void api.getPendingApprovals(activeSessionId).then((res) => {
      if (cancelled || !res.success || !res.data?.length) return;
      setThread((prev) => {
        const have = new Set(
          prev.flatMap((mm) => (mm.kind === 'approval' || mm.kind === 'patch' ? [mm.approvalId] : [])),
        );
        const recovered = res
          .data!.filter((p) => !have.has(p.approval_id))
          .map(pendingToThread)
          .filter((mm): mm is ThreadMessage => mm !== null);
        return recovered.length ? [...prev, ...recovered] : prev;
      });
    });
    return () => {
      cancelled = true;
    };
  }, [activeSessionId]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [thread]);

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

  // user corrected an answer → write a memory, add a user-model signal, AND
  // persist the correction as a real rule on the target skill (visible in My Skills).
  const handleCorrect = (text: string) => {
    const ruleText = text.length > 60 ? text.slice(0, 60) + '…' : text;
    setThread((p) => [
      ...p,
      { kind: 'user', text },
      { kind: 'agent', text: '알려주셔서 고마워요. 바로 반영하고, 다음에도 그렇게 하도록 기억해둘게요.' },
      {
        kind: 'memory',
        text: '기억했습니다',
        facts: [text],
        target: { kind: 'skill', id: 's1', label: '분기 매출 요약 v3' },
      },
    ]);
    setSignals((arr) => [
      ...arr,
      {
        id: 'u' + Date.now(),
        label: '교정',
        value: text.length > 28 ? text.slice(0, 28) + '…' : text,
        conf: 0.95,
        source: '방금 가르침',
      },
    ]);
    // 교정 → s1 스킬의 학습한 규칙으로 누적 (My Skills 상세에서 바로 확인)
    setDetails((d) =>
      d.s1
        ? { ...d, s1: { ...d.s1, rules: [...d.s1.rules, { id: 'cr' + Date.now(), text: ruleText, source: '교정에서 학습', on: true }] } }
        : d,
    );
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
    // contract response (parking match by approval_id) — no-op until runtime is up
    void api.respondSkillPatch(m.skillId, { approval_id: m.approvalId, decision: 'approve', edited_body: editedBody });
    jumpToSkill(m.skillId);
  };

  // tool-execution approval (HITL) → POST the decision back to the runtime,
  // matched server-side by approval_id (CONTRACTS 1.2). Card state is local; this
  // is the real wire (no-op offline).
  const handleToolApproval = (m: Extract<ThreadMessage, { kind: 'approval' }>, decision: 'approve' | 'reject') => {
    void api.respondToolApproval(activeSessionId, { approval_id: m.approvalId, decision });
  };

  const regenerate = () => {
    setThread((p) => [
      ...p,
      {
        kind: 'agent',
        text: '다시 생성했어요. 같은 데이터로 표현을 조금 바꿨습니다.',
        bullets: ['총 매출 $9.3M (전주 +6.2%)', 'APAC $3.8M — 주간 1위', 'watsonx.ai 라인 61% 기여'],
      },
    ]);
  };

  const send = () => {
    const body = draft.trim();
    if (!command && !body) return;
    const q = command ? `/${command}${body ? ' ' + body : ''}` : body;
    setDraft('');
    setCommand(null);
    setIsEmpty(false);
    const runId = 'r' + Date.now();
    setThread((p) => [...p, { kind: 'user', text: q }]);

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
    setIsEmpty(true);
    setThread([]);
  };

  const manageFromPopover = () => {
    panelScrollRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
  };

  // open the conversation a proposal's evidence came from, then scroll to + highlight it
  const openConversation = (conversationId: string, messageIndex?: number) => {
    setActiveSessionId(conversationId);
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
    <>
      <div className="flex flex-1 min-w-0 overflow-hidden">
        {/* Session Sidebar */}
        <SessionSidebar
          sessions={sessions}
          activeSessionId={activeSessionId}
          onSelectSession={setActiveSessionId}
          onNewSession={newChat}
          onDeleteSession={() => {}}
          isOpen={sessionSidebarOpen}
          onToggle={() => setSessionSidebarOpen(!sessionSidebarOpen)}
          width={sessionPanel.width}
          modelName={selectedModel.name}
          modelMode={modelMode}
          onOpenModelSettings={() => setSettingsPanelOpen(true)}
        />
        {sessionSidebarOpen && (
          <ResizeHandle
            onPointerDown={sessionPanel.startResize}
            onDoubleClick={sessionPanel.resetWidth}
            isResizing={sessionPanel.isResizing}
            aria-label="Resize sessions panel"
          />
        )}

        {/* Main */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden bg-background">
          {/* Header */}
          <div className="h-[52px] flex-shrink-0 flex items-center gap-3 px-4 border-b border-border-subtle-01">
            <span className="text-body-short-01 font-semibold text-text-primary truncate">
              {isEmpty ? '새 대화' : activeSession?.title || '분기 매출 리포트 자동화'}
            </span>

            <div className="ml-auto flex items-center gap-1">
              <button
                onClick={() => setLearningPanelOpen((o) => !o)}
                className={`flex items-center gap-1.5 px-2 py-0.5 border border-border-subtle-01 hover:bg-layer-hover-01 transition-colors text-text-secondary text-caption-01 rounded-md ${
                  learningPanelOpen ? 'bg-layer-selected-01' : ''
                }`}
                title={learningPanelOpen ? '학습 루프 접기' : '학습 루프 펼치기'}
              >
                <Icon name="growth" size={14} />
                학습 루프
                <Icon name={learningPanelOpen ? 'side-panel-close' : 'side-panel-open'} size={14} />
              </button>
            </div>
          </div>

          {/* Thread */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto">
            <div className="w-full max-w-[920px] mx-auto px-5 py-4">
              {isEmpty ? (
                <div className="pt-20 text-center">
                  <div className="w-12 h-12 mx-auto mb-4 bg-blue-60 flex items-center justify-center rounded-xl">
                    <Icon name="model" size={24} className="text-white" />
                  </div>
                  <div className="text-heading-04 font-light text-text-primary mb-2">무엇을 도와드릴까요?</div>
                  <div className="text-body-short-02 text-text-helper">
                    쓸수록 당신의 방식에 맞춰 학습하고, 반복 작업은 스킬로 저장합니다.
                  </div>
                </div>
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
                    onRespondApproval={handleToolApproval}
                    highlightIndex={highlightMsg}
                    actionsCopyAt={actionsCopyAt}
                  />
                ))
              )}
            </div>
          </div>

          {/* Composer */}
          <div className="flex-shrink-0 px-5 pt-2 pb-5">
            <div className="w-full max-w-[920px] mx-auto relative">
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
                  initialQuery={inlineQuery}
                  inlineMode
                />
              )}
              <div className="bg-layer-01 border border-border-strong-01 rounded-xl shadow-elevation-1 transition-colors focus-within:border-focus">
                {/* Active slash-command chip — the command lives here, not in the textarea */}
                {command && (
                  <div className="flex items-center gap-2 px-3 pt-3 pb-1">
                    <span className="inline-flex items-center gap-1.5 pl-2 pr-1 py-1 bg-blue-10 border border-blue-30 rounded-lg text-caption-01 text-blue-70 select-none">
                      <Icon name="skill" size={12} className="text-blue-60" />
                      <span className="font-mono font-medium">/{command}</span>
                      <button
                        onClick={clearCommand}
                        className="ml-0.5 p-0.5 rounded-md text-blue-60 hover:bg-blue-30 hover:text-blue-70 transition-colors"
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
                    if (e.key === 'Enter' && !e.shiftKey) {
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
                  placeholder={command ? '추가 지시사항 입력 (선택) · Enter로 실행' : 'RICE에게 메시지…  (Enter로 전송)'}
                  className="w-full resize-none border-none outline-none bg-transparent text-text-primary text-body-short-02 leading-5 px-4 pt-3.5 pb-1 min-h-[56px] max-h-[200px]"
                />
                <div className="flex items-center gap-1.5 px-2 pb-2">
                  <button className="p-1.5 hover:bg-layer-hover-01 transition-colors rounded-md text-icon-primary" title="첨부">
                    <Icon name="attachment" size={16} />
                  </button>
                  <button className="p-1.5 hover:bg-layer-hover-01 transition-colors rounded-md text-icon-primary" title="음성">
                    <Icon name="microphone" size={16} />
                  </button>
                  <div className="relative">
                    <button
                      onClick={() => setSkillsOpen((o) => !o)}
                      className={`flex items-center gap-1.5 px-2 py-1 border border-border-subtle-01 hover:bg-layer-hover-01 transition-colors text-text-secondary text-caption-01 rounded-md ${
                        skillsOpen ? 'bg-layer-selected-01' : ''
                      }`}
                    >
                      <Icon name="skill" size={14} /> 스킬·도구
                      <Icon name={skillsOpen ? 'chevron-up' : 'chevron-down'} size={12} />
                    </button>
                    {/* Button-triggered popover — only when NOT in inline "/" mode */}
                    {skillsOpen && !isInlineSlash && (
                      <SkillsPopover onInsert={insertSkill} onClose={() => setSkillsOpen(false)} onEscape={() => composerRef.current?.focus()} onManage={manageFromPopover} />
                    )}
                  </div>
                  <div className="flex-1" />
                  <button
                    onClick={send}
                    disabled={!command && !draft.trim()}
                    className={`p-1.5 rounded-md transition-colors ${
                      command || draft.trim() ? 'bg-blue-60 hover:bg-blue-70 cursor-pointer' : 'bg-layer-02 cursor-not-allowed opacity-50'
                    }`}
                    title="전송"
                  >
                    <Icon name="send" size={16} className="text-white" />
                  </button>
                </div>
              </div>
              <div className="text-caption-01 text-text-helper text-center mt-2.5">
                {modelMode === 'local'
                  ? 'Ollama 로컬 모델로 실행 중 · 데이터가 기기를 벗어나지 않습니다'
                  : 'Claude(SaaS) 모델로 실행 중'}
              </div>
            </div>
          </div>
        </div>

        {/* Learning Panel */}
        {learningPanelOpen && (
          <>
            <ResizeHandle
              onPointerDown={learningPanel.startResize}
              onDoubleClick={learningPanel.resetWidth}
              isResizing={learningPanel.isResizing}
              aria-label="Resize learning loop panel"
            />
            <LearningPanel width={learningPanel.width} onOpenConversation={openConversation} />
          </>
        )}
      </div>

      {/* Model Settings Panel */}
      <ModelSettingsPanel
        isOpen={settingsPanelOpen}
        onClose={() => setSettingsPanelOpen(false)}
        currentModel={currentModel}
        availableModels={availableModels}
        onModelChange={setCurrentModel}
        settings={modelSettings}
        onSettingsChange={setModelSettings}
        modelMode={modelMode}
        onModelModeChange={(mode) => {
          setModelMode(mode);
          setCurrentModel(mockModels[mode][0].id);
        }}
      />
    </>
  );
}

export default ChatScreen;

// Made with Bob
