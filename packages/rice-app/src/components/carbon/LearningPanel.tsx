import React, { useEffect, useRef, useState } from 'react';
import { Icon, Tag, Toggle } from '../ui';
import type { IconName } from '../ui/Icon';
import { useLearning } from '../../lib/learning/LearningContext';
import {
  SESSION_DELTAS,
  type InstalledTool,
  type Proposal,
  type ProposalKind,
  type UserSignal,
} from '../../lib/learningData';

/**
 * LearningPanel - the right-side learning surface. Interactive: users can
 * toggle/remove installed tools, edit/remove user-model signals, and
 * enable/disable/delete learned skills. A skill highlighted from the chat
 * thread scrolls into view and pulses.
 */

export interface LearningPanelProps {
  isLearning?: boolean;
  /** Panel width in px (drag-resizable from parent) */
  width?: number;
  /** open the conversation (and scroll to the message) a proposal's evidence came from */
  onOpenConversation?: (conversationId: string, messageIndex?: number) => void;
}

// confidence meter
const Confidence: React.FC<{ v: number }> = ({ v }) => (
  <div className="w-11 h-1 bg-border-strong-01 flex-shrink-0 rounded-full overflow-hidden">
    <div className="h-full bg-blue-50" style={{ width: `${Math.round(v * 100)}%` }} />
  </div>
);

// small overflow menu
interface MenuItem {
  icon: IconName;
  label: string;
  danger?: boolean;
  onClick: () => void;
}

