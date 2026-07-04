import React, { useEffect, useRef, useState } from 'react';
import { Icon, Tag, Toggle } from '../ui';
import type { IconName } from '../ui/Icon';
import { useLearning } from '../../lib/learning/LearningContext';
import {
  type InstalledTool,
  type Proposal,
  type ProposalKind,
  type UserSignal,
} from '../../lib/learningData';
import type { SkillSuggestion } from '../../lib/types';
import { api } from '../../lib/api';
import { TransparencyPanel } from './TransparencyPanel';

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

// confidence meter — 신뢰도 구간별로 색을 달리해 바가 "다 똑같은 파란 줄"로
// 읽히지 않게 한다 (낮음=회색, 보통=블루, 높음=AI 그라디언트)
const Confidence: React.FC<{ v: number }> = ({ v }) => {
  const fill =
    v >= 0.75
      ? 'bg-gradient-to-r from-[var(--ai-gradient-start)] to-[var(--ai-gradient-end)]'
      : v >= 0.4
        ? 'bg-blue-50'
        : 'bg-border-strong-03';
  return (
    <div
      title={`신뢰도 ${Math.round(v * 100)}%`}
      className="w-11 h-1 bg-border-strong-01 flex-shrink-0 rounded-full overflow-hidden"
    >
      <div className={`h-full rounded-full ${fill}`} style={{ width: `${Math.round(v * 100)}%` }} />
    </div>
  );
};

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
            className="px-2 h-7 text-caption-01 bg-interactive text-white rounded hover:bg-interactive-hover"
          >
            저장
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="group flex items-center gap-2.5 py-1">
      <div className="min-w-0 flex-1">
        {/* label은 그룹 헤더로 승격됐으므로 행에는 출처만 */}
        <div className="text-caption-01 text-text-helper">{s.source}</div>
        <div className="text-body-short-01 text-text-primary">{s.value}</div>
      </div>
      {/* 신뢰도 바 — 항상 노출하면 로그처럼 읽혀서, 행에 관심을 줄 때만 */}
      <span className="opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity">
        <Confidence v={s.conf} />
      </span>
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
  <div className={`flex items-center gap-2 px-2 py-1.5 rounded-md ${t.enabled ? '' : 'opacity-55'}`}>
    <div className="w-[22px] h-[22px] flex-shrink-0 flex items-center justify-center rounded bg-layer-02 border border-border-subtle-02">
      <Icon name={t.icon} size={13} className={t.enabled ? 'text-icon-primary' : 'text-icon-secondary'} />
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
                  ? 'bg-yellow-60 text-yellow-20 hover:bg-yellow-70'
                  : 'bg-interactive text-white hover:bg-interactive-hover'
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

// ── 제안함(suggestion inbox) 카드 — 런타임 skill_suggestions 실배선 ──
const SUGGESTION_KIND_META: Record<SkillSuggestion['kind'], { icon: IconName; tint: string; label: string }> = {
  create: { icon: 'add', tint: 'text-support-success', label: '신규 스킬' },
  patch: { icon: 'edit', tint: 'text-link-primary', label: '개선' },
  archive: { icon: 'trash', tint: 'text-text-error', label: '보관' },
};

const SUGGESTION_ORIGIN_LABELS: Record<string, string> = {
  discovery: '패턴 발굴',
  timeout: '응답 없던 제안',
  review: '대화 리뷰',
  learn: '수동 학습',
  agent: '에이전트 발의',
  consolidation: '통합 정리',
};

