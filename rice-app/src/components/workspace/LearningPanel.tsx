import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon, Tag } from '../ui';
import type { IconName } from '../ui/Icon';
import { useLearning } from '../../lib/learning/LearningContext';
import { renderChangelog, wrapT, type Proposal, type ProposalKind, type Skill } from '../../lib/learningData';
import type { SkillCandidate, SkillSuggestion } from '../../lib/types';
import { api } from '../../lib/api';
import { localizeLearningWarning } from '../../lib/learningWarnings';
import { TransparencyPanel } from './TransparencyPanel';
import { useMarketplace } from '../../hooks/useMarketplace';
import { publishedMarketId } from '../../lib/skillBridge';

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
  /** navigate to another route (e.g. /my-skills?id=xxx) */
  onNavigate?: (path: string) => void;
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
  const isCreation = s.kind === 'create' || s.payload.from_version === 0;
  const descriptionValue = isCreation ? s.payload.description : s.payload.new_description;
  const tagsValue = isCreation ? s.payload.tags : s.payload.new_tags;
  const showDescription = s.kind !== 'archive' && (isCreation || s.payload.new_description !== undefined);
  const showTags = s.kind !== 'archive' && (isCreation || s.payload.new_tags !== undefined);
  const showMetadata = showDescription || showTags;
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
            {s.kind === 'archive'
              ? s.payload.reason ?? s.payload.changelog
              : renderChangelog(s.payload.changelog, s.payload.from_version === 0, wrapT(t))}
          </div>
          {showMetadata && (
            <div className="mt-2 rounded-md border border-border-subtle-01 bg-background/50 px-2.5 py-2 text-caption-01">
              <div className="mb-1.5 font-semibold text-text-secondary">
                {t('message.patchMetadataTitle')}
              </div>
              {showDescription && (
                <div>
                  <span className="text-text-helper">
                    {t(isCreation ? 'message.patchMetadataDescription' : 'message.patchMetadataNewDescription')}
                  </span>
                  <div className="mt-0.5 whitespace-pre-wrap break-words font-mono text-text-primary">
                    {descriptionValue || t('message.patchMetadataEmpty')}
                  </div>
                </div>
              )}
              {showTags && (
                <div className={showDescription ? 'mt-1.5' : ''}>
                  <span className="text-text-helper">
                    {t(isCreation ? 'message.patchMetadataTags' : 'message.patchMetadataNewTags')}
                  </span>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {(tagsValue ?? []).length > 0 ? (
                      (tagsValue ?? []).map((tag, index) => (
                        <span
                          key={`${tag}-${index}`}
                          className="max-w-full break-all rounded border border-border-subtle-01 bg-layer-02 px-1.5 py-0.5 font-mono text-text-primary"
                        >
                          {tag}
                        </span>
                      ))
                    ) : (
                      <span className="font-mono text-text-helper">{t('message.patchMetadataNoTags')}</span>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
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
                <Icon name="warning" size={11} />{' '}
                {s.payload.warnings
                  ?.map((warning) => localizeLearningWarning(warning, t))
                  .join(' · ')}
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

// ── 승격 대기(candidate) 카드 — baseline-vs-candidate 평가 게이트(§10.6) 실배선 ──
const CANDIDATE_STATUS_TAG: Record<SkillCandidate['status'], 'gray' | 'blue' | 'green' | 'red' | 'yellow'> = {
  pending: 'blue',
  running: 'blue',
  promoted: 'green',
  discarded: 'yellow',
  expired: 'gray',
  failed: 'red',
};

const pct = (v: number | null | undefined): string =>
  v == null ? '—' : `${Math.round(v * 100)}%`;

const CandidateCard: React.FC<{
  c: SkillCandidate;
  busy: boolean;
  onEvaluate: () => void;
  onDecide: (approved: boolean) => void;
}> = ({ c, busy, onEvaluate, onDecide }) => {
  const { t } = useTranslation('chat');
  // i18next 타입드 키 — 동적 문자열 조회 대신 리터럴 키 사전으로 번역한다.
  const statusLabel: Record<SkillCandidate['status'], string> = {
    pending: t('learning.candidateStatusPending'),
    running: t('learning.candidateStatusRunning'),
    promoted: t('learning.candidateStatusPromoted'),
    discarded: t('learning.candidateStatusDiscarded'),
    expired: t('learning.candidateStatusExpired'),
    failed: t('learning.candidateStatusFailed'),
  };
  const reasonLabel = (r: string): string => {
    switch (r) {
      case 'success_regression':
        return t('learning.candidateReasonSuccess');
      case 'cost_regression':
        return t('learning.candidateReasonCost');
      case 'judge_preference_regression':
        return t('learning.candidateReasonJudge');
      default:
        return r;
    }
  };
  const versionText =
    c.baseline_version === 0
      ? t('learning.suggestionVersionNew')
      : t('learning.suggestionVersionUpgrade', { from: c.baseline_version, to: c.baseline_version + 1 });
  const report = c.report;
  const evidence = report?.evidence;
  // 결정 가능 상태(§10.6): pending/failed 만 수동 결정, running 은 평가 종료 대기.
  const decidable = c.status === 'pending' || c.status === 'failed';
  const evaluable = c.status === 'pending';
  return (
    <div className="bg-layer-01 border border-border-subtle-01 rounded-lg p-3">
      <div className="flex items-start gap-2.5">
        <div className="w-6 h-6 flex-shrink-0 flex items-center justify-center rounded-md bg-layer-02">
          <Icon name="growth" size={14} className="text-link-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="text-body-short-01 font-semibold text-text-primary truncate">{c.skill_id}</span>
            <span className="text-caption-01 text-text-helper font-mono flex-shrink-0">{versionText}</span>
            <span className="ml-auto flex-shrink-0">
              <Tag type={CANDIDATE_STATUS_TAG[c.status]} size="sm">{statusLabel[c.status]}</Tag>
            </span>
          </div>
          <div className="text-caption-01 text-text-helper mt-1 leading-snug">
            {c.status === 'pending' || c.status === 'running'
              ? t('learning.candidateAwaitingEval')
              : null}
            {c.payload.changelog
              ? renderChangelog(c.payload.changelog, c.baseline_version === 0, wrapT(t))
              : null}
          </div>
          {/* 평가 리포트 요약 — 판정(성공률 델타)과 그 판정의 증거 강도(합의율·재생율)
              를 함께 보여준다: promote 라는 결론만큼 결론의 신뢰도도 사용자 정보다. */}
          {report?.baseline && report?.candidate && (
            <div className="mt-2 rounded-md border border-border-subtle-01 bg-background/50 px-2.5 py-2 text-caption-01">
              <div className="font-mono text-text-primary">
                {t('learning.candidateReportLine', {
                  base: pct(report.baseline.success_rate),
                  cand: pct(report.candidate.success_rate),
                })}
                <span className="text-text-helper">
                  {' · '}
                  {t('learning.candidateReportWins', {
                    wins: report.candidate.wins,
                    ties: report.candidate.ties,
                    losses: report.candidate.losses,
                  })}
                </span>
              </div>
              {evidence && (
                <div className="mt-1 text-text-helper">
                  {t('learning.candidateEvidenceLine', {
                    pairings: evidence.pairings ?? 0,
                    agree: pct(evidence.judge_agreement_rate),
                    grounded: pct(evidence.grounded_tool_result_rate),
                  })}
                </div>
              )}
              {(report.reasons?.length ?? 0) > 0 && (
                <div className="mt-1 inline-flex items-center gap-1 text-support-warning">
                  <Icon name="warning" size={11} />
                  {report.reasons!.map(reasonLabel).join(' · ')}
                </div>
              )}
            </div>
          )}
          {c.status === 'failed' && (
            <div className="mt-1.5 inline-flex items-center gap-1 text-caption-01 text-support-warning">
              <Icon name="warning" size={11} /> {t('learning.candidateEvalFailedNote')}
            </div>
          )}
          {(evaluable || decidable) && (
            <div className="flex items-center gap-1.5 mt-2.5">
              {evaluable && (
                <button
                  onClick={onEvaluate}
                  disabled={busy}
                  className="inline-flex items-center gap-1 h-7 px-2.5 rounded-md text-caption-01 font-semibold bg-interactive text-white hover:bg-interactive-hover transition-colors disabled:opacity-50"
                >
                  <Icon name="renew" size={13} className={busy ? 'animate-spin' : ''} />
                  {busy ? t('learning.candidateEvaluateBusy') : t('learning.candidateEvaluate')}
                </button>
              )}
              {decidable && (
                <>
                  <button
                    onClick={() => onDecide(true)}
                    disabled={busy}
                    title={t('learning.candidatePromoteTitle')}
                    className="inline-flex items-center gap-1 h-7 px-2.5 rounded-md text-caption-01 text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary transition-colors disabled:opacity-50"
                  >
                    <Icon name="check" size={13} /> {t('learning.candidatePromote')}
                  </button>
                  <button
                    onClick={() => onDecide(false)}
                    disabled={busy}
                    className="h-7 px-2.5 rounded-md text-caption-01 text-text-secondary hover:bg-layer-hover-01 transition-colors disabled:opacity-50"
                  >
                    {t('learning.candidateDiscard')}
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export const LearningPanel: React.FC<LearningPanelProps> = ({ width = 320, onOpenConversation, onNavigate }) => {
  const { t } = useTranslation('chat');
  const {
    skills,
    setSkills,
    setDetails,
    proposals,
    setProposals,
    suggestions,
    candidates,
    evaluateCandidate,
    decideCandidate,
    decideSuggestion,
    discoverNow,
    myPlugins,
    setMyPlugins,
    highlightedSkill,
    panelScrollRef,
  } = useLearning();
  const { uninstallArtifact, unpublishSkill } = useMarketplace();
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
  // candidate 평가/결정 진행 상태 — 평가는 LLM 다수 호출이라 오래 걸린다.
  const [candidateBusyId, setCandidateBusyId] = useState<string | null>(null);
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

  const handleCandidateEvaluate = async (c: SkillCandidate) => {
    if (candidateBusyId) return;
    setCandidateBusyId(c.id);
    const updated = await evaluateCandidate(c.id);
    setCandidateBusyId(null);
    if (!updated) {
      showFlash(t('learning.candidateFlashEvalFailed'));
      return;
    }
    showFlash(
      updated.status === 'promoted'
        ? t('learning.candidateFlashPromoted', { id: c.skill_id })
        : updated.status === 'discarded'
          ? t('learning.candidateFlashDiscarded', { id: c.skill_id })
          : t('learning.candidateFlashEvalPending'),
    );
  };

  const handleCandidateDecision = async (c: SkillCandidate, approved: boolean) => {
    if (candidateBusyId) return;
    setCandidateBusyId(c.id);
    const ok = await decideCandidate(c.id, approved);
    setCandidateBusyId(null);
    if (!ok) {
      showFlash(t('learning.flashDecideFailed'));
      return;
    }
    showFlash(
      approved
        ? t('learning.candidateFlashPromoted', { id: c.skill_id })
        : t('learning.candidateFlashDiscarded', { id: c.skill_id }),
    );
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
  const removeSkill = async (skill: Skill) => {
    if (skill.shared) {
      const unpublished = await unpublishSkill(publishedMarketId(skill.id));
      if (!unpublished) {
        showFlash(t('learning.skillRemoveFailed'));
        return;
      }
      setSkills((items) =>
        items.map((item) => (item.id === skill.id ? { ...item, shared: false } : item)),
      );
      setDetails((prev) => ({
        ...prev,
        [skill.id]: { ...prev[skill.id], shared: false },
      }));
    }

    const removed = skill.origin === 'installed'
      ? await uninstallArtifact(skill.id, 'skill')
      : (await api.deleteSkill(skill.id)).success;
    if (!removed) {
      showFlash(t('learning.skillRemoveFailed'));
      return;
    }
    setSkills((items) => items.filter((item) => item.id !== skill.id));
    setDetails((prev) => {
      const next = { ...prev };
      delete next[skill.id];
      return next;
    });
  };

  return (
    <div
      className="h-full flex-shrink-0 bg-layer-01 border-l border-border-subtle-01 flex flex-col overflow-hidden"
      style={{ width: `var(--rice-learning-panel-width, ${width}px)` }}
    >
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

        {/* candidate 승격 게이트(§10.6) — 승인됐지만 baseline-vs-candidate 평가를
            아직 통과하지 못한 후보들 + 최근 판정(리포트). 게이트가 꺼져 있으면
            서버가 빈 배열을 주므로 섹션 전체가 숨는다. */}
        {(() => {
          const undecided = candidates.filter((c) =>
            c.status === 'pending' || c.status === 'running' || c.status === 'failed',
          );
          const decided = candidates
            .filter((c) => c.status === 'promoted' || c.status === 'discarded' || c.status === 'expired')
            .slice(0, 3);
          if (undecided.length === 0 && decided.length === 0) return null;
          return (
            <div className="border-b border-border-subtle-01 border-l-2 border-l-interactive/70 bg-interactive/[0.04]">
              <div className="flex items-center gap-2 px-4 pt-3.5 pb-2">
                <Icon name="growth" size={16} className="text-link-primary" />
                <span className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">
                  {t('learning.sectionCandidates')}
                </span>
                <span className="ai-label">
                  <span className="ai-label__text">AI</span>
                </span>
                {undecided.length > 0 && (
                  <span className="ml-auto text-caption-01 text-text-helper font-mono">{undecided.length}</span>
                )}
              </div>
              <div className="px-4 pb-3.5 flex flex-col gap-2">
                {[...undecided, ...decided].map((c) => (
                  <CandidateCard
                    key={c.id}
                    c={c}
                    busy={candidateBusyId === c.id}
                    onEvaluate={() => void handleCandidateEvaluate(c)}
                    onDecide={(approved) => void handleCandidateDecision(c, approved)}
                  />
                ))}
              </div>
            </div>
          );
        })()}

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
                  className="group/skill flex h-[36px] items-center gap-2 border border-transparent bg-layer-01 pl-2.5 pr-1 rounded-lg transition-colors hover:border-border-subtle-01 hover:bg-layer-hover-01"
                >
                  <div className={`flex flex-1 min-w-0 items-center gap-1.5 ${p.enabled ? '' : 'opacity-50'}`}>
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
                      ...(onNavigate
                        ? [
                            {
                              icon: 'arrow-up-right' as IconName,
                              label: t('learning.goToPlugin'),
                              onClick: () => onNavigate(`/my-plugins?id=${encodeURIComponent(p.id)}`),
                            },
                          ]
                        : []),
                      ...(builtin
                        ? []
                        : [
                            {
                              icon: 'trash' as IconName,
                              label: t('learning.skillMenuDelete'),
                              danger: true,
                              onClick: () => {
                                void uninstallArtifact(p.id, 'plugin').then((removed) => {
                                  if (removed) {
                                    setMyPlugins((arr) => arr.filter((x) => x.id !== p.id));
                                  } else {
                                    showFlash(t('learning.pluginRemoveFailed'));
                                  }
                                });
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
                  className={`group/skill flex h-[36px] items-center gap-2 border border-transparent bg-layer-01 pl-2.5 pr-1 rounded-lg transition-colors hover:border-border-subtle-01 hover:bg-layer-hover-01 ${
                    hot ? 'ia-skill-hot outline outline-2 outline-blue-50 -outline-offset-1' : ''
                  }`}
                >
                  <div className={`flex flex-1 min-w-0 items-center gap-1.5 ${s.enabled ? '' : 'opacity-50'}`}>
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
                      ...(onNavigate
                        ? [
                            {
                              icon: 'arrow-up-right' as IconName,
                              label: t('learning.goToSkill'),
                              onClick: () => onNavigate(`/my-skills?id=${encodeURIComponent(s.id)}`),
                            },
                          ]
                        : []),
                      ...(builtin
                        ? []
                        : [
                            {
                              icon: 'trash' as IconName,
                              label: t('learning.skillMenuDelete'),
                              danger: true,
                              onClick: () => void removeSkill(s),
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