const RowMenu: React.FC<{ items: MenuItem[] }> = ({ items }) => {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, [open]);
  return (
    <div ref={ref} className="relative flex-shrink-0">
      <button
        onClick={() => setOpen((o) => !o)}
        title="옵션"
        aria-label="옵션"
        className={`w-7 h-7 flex items-center justify-center rounded-md transition-colors ${
          open ? 'bg-layer-hover-01' : 'hover:bg-layer-hover-01'
        }`}
      >
        <Icon name="overflow" size={16} className="text-icon-secondary" />
      </button>
      {open && (
        <div className="absolute top-8 right-0 z-30 min-w-[148px] bg-layer-02 border border-border-subtle-01 rounded-md shadow-elevation-2 overflow-hidden">
          {items.map((it, i) => (
            <button
              key={i}
              onClick={() => {
                setOpen(false);
                it.onClick();
              }}
              className={`flex items-center gap-2 w-full px-3 py-2 text-left text-body-short-01 hover:bg-layer-hover-02 transition-colors ${
                it.danger ? 'text-text-error' : 'text-text-primary'
              }`}
            >
              <Icon name={it.icon} size={16} className={it.danger ? '' : 'opacity-80'} />
              {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

// section wrapper
const PanelSection: React.FC<{
  icon: IconName;
  title: string;
  count?: string;
  children: React.ReactNode;
}> = ({ icon, title, count, children }) => (
  <div className="border-b border-border-subtle-01">
    <div className="flex items-center gap-2 px-4 pt-3.5 pb-2">
      <Icon name={icon} size={16} className="text-icon-primary" />
      <span className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">{title}</span>
      {count != null && <span className="ml-auto text-caption-01 text-text-helper font-mono">{count}</span>}
    </div>
    <div className="px-4 pb-3.5">{children}</div>
  </div>
);

// user-model signal row (editable / removable)
const SignalRow: React.FC<{ s: UserSignal; onEdit: (v: string) => void; onRemove: () => void }> = ({
  s,
  onEdit,
  onRemove,
}) => {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(s.value);

  if (editing) {
    return (
      <div className="flex flex-col gap-1.5 bg-layer-02 px-2.5 py-2 rounded-md">
        <div className="text-caption-01 text-text-helper">{s.label}</div>
        <input
          value={val}
          onChange={(e) => setVal(e.target.value)}
          autoFocus
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              onEdit(val);
              setEditing(false);
            }
          }}
          className="bg-field-01 border-0 border-b border-border-strong-01 outline-none text-text-primary text-body-short-01 px-1.5 py-1"
        />
        <div className="flex gap-1.5 justify-end">
          <button
            onClick={() => {
              setVal(s.value);
              setEditing(false);
            }}
            className="px-2 h-7 text-caption-01 text-link-primary hover:bg-layer-hover-01 rounded"
          >
            취소
          </button>
          <button
            onClick={() => {
              onEdit(val);
              setEditing(false);
            }}
            className="px-2 h-7 text-caption-01 bg-interactive text-white rounded hover:bg-[#0353e9]"
          >
            저장
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="group flex items-center gap-2.5">
      <div className="min-w-0 flex-1">
        <div className="text-caption-01 text-text-helper">
          {s.label} · {s.source}
        </div>
        <div className="text-body-short-01 text-text-primary">{s.value}</div>
      </div>
      <Confidence v={s.conf} />
      <div className="flex flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          onClick={() => setEditing(true)}
          title="수정"
          aria-label="수정"
          className="w-7 h-7 flex items-center justify-center rounded-md text-icon-secondary hover:bg-layer-hover-01"
        >
          <Icon name="edit" size={14} />
        </button>
        <button
          onClick={onRemove}
          title="이 신호 삭제"
          aria-label="이 신호 삭제"
          className="w-7 h-7 flex items-center justify-center rounded-md text-icon-secondary hover:bg-layer-hover-01"
        >
          <Icon name="close" size={14} />
        </button>
      </div>
    </div>
  );
};

// installed tool/plugin row
const InstalledRow: React.FC<{ t: InstalledTool; onToggle: () => void; onRemove: () => void }> = ({
  t,
  onToggle,
  onRemove,
}) => (
  <div className={`flex items-center gap-2 bg-layer-01 px-2 py-1 rounded-md ${t.enabled ? '' : 'opacity-55'}`}>
    <div
      className={`w-[22px] h-[22px] flex-shrink-0 flex items-center justify-center rounded ${
        t.enabled ? 'bg-blue-60' : 'bg-layer-03'
      }`}
    >
      <Icon name={t.icon} size={13} className="text-white" />
    </div>
    <div className="flex-1 min-w-0">
      <div className="flex items-center gap-1.5">
        <span className="text-caption-02 text-text-primary truncate">{t.name}</span>
        <Tag type={t.kind === 'plugin' ? 'purple' : 'gray'} size="sm">
          {t.kind === 'plugin' ? '플러그인' : '도구'}
        </Tag>
      </div>
      <div className="text-caption-01 text-text-helper leading-tight">{t.meta}</div>
    </div>
    <Toggle checked={t.enabled} onChange={() => onToggle()} />
    <RowMenu items={[{ icon: 'trash', label: '제거', danger: true, onClick: onRemove }]} />
  </div>
);

// loop proposal card — a "pending decision" the loop surfaced (accept / later)
const PROPOSAL_META: Record<ProposalKind, { icon: IconName; tint: string }> = {
  rule: { icon: 'edit', tint: 'text-link-primary' },
  tool: { icon: 'tools', tint: 'text-support-success' },
  prune: { icon: 'trash', tint: 'text-text-error' },
};

const ProposalCard: React.FC<{
  p: Proposal;
  onAccept: () => void;
  onDismiss: () => void;
  /** jump to the conversation (and message) the evidence came from */
  onOpenSource?: (conversationId: string, messageIndex?: number) => void;
}> = ({ p, onAccept, onDismiss, onOpenSource }) => {
  const m = PROPOSAL_META[p.kind];
  return (
    <div className="bg-layer-01 border border-border-subtle-01 rounded-lg p-3">
      <div className="flex items-start gap-2.5">
        <div className="w-6 h-6 flex-shrink-0 flex items-center justify-center rounded-md bg-layer-02">
          <Icon name={m.icon} size={14} className={m.tint} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-body-short-01 font-semibold text-text-primary leading-tight">{p.title}</div>
          <div className="text-caption-01 text-text-helper mt-1 leading-snug">{p.evidence}</div>
          {/* 근거 출처 — 클릭하면 관찰된 대화로 점프 (추적 가능) */}
          {p.source && (
            <button
              onClick={() => onOpenSource?.(p.source!.conversationId, p.source!.messageIndex)}
              title="근거가 된 대화 열기"
              className="mt-1.5 inline-flex items-center gap-1 text-caption-01 text-link-primary hover:underline"
            >
              <Icon name="chat" size={11} className="flex-shrink-0" />
              <span className="truncate">
                「{p.source.title}」{p.source.count ? ` · ${p.source.count}회 관찰` : ' 에서'}
              </span>
              <Icon name="arrow-up-right" size={11} className="opacity-70 flex-shrink-0" />
            </button>
          )}
          {p.rule && (
            <div className="mt-2 inline-flex items-center gap-1.5 bg-layer-02 border border-border-subtle-01 rounded px-2 py-1">
              <Icon name="add" size={12} className="text-link-primary flex-shrink-0" />
              <span className="text-caption-01 text-text-secondary">{p.rule}</span>
            </div>
          )}
          {p.tool && (
            <div className="mt-2 inline-flex items-center gap-1.5 bg-layer-02 border border-border-subtle-01 rounded px-2 py-1">
              <Icon name="tools" size={12} className="text-support-success flex-shrink-0" />
              <span className="text-caption-01 text-text-secondary">{p.tool.name}</span>
              <span className="text-caption-01 font-mono text-text-helper">/{p.tool.command}</span>
            </div>
          )}
          <div className="flex items-center gap-1.5 mt-2.5">
            <button
              onClick={onAccept}
              className={`inline-flex items-center gap-1 h-7 px-2.5 rounded-md text-caption-01 font-semibold transition-colors ${
                p.kind === 'prune'
                  ? 'bg-[#684e00] text-[#fddc69] hover:bg-[#523c00]'
                  : 'bg-interactive text-white hover:bg-[#0353e9]'
              }`}
            >
              <Icon name={p.kind === 'prune' ? 'warning' : 'check'} size={13} /> {p.acceptLabel}
            </button>
            <button
              onClick={onDismiss}
              className="h-7 px-2.5 rounded-md text-caption-01 text-text-secondary hover:bg-layer-hover-01 transition-colors"
            >
              나중에
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export const LearningPanel: React.FC<LearningPanelProps> = ({ isLearning = true, width = 320, onOpenConversation }) => {
  const {
    skills,
    setSkills,
    signals,
    setSignals,
    installed,
    setInstalled,
    setDetails,
    proposals,
    setProposals,
    highlightedSkill,
    panelScrollRef,
  } = useLearning();
  const skillRefs = useRef<Record<string, HTMLDivElement | null>>({});

  // transient confirmation after accepting/dismissing a proposal
  const [flash, setFlash] = useState<string | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const showFlash = (msg: string) => {
    setFlash(msg);
    if (flashTimer.current) clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setFlash(null), 2600);
  };

  // accept a loop proposal → mutate real state so it lands in My Skills too
  const acceptProposal = (p: Proposal) => {
    if (p.kind === 'rule' && p.targetSkillId && p.rule) {
      const target = p.targetSkillId;
      setDetails((d) => {
        const cur = d[target];
        if (!cur) return d;
        const ruleSource = p.source ? `관찰 승격 · ${p.source.title}` : '관찰에서 승격';
        return {
          ...d,
          [target]: { ...cur, rules: [...cur.rules, { id: `pr${Date.now()}`, text: p.rule!, source: ruleSource, on: true }] },
        };
      });
      showFlash('규칙을 반영했어요 · My Skills에서 확인');
    } else if (p.kind === 'tool' && p.tool) {
      const id = `us${Date.now()}`;
      const tool = p.tool;
      setSkills((a) => [
        ...a,
        { id, name: tool.name, ver: 'v1', uses: 0, trend: '새로 생성', enabled: true, command: tool.command },
      ]);
      setDetails((d) => ({
        ...d,
        [id]: {
          summary: tool.summary,
          createdFrom: '반복 작업에서 자동 생성',
          lastUsed: '방금',
          shared: false,
          rules: [],
          versions: [{ ver: 'v1', when: '방금', note: '루프 제안 승인으로 생성', current: true, rules: [] }],
          usage: [],
          permissions: [],
        },
      }));
      showFlash('새 도구를 저장했어요 · My Skills에서 확인');
    } else if (p.kind === 'prune' && p.targetSkillId) {
      const target = p.targetSkillId;
      setSkills((a) => a.map((x) => (x.id === target ? { ...x, state: 'stale' as const } : x)));
      showFlash('Stale로 표시했어요 · My Skills에서 확인');
    }
    setProposals((arr) => arr.filter((x) => x.id !== p.id));
  };

  useEffect(() => {
    if (highlightedSkill && skillRefs.current[highlightedSkill] && panelScrollRef.current) {
      const el = skillRefs.current[highlightedSkill]!;
      const top = el.offsetTop - panelScrollRef.current.offsetTop - 80;
      panelScrollRef.current.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
    }
  }, [highlightedSkill, panelScrollRef]);

  const activeSkills = skills.filter((s) => s.enabled).length;
  const activeTools = installed.filter((t) => t.enabled).length;

  return (
    <div className="h-full flex-shrink-0 bg-layer-01 border-l border-border-subtle-01 flex flex-col overflow-hidden" style={{ width }}>
      <div ref={panelScrollRef} className="flex-1 overflow-y-auto">
        {/* header */}
        <div className="flex items-center gap-2 px-4 pt-4 pb-1">
          <Icon name="renew" size={18} className="text-icon-primary" />
          <span className="text-body-short-02 font-semibold text-text-primary">학습 루프</span>
          <span className="ml-auto flex-shrink-0">
            <Tag type={isLearning ? 'green' : 'gray'} size="sm" dot={isLearning}>
              {isLearning ? '학습 중' : '일시정지'}
            </Tag>
          </span>
        </div>

        {/* loop proposals — pending decisions the learning loop surfaced */}
        {(proposals.length > 0 || flash) && (
          <div className="border-b border-border-subtle-01 bg-[#0f62fe]/[0.05]">
            <div className="flex items-center gap-2 px-4 pt-3.5 pb-2">
              <Icon name="idea" size={16} className="text-link-primary" />
              <span className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">
                검토 대기 · 루프 제안
              </span>
              {proposals.length > 0 && (
                <span className="ml-auto text-caption-01 text-text-helper font-mono">{proposals.length}</span>
              )}
            </div>
            <div className="px-4 pb-3.5 flex flex-col gap-2">
              {flash && (
                <div className="flex items-center gap-1.5 text-caption-01 text-support-success">
                  <Icon name="check-circle" size={13} className="flex-shrink-0" /> {flash}
                </div>
              )}
              {proposals.map((p) => (
                <ProposalCard
                  key={p.id}
                  p={p}
                  onAccept={() => acceptProposal(p)}
                  onDismiss={() => setProposals((arr) => arr.filter((x) => x.id !== p.id))}
                  onOpenSource={onOpenConversation}
                />
              ))}
            </div>
          </div>
        )}

        {/* installed tools / plugins — what the agent can call now */}
        <PanelSection icon="plug" title="설치된 도구 · 플러그인" count={`${activeTools}/${installed.length} 활성`}>
          <div className="flex flex-col gap-1">
            {installed.map((t) => (
              <InstalledRow
                key={t.id}
                t={t}
                onToggle={() => setInstalled((arr) => arr.map((x) => (x.id === t.id ? { ...x, enabled: !x.enabled } : x)))}
                onRemove={() => setInstalled((arr) => arr.filter((x) => x.id !== t.id))}
              />
            ))}
            {installed.length === 0 && (
              <span className="text-caption-01 text-text-helper">설치된 도구가 없어요. 마켓에서 설치해보세요.</span>
            )}
          </div>
        </PanelSection>

        {/* user model — editable signals */}
        <PanelSection icon="user" title="나에 대한 모델" count={`신호 ${signals.length + 42}`}>
          <div className="flex flex-col gap-2.5">
            {signals.map((u) => (
              <SignalRow
                key={u.id}
                s={u}
                onEdit={(v) => setSignals((arr) => arr.map((x) => (x.id === u.id ? { ...x, value: v, source: '명시적 교정' } : x)))}
                onRemove={() => setSignals((arr) => arr.filter((x) => x.id !== u.id))}
              />
            ))}
            {signals.length === 0 && <span className="text-caption-01 text-text-helper">모든 신호를 지웠어요.</span>}
          </div>
        </PanelSection>

        {/* learned skills — enable/disable/delete */}
        <PanelSection icon="skill" title="학습한 스킬" count={`${activeSkills}/${skills.length} 활성`}>
          <div className="flex flex-col gap-2">
            {skills.map((s) => {
              const hot = highlightedSkill === s.id;
              return (
                <div
                  key={s.id}
                  ref={(el) => {
                    skillRefs.current[s.id] = el;
                  }}
                  className={`flex items-center gap-2 bg-layer-01 pl-2.5 pr-1.5 py-2 rounded-md ${s.enabled ? '' : 'opacity-50'} ${
                    hot ? 'ia-skill-hot outline outline-2 outline-blue-50 -outline-offset-1' : ''
                  }`}
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className={`text-body-short-01 text-text-primary truncate ${s.enabled ? '' : 'line-through'}`}>
                        {s.name} <span className="text-text-helper font-mono">{s.ver}</span>
                      </span>
                      {!s.enabled && (
                        <Tag type="gray" size="sm">
                          비활성
                        </Tag>
                      )}
                    </div>
                    <div className="text-caption-01 text-text-helper">
                      {s.uses}회 사용 · {s.trend}
                    </div>
                  </div>
                  <RowMenu
                    items={[
                      {
                        icon: s.enabled ? 'view-off' : 'view',
                        label: s.enabled ? '비활성화' : '활성화',
                        onClick: () => setSkills((arr) => arr.map((x) => (x.id === s.id ? { ...x, enabled: !x.enabled } : x))),
                      },
                      {
                        icon: 'trash',
                        label: '삭제',
                        danger: true,
                        onClick: () => setSkills((arr) => arr.filter((x) => x.id !== s.id)),
                      },
                    ]}
                  />
                </div>
              );
            })}
          </div>
        </PanelSection>

        {/* this session */}
        <PanelSection icon="growth" title="이번 세션에서 배운 것">
          <ul className="pl-4 flex flex-col gap-1.5 list-disc">
            {SESSION_DELTAS.map((d, i) => (
              <li key={i} className="text-caption-02 leading-[17px] text-text-secondary">
                {d}
              </li>
            ))}
          </ul>
        </PanelSection>
      </div>
    </div>
  );
};

// Made with Bob