const SuggestionCard: React.FC<{
  s: SkillSuggestion;
  onDecide: (approved: boolean) => void;
  deciding: boolean;
}> = ({ s, onDecide, deciding }) => {
  const m = SUGGESTION_KIND_META[s.kind];
  const adds = s.payload.diff?.lines.filter((l) => l.type === 'add').length ?? 0;
  const dels = s.payload.diff?.lines.filter((l) => l.type === 'remove').length ?? 0;
  const versionText =
    s.kind === 'archive'
      ? '보관 제안'
      : s.payload.from_version === 0
        ? 'v1 신규'
        : `v${s.payload.from_version}→v${s.payload.to_version}`;
  return (
    <div className="bg-layer-01 border border-border-subtle-01 rounded-lg p-3">
      <div className="flex items-start gap-2.5">
        <div className="w-6 h-6 flex-shrink-0 flex items-center justify-center rounded-md bg-layer-02">
          <Icon name={m.icon} size={14} className={m.tint} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="text-body-short-01 font-semibold text-text-primary truncate">{s.skill_id}</span>
            <span className="text-caption-01 text-text-helper font-mono flex-shrink-0">{versionText}</span>
          </div>
          <div className="text-caption-01 text-text-helper mt-1 leading-snug">
            {s.kind === 'archive' ? s.payload.reason ?? s.payload.changelog : s.payload.changelog}
          </div>
          <div className="flex items-center gap-2 mt-1.5 text-caption-01 text-text-helper">
            <span className="inline-flex items-center gap-1">
              <Icon name="renew" size={11} className="opacity-70" />
              {SUGGESTION_ORIGIN_LABELS[s.origin] ?? s.origin}
            </span>
            {s.kind !== 'archive' && (adds > 0 || dels > 0) && (
              <span className="font-mono">
                <span className="text-support-success">+{adds}</span>{' '}
                <span className="text-text-error">-{dels}</span>
              </span>
            )}
            {(s.payload.warnings?.length ?? 0) > 0 && (
              <span className="inline-flex items-center gap-1 text-support-warning">
                <Icon name="warning" size={11} /> 민감정보 마스킹됨
              </span>
            )}
          </div>
          <div className="flex items-center gap-1.5 mt-2.5">
            <button
              onClick={() => onDecide(true)}
              disabled={deciding}
              className="inline-flex items-center gap-1 h-7 px-2.5 rounded-md text-caption-01 font-semibold bg-interactive text-white hover:bg-interactive-hover transition-colors disabled:opacity-50"
            >
              <Icon name="check" size={13} /> {s.kind === 'archive' ? '보관 승인' : '반영'}
            </button>
            <button
              onClick={() => onDecide(false)}
              disabled={deciding}
              className="h-7 px-2.5 rounded-md text-caption-01 text-text-secondary hover:bg-layer-hover-01 transition-colors disabled:opacity-50"
            >
              거부
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
    suggestions,
    decideSuggestion,
    discoverNow,
    removeSignal,
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

  // 제안함 결정/발굴 진행 상태 — 발굴은 LLM 1회 호출이라 느릴 수 있다.
  const [decidingId, setDecidingId] = useState<string | null>(null);
  const [discovering, setDiscovering] = useState(false);
  // 학습 투명성 모달 — 컨텍스트 렌더·격리/차단 메모리·되돌리기.
  const [transparencyOpen, setTransparencyOpen] = useState(false);

  // "나에 대한 모델" 그룹 펼침 상태 — 기본은 preview 2개 + count로 접힘
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());

  const handleSuggestionDecision = async (s: SkillSuggestion, approved: boolean) => {
    setDecidingId(s.id);
    const ok = await decideSuggestion(s.id, approved);
    setDecidingId(null);
    if (!ok) {
      showFlash('결정을 전달하지 못했어요 · 런타임 연결을 확인하세요');
      return;
    }
    showFlash(
      approved
        ? s.kind === 'archive'
          ? `${s.skill_id} 보관됨`
          : `${s.skill_id} 반영됨 · My Skills에서 확인`
        : '제안을 거부했어요 · 같은 제안은 당분간 다시 오지 않습니다',
    );
  };

  const handleDiscover = async () => {
    if (discovering) return;
    setDiscovering(true);
    const added = await discoverNow();
    setDiscovering(false);
    showFlash(added > 0 ? `반복 패턴 ${added}건을 찾았어요` : '새로 발굴된 패턴이 없어요');
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
      {/* header — 스크롤 밖 고정, 사이드바·채팅 헤더와 같은 52px 라인 */}
      <div className="flex items-center gap-2 px-4 h-[52px] border-b border-border-subtle-01 flex-shrink-0">
          <Icon name="renew" size={18} className="text-icon-primary" />
          <span className="text-body-short-02 font-semibold text-text-primary">학습 루프</span>
          <button
            type="button"
            onClick={() => setTransparencyOpen(true)}
            title="학습 투명성 — 모델이 본 것/못 본 것, 격리·차단된 메모리, 되돌릴 수 있는 변경"
            className="ml-auto flex-shrink-0 flex items-center gap-1 h-[22px] px-2 rounded text-label-micro text-text-secondary hover:bg-layer-hover-01"
          >
            <Icon name="view" size={14} />
            투명성
          </button>
          <span className="flex-shrink-0">
            {isLearning ? (
              // 살아있는 루프임이 느껴지도록 도트에 은은한 pulse
              <span className="inline-flex items-center gap-1.5 h-[18px] px-2 rounded-full text-label-micro font-semibold bg-support-success/12 text-support-success">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full rounded-full bg-support-success opacity-60 animate-ping" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-support-success" />
                </span>
                학습 중
              </span>
            ) : (
              <Tag type="gray" size="sm">
                일시정지
              </Tag>
            )}
          </span>
        </div>

      <div ref={panelScrollRef} className="flex-1 overflow-y-auto">
        {/* suggestion inbox — 런타임 영속 제안함(skill_suggestions) 실배선.
            파킹 카드(채팅 diff 카드)와 달리 스트림 밖에서 태어난 제안이 여기 산다:
            응답 없이 만료된 제안, 백그라운드/수동 발굴 산출물. */}
        <div className="border-b border-border-subtle-01 border-l-2 border-l-interactive/70 bg-interactive/[0.04]">
          <div className="flex items-center gap-2 px-4 pt-3.5 pb-2">
            <Icon name="download" size={16} className="text-link-primary" />
            <span className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">
              제안함
            </span>
            <span className="ai-label">
              <span className="ai-label__text">AI</span>
            </span>
            {suggestions.length > 0 && (
              <span className="text-caption-01 text-text-helper font-mono">{suggestions.length}</span>
            )}
            <button
              onClick={handleDiscover}
              disabled={discovering}
              title="최근 대화들에서 반복 패턴을 발굴합니다 (LLM 호출)"
              className="ml-auto inline-flex items-center gap-1 h-6 px-2 rounded-md text-caption-01 text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary transition-colors disabled:opacity-50"
            >
              <Icon name="search" size={12} className={discovering ? 'animate-pulse' : ''} />
              {discovering ? '발굴 중…' : '패턴 발굴'}
            </button>
          </div>
          <div className="px-4 pb-3.5 flex flex-col gap-2">
            {suggestions.map((s) => (
              <SuggestionCard
                key={s.id}
                s={s}
                deciding={decidingId === s.id}
                onDecide={(approved) => void handleSuggestionDecision(s, approved)}
              />
            ))}
            {suggestions.length === 0 && (
              <span className="text-caption-01 text-text-helper">
                대기 중인 제안이 없어요. 시간이 지나면 발굴·리뷰 제안이 쌓입니다.
              </span>
            )}
          </div>
        </div>

        {/* loop proposals — pending decisions the learning loop surfaced */}
        {(proposals.length > 0 || flash) && (
          <div className="border-b border-border-subtle-01 border-l-2 border-l-interactive/70 bg-interactive/[0.04]">
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
              <div className="flex items-center gap-2.5 rounded-lg border border-dashed border-border-strong-01 px-3 py-2.5">
                <Icon name="plug" size={16} className="text-icon-secondary flex-shrink-0" />
                <span className="text-caption-01 text-text-helper">
                  설치된 도구가 없어요. 마켓에서 설치해보세요.
                </span>
              </div>
            )}
          </div>
        </PanelSection>

        {/* user model — grouped memory card. 같은 label끼리 묶고, 반복 항목은
            preview 2개 + count로 접어 raw log처럼 쌓이지 않게 한다. */}
        <PanelSection icon="user" title="나에 대한 모델" count={`신호 ${signals.length}`}>
          <div className="flex flex-col gap-3">
            {Array.from(
              signals.reduce((m, u) => {
                const arr = m.get(u.label) ?? [];
                arr.push(u);
                m.set(u.label, arr);
                return m;
              }, new Map<string, UserSignal[]>()),
            ).map(([label, items]) => {
              const expanded = expandedGroups.has(label);
              const visibleItems = expanded ? items : items.slice(0, 2);
              const hiddenCount = items.length - visibleItems.length;
              return (
                <div key={label}>
                  <div className="flex items-center gap-1.5 mb-1">
                    <span className="text-caption-01 font-semibold text-text-secondary">{label}</span>
                    {items.length > 1 && (
                      <span className="text-caption-01 text-text-helper font-mono">{items.length}</span>
                    )}
                  </div>
                  <div className="flex flex-col gap-1.5 border-l border-border-subtle-02 pl-2.5">
                    {visibleItems.map((u) => (
                      <SignalRow
                        key={u.id}
                        s={u}
                        onEdit={(v) =>
                          setSignals((arr) => arr.map((x) => (x.id === u.id ? { ...x, value: v, source: '명시적 교정' } : x)))
                        }
                        // 런타임 메모리(mem_*)는 서버에서도 삭제된다 — HITL 없는 유일한
                        // 학습 경로의 사후 통제 수단(Self-Improvement v3 위생).
                        onRemove={() => removeSignal(u.id)}
                      />
                    ))}
                    {(hiddenCount > 0 || expanded) && (
                      <button
                        onClick={() =>
                          setExpandedGroups((s) => {
                            const next = new Set(s);
                            if (next.has(label)) next.delete(label);
                            else next.add(label);
                            return next;
                          })
                        }
                        className="self-start text-caption-01 text-text-helper hover:text-text-secondary transition-colors"
                      >
                        {expanded ? '접기' : `외 ${hiddenCount}개 더 보기`}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
            {signals.length === 0 && (
              <span className="text-caption-01 text-text-helper">아직 학습된 신호가 없어요. 대화에서 자동으로 쌓입니다.</span>
            )}
          </div>
        </PanelSection>

        {/* learned skills — enable/disable/delete */}
        <PanelSection icon="skill" title="학습한 스킬" count={`${activeSkills}/${skills.length} 활성`}>
          <div className="flex flex-col gap-2">
            {skills.map((s) => {
              const hot = highlightedSkill === s.id;
              // 빌트인: 토글은 서버 상태(active/archived)로 영속하고 삭제 항목은 아예
              // 제공하지 않는다(백엔드도 422로 거부 — UI가 먼저 어포던스를 지운다).
              const builtin = s.origin === 'builtin';
              const toggleSkill = () => {
                const next = !s.enabled;
                setSkills((arr) =>
                  arr.map((x) =>
                    x.id === s.id
                      ? { ...x, enabled: next, ...(builtin ? { state: next ? ('active' as const) : ('archived' as const) } : {}) }
                      : x,
                  ),
                );
                if (builtin) void api.setSkillState(s.id, next ? 'active' : 'archived');
              };
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
                      {builtin && (
                        <span title="기본 내장 스킬 — 활성/비활성만 가능" className="flex-shrink-0">
                          <Icon name="sparkle" size={11} className="text-teal-40 opacity-80" />
                        </span>
                      )}
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
                        onClick: toggleSkill,
                      },
                      ...(builtin
                        ? []
                        : [
                            {
                              icon: 'trash' as IconName,
                              label: '삭제',
                              danger: true,
                              onClick: () => setSkills((arr) => arr.filter((x) => x.id !== s.id)),
                            },
                          ]),
                    ]}
                  />
                </div>
              );
            })}
            {skills.length === 0 && (
              <span className="text-caption-01 text-text-helper">아직 학습된 스킬이 없어요. 대화를 나누면 자동으로 자라납니다.</span>
            )}
          </div>
        </PanelSection>
      </div>
      <TransparencyPanel open={transparencyOpen} onClose={() => setTransparencyOpen(false)} />
    </div>
  );
};

// Made with Bob
