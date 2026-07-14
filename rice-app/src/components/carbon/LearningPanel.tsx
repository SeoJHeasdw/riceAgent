import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon, Tag } from '../ui';
import type { IconName } from '../ui/Icon';
import { useLearning } from '../../lib/learning/LearningContext';
import { type Proposal, type ProposalKind } from '../../lib/learningData';
import type { SkillSuggestion } from '../../lib/types';
import { api } from '../../lib/api';
import { TransparencyPanel } from './TransparencyPanel';

/**
 * LearningPanel - the right-side learning surface. Interactive: users decide
 * inbox suggestions, enable/disable/remove installed plugins, and
 * enable/disable/delete learned skills. Runtime-memory viewing/removal moved to
 * the transparency panel (2026-07-14). A skill highlighted from the chat thread
 * scrolls into view and pulses.
 */

export interface LearningPanelProps {
  isLearning?: boolean;
  /** Panel width in px (drag-resizable from parent) */
  width?: number;
  /** open the conversation (and scroll to the message) a proposal's evidence came from */
  onOpenConversation?: (conversationId: string, messageIndex?: number) => void;
}

// small overflow menu
interface MenuItem {
  icon: IconName;
  label: string;
  danger?: boolean;
  onClick: () => void;
}

const RowMenu: React.FC<{ items: MenuItem[]; menuTitle: string }> = ({ items, menuTitle }) => {
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
        title={menuTitle}
        aria-label={menuTitle}
        className={`flex h-[28px] w-[28px] items-center justify-center rounded-md transition-colors ${
          open ? 'bg-layer-hover-01' : 'hover:bg-layer-hover-01'
        }`}
      >
        <Icon name="overflow" size={16} className="text-icon-secondary" />
      </button>
      {open && (
        <div className="absolute top-[30px] right-0 z-30 min-w-[148px] bg-layer-02 border border-border-subtle-01 rounded-md shadow-elevation-2 overflow-hidden">
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
  laterLabel: string;
}> = ({ p, onAccept, onDismiss, onOpenSource, laterLabel }) => {
  const { t } = useTranslation('chat');
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
              title={t('learning.sourceObservedFrom').trim()}
              className="mt-1.5 inline-flex items-center gap-1 text-caption-01 text-link-primary hover:underline"
            >
              <Icon name="chat" size={11} className="flex-shrink-0" />
              <span className="truncate">
                「{p.source.title}」{p.source.count ? t('learning.sourceObservedCount', { count: p.source.count }) : t('learning.sourceObservedFrom')}
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
              {laterLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

// ── 제안함(suggestion inbox) 카드 — 런타임 skill_suggestions 실배선 ──
const SuggestionCard: React.FC<{
  s: SkillSuggestion;
  onDecide: (approved: boolean) => void;
  deciding: boolean;
}> = ({ s, onDecide, deciding }) => {
  const { t } = useTranslation('chat');
  const kindIconMap: Record<SkillSuggestion['kind'], { icon: IconName; tint: string }> = {
    create: { icon: 'add', tint: 'text-support-success' },
    patch: { icon: 'edit', tint: 'text-link-primary' },
    archive: { icon: 'trash', tint: 'text-text-error' },
  };
  const originKeyMap: Record<string, string> = {
    discovery: t('learning.suggestionOriginDiscovery'),
    timeout: t('learning.suggestionOriginTimeout'),
    review: t('learning.suggestionOriginReview'),
    learn: t('learning.suggestionOriginLearn'),
    agent: t('learning.suggestionOriginAgent'),
    consolidation: t('learning.suggestionOriginConsolidation'),
  };
  const m = kindIconMap[s.kind];
  const adds = s.payload.diff?.lines.filter((l) => l.type === 'add').length ?? 0;
  const dels = s.payload.diff?.lines.filter((l) => l.type === 'remove').length ?? 0;
  const versionText =
    s.kind === 'archive'
      ? t('learning.suggestionVersionArchive')
      : s.payload.from_version === 0
        ? t('learning.suggestionVersionNew')
        : t('learning.suggestionVersionUpgrade', { from: s.payload.from_version, to: s.payload.to_version });
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
              {originKeyMap[s.origin] ?? s.origin}
            </span>
            {s.kind !== 'archive' && (adds > 0 || dels > 0) && (
              <span className="font-mono">
                <span className="text-support-success">+{adds}</span>{' '}
                <span className="text-text-error">-{dels}</span>
              </span>
            )}
            {(s.payload.warnings?.length ?? 0) > 0 && (
              <span className="inline-flex items-center gap-1 text-support-warning">
                <Icon name="warning" size={11} /> {t('learning.suggestionWarnMasked')}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1.5 mt-2.5">
            <button
              onClick={() => onDecide(true)}
              disabled={deciding}
              className="inline-flex items-center gap-1 h-7 px-2.5 rounded-md text-caption-01 font-semibold bg-interactive text-white hover:bg-interactive-hover transition-colors disabled:opacity-50"
            >
              <Icon name="check" size={13} /> {s.kind === 'archive' ? t('learning.suggestionApproveArchive') : t('learning.suggestionApprove')}
            </button>
            <button
              onClick={() => onDecide(false)}
              disabled={deciding}
              className="h-7 px-2.5 rounded-md text-caption-01 text-text-secondary hover:bg-layer-hover-01 transition-colors disabled:opacity-50"
            >
              {t('learning.suggestionReject')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export const LearningPanel: React.FC<LearningPanelProps> = ({ width = 320, onOpenConversation }) => {
  const { t } = useTranslation('chat');
  const {
    skills,
    setSkills,
    setDetails,
    proposals,
    setProposals,
    suggestions,
    decideSuggestion,
    discoverNow,
    myPlugins,
    setMyPlugins,
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

  const handleSuggestionDecision = async (s: SkillSuggestion, approved: boolean) => {
    setDecidingId(s.id);
    const ok = await decideSuggestion(s.id, approved);
    setDecidingId(null);
    if (!ok) {
      showFlash(t('learning.flashDecideFailed'));
      return;
    }
    showFlash(
      approved
        ? s.kind === 'archive'
          ? t('learning.flashArchived', { id: s.skill_id })
          : t('learning.flashApplied', { id: s.skill_id })
        : t('learning.flashRejected'),
    );
  };

  const handleDiscover = async () => {
    if (discovering) return;
    setDiscovering(true);
    const added = await discoverNow();
    setDiscovering(false);
    showFlash(added > 0 ? t('learning.flashDiscoverFound', { count: added }) : t('learning.flashDiscoverNone'));
  };

  // accept a loop proposal → mutate real state so it lands in My Skills too
  const acceptProposal = (p: Proposal) => {
    if (p.kind === 'rule' && p.targetSkillId && p.rule) {
      const target = p.targetSkillId;
      setDetails((d) => {
        const cur = d[target];
        if (!cur) return d;
        // rule source는 관찰 대화 제목을 포함하는 동적 문자열이라 번역 결과를 저장해야 한다.
        // 언어 변경 시에는 다음 승인부터 새 언어로 저장된다 (히스토리 레코드라 허용 범위).
        const ruleSource = p.source
          ? t('learning.ruleSourceObserved', { title: p.source.title })
          : t('learning.ruleSourceObservedFallback');
        return {
          ...d,
          [target]: { ...cur, rules: [...cur.rules, { id: `pr${Date.now()}`, text: p.rule!, source: ruleSource, on: true }] },
        };
      });
      showFlash(t('learning.flashRuleApplied'));
    } else if (p.kind === 'tool' && p.tool) {
      const id = `us${Date.now()}`;
      const tool = p.tool;
      setSkills((a) => [
        ...a,
        { id, name: tool.name, ver: 'v1', uses: 0, trend: 'trend_new', enabled: true, command: tool.command },
      ]);
      setDetails((d) => ({
        ...d,
        [id]: {
          summary: tool.summary,
          createdFrom: 'created_from_loop',
          lastUsed: 'last_used_just_now',
          shared: false,
          rules: [],
          versions: [{ ver: 'v1', when: 'last_used_just_now', note: t('learning.noteLoopApproved'), current: true, rules: [] }],
          usage: [],
          permissions: [],
        },
      }));
      showFlash(t('learning.flashToolSaved'));
    } else if (p.kind === 'prune' && p.targetSkillId) {
      const target = p.targetSkillId;
      setSkills((a) => a.map((x) => (x.id === target ? { ...x, state: 'stale' as const } : x)));
      showFlash(t('learning.flashMarkedStale'));
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
  const activePlugins = myPlugins.filter((p) => p.enabled).length;

  return (
    <div className="h-full flex-shrink-0 bg-layer-01 border-l border-border-subtle-01 flex flex-col overflow-hidden" style={{ width }}>
      {/* 제목은 패널의 정체성, 우측 버튼은 감사/audit 액션이다. 패널 토글과
          같은 renew 아이콘을 반복하지 않아 세 컨트롤의 역할이 섞이지 않게 한다. */}
      <div className="flex items-center gap-2 px-4 pr-6 h-11 flex-shrink-0 border-b border-border-subtle-00 electron-drag">
          <span className="text-label-02 font-semibold text-text-primary">{t('learning.panelTitle')}</span>
          <span className="h-1.5 w-1.5 rounded-full bg-support-success/80" title={t('learning.panelActiveIndicatorTitle')} />
          <button
            type="button"
            onClick={() => setTransparencyOpen(true)}
            title={t('learning.transparencyTitle')}
            className="electron-no-drag ml-auto flex h-[28px] flex-shrink-0 items-center gap-1.5 rounded-lg border border-border-subtle-02 bg-layer-02/60 px-2.5 text-caption-01 font-medium text-text-primary transition-colors hover:border-border-strong-01 hover:bg-layer-hover-02"
          >
            <Icon name="view" size={14} className="text-blue-40" />
            {t('learning.transparencyButton')}
          </button>
        </div>

      <div ref={panelScrollRef} className="flex-1 overflow-y-auto">
        {/* suggestion inbox — 런타임 영속 제안함(skill_suggestions) 실배선.
            파킹 카드(채팅 diff 카드)와 달리 스트림 밖에서 태어난 제안이 여기 산다:
            응답 없이 만료된 제안, 백그라운드/수동 발굴 산출물. */}
        <div className="border-b border-border-subtle-01 border-l-2 border-l-interactive/70 bg-interactive/[0.04]">
          <div className="flex items-center gap-2 px-4 pt-3.5 pb-2">
            <Icon name="download" size={16} className="text-link-primary" />
            <span className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">
              {t('learning.sectionInbox')}
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
              title={t('learning.discoverTitle')}
              className="ml-auto inline-flex items-center gap-1 h-6 px-2 rounded-md text-caption-01 text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary transition-colors disabled:opacity-50"
            >
              <Icon name="search" size={12} className={discovering ? 'animate-pulse' : ''} />
              {discovering ? t('learning.discoverButtonBusy') : t('learning.discoverButton')}
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
                {t('learning.inboxEmpty')}
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
                {t('learning.sectionProposals')}
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
                  laterLabel={t('learning.proposalLater')}
                />
              ))}
            </div>
          </div>
        )}

        {/* installed plugins — enable/disable/remove. 이전의 "나에 대한 모델"(런타임
            메모리) 섹션을 대체한다: 메모리 확인·삭제는 투명성 패널로 이관했고(2026-07-14),
            여기선 학습한 스킬과 같은 어휘로 설치된 플러그인을 한눈에 관리한다. */}
        <PanelSection icon="plug" title={t('learning.sectionPlugins')} count={t('learning.pluginActiveCount', { active: activePlugins, total: myPlugins.length })}>
          <div className="flex flex-col gap-px">
            {myPlugins.map((p) => {
              // 빌트인 플러그인은 활성/비활성만 가능(삭제 어포던스 없음 — 스킬과 대칭).
              const builtin = p.builtin;
              const togglePlugin = () => {
                const next = !p.enabled;
                setMyPlugins((arr) => arr.map((x) => (x.id === p.id ? { ...x, enabled: next } : x)));
                // 런타임에 영속(재시작 후에도 유지). 실패 시 롤백 + 플래시.
                if (api.isEnabled)
                  void api.setPluginEnabled(p.id, next).then((res) => {
                    if (!res.success) {
                      setMyPlugins((arr) => arr.map((x) => (x.id === p.id ? { ...x, enabled: !next } : x)));
                      showFlash(t('learning.pluginToggleFailed'));
                    }
                  });
              };
              return (
                <div
                  key={p.id}
                  className={`group/skill flex h-[36px] items-center gap-2 border border-transparent bg-layer-01 pl-2.5 pr-1 rounded-lg transition-colors hover:border-border-subtle-01 hover:bg-layer-hover-01 ${p.enabled ? '' : 'opacity-50'}`}
                >
                  <div className="flex flex-1 min-w-0 items-center gap-1.5">
                    <span className="flex-shrink-0">
                      <Icon name={p.icon} size={13} className="text-icon-secondary opacity-90" />
                    </span>
                    {builtin && (
                      <span title={t('learning.pluginBuiltinTitle')} className="flex-shrink-0">
                        <Icon name="sparkle" size={11} className="text-teal-40 opacity-80" />
                      </span>
                    )}
                    <span className={`min-w-0 flex-1 truncate text-body-short-01 text-text-primary ${p.enabled ? '' : 'line-through'}`}>
                      {p.name}
                    </span>
                    <span className="hidden max-w-[92px] flex-shrink truncate text-caption-01 text-text-helper min-[360px]:block">
                      {t('learning.pluginSkillCount', { count: p.skills.length })}
                    </span>
                    {!p.enabled && <Tag type="gray" size="sm">{t('learning.skillInactive')}</Tag>}
                  </div>
                  <RowMenu
                    menuTitle={t('learning.rowMenuTitle')}
                    items={[
                      {
                        icon: p.enabled ? 'view-off' : 'view',
                        label: p.enabled ? t('learning.skillMenuDisable') : t('learning.skillMenuEnable'),
                        onClick: togglePlugin,
                      },
                      ...(builtin
                        ? []
                        : [
                            {
                              icon: 'trash' as IconName,
                              label: t('learning.skillMenuDelete'),
                              danger: true,
                              onClick: () => {
                                setMyPlugins((arr) => arr.filter((x) => x.id !== p.id));
                                if (api.isEnabled) void api.removeArtifact(p.id);
                              },
                            },
                          ]),
                    ]}
                  />
                </div>
              );
            })}
            {myPlugins.length === 0 && (
              <span className="text-caption-01 text-text-helper">{t('learning.pluginEmpty')}</span>
            )}
          </div>
        </PanelSection>

        {/* learned skills — enable/disable/delete */}
        <PanelSection icon="skill" title={t('learning.sectionSkills')} count={t('learning.skillActiveCount', { active: activeSkills, total: skills.length })}>
          <div className="flex flex-col gap-px">
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
                  className={`group/skill flex h-[36px] items-center gap-2 border border-transparent bg-layer-01 pl-2.5 pr-1 rounded-lg transition-colors hover:border-border-subtle-01 hover:bg-layer-hover-01 ${s.enabled ? '' : 'opacity-50'} ${
                    hot ? 'ia-skill-hot outline outline-2 outline-blue-50 -outline-offset-1' : ''
                  }`}
                >
                  <div className="flex flex-1 min-w-0 items-center gap-1.5">
                    {builtin && (
                      <span title={t('learning.skillBuiltinTitle')} className="flex-shrink-0">
                        <Icon name="sparkle" size={11} className="text-teal-40 opacity-80" />
                      </span>
                    )}
                    <span className={`min-w-0 flex-1 truncate text-body-short-01 text-text-primary ${s.enabled ? '' : 'line-through'}`}>
                      {s.name}
                    </span>
                    <span className="flex-shrink-0 text-label-micro font-mono text-text-helper">
                      {s.ver}
                    </span>
                    <span className="hidden max-w-[92px] flex-shrink truncate text-caption-01 text-text-helper min-[360px]:block">
                      {t('learning.skillUsageCount', { count: s.uses })}
                    </span>
                    {!s.enabled && <Tag type="gray" size="sm">{t('learning.skillInactive')}</Tag>}
                  </div>
                  <RowMenu
                    menuTitle={t('learning.rowMenuTitle')}
                    items={[
                      {
                        icon: s.enabled ? 'view-off' : 'view',
                        label: s.enabled ? t('learning.skillMenuDisable') : t('learning.skillMenuEnable'),
                        onClick: toggleSkill,
                      },
                      ...(builtin
                        ? []
                        : [
                            {
                              icon: 'trash' as IconName,
                              label: t('learning.skillMenuDelete'),
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
              <span className="text-caption-01 text-text-helper">{t('learning.skillEmpty')}</span>
            )}
          </div>
        </PanelSection>
      </div>
      <TransparencyPanel open={transparencyOpen} onClose={() => setTransparencyOpen(false)} />
    </div>
  );
};

// Made with Bob
