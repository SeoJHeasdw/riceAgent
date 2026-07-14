import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation, Trans } from 'react-i18next';
import type { TFunction } from 'i18next';
import { Button, Icon, Tag, ResizeHandle, Toggle, ConfirmDialog, OverflowMenu, Tabs } from '../components/ui';
import { useResizable } from '../hooks/useResizable';
import { useLearning } from '../lib/learning/LearningContext';
import { useMarketplace } from '../hooks/useMarketplace';
import { useI18n } from '../lib/i18n/I18nContext';
import { LearningImpactPanel } from '../components/carbon/LearningImpactPanel';
import { SkillSourceWorkspace } from '../components/carbon/SkillSourceWorkspace';
import { SkillPublishDialog } from '../components/carbon/SkillPublishDialog';
import { SkillEditDrawer, type EditPatch } from '../components/carbon/SkillEditDrawer';
import { Markdown } from '../components/carbon/Markdown';
import { apiErrorMessage } from '../lib/i18n/apiError';
import { IMPACT_REPORT, runtimeAnalysisToSkillAnalysis, renderTrend, renderCreatedFrom, renderLastUsed, renderVersionWhen, renderVersionNote, wrapT } from '../lib/learningData';
import { ownedSkillFiles, ownedToMarketSkill, publishedMarketId } from '../lib/skillBridge';
import { languageFromPath } from '../lib/skillFiles';
import { api } from '../lib/api';
import { useToast } from '../lib/toast/ToastContext';
import type { RuntimeSkillAnalysis, RuntimeSkillVersion, Skill, SkillAnalysisConfidence, SkillAnalysisToolMention, SkillDetail, SkillOrigin, SkillStep, SkillUpstream, SkillVersion } from '../lib/learningData';
import type { IconName } from '../components/ui';
import type { ConsolidateProposal, ImpactReport } from '../lib/types';

/**
 * MySkillsScreen ("나의 스킬") — full management screen for the user's own learned
 * skills. Master-detail: edit the rules a skill learned, see version history,
 * where it's used, which data it touches, and share to the Skill Market.
 */

// 필터를 3축으로 그룹핑 — 출처(내가 만듦/설치/학습) · 상태(생명주기) · 사용성.
// 출처가 새 직교 축이라, 한 줄로 평평히 두지 않고 섹션 라벨로 묶는다.
type FilterKey =
  | 'all'
  | 'active'
  | 'attention'
  | 'builtin'
  | 'authored'
  | 'installed'
  | 'learned'
  | 'stale'
  | 'archived'
  | 'frequent'
  | 'recent'
  | 'draft'
  | 'shared'
  | 'inactive';

type Filter = FilterKey;
type DetailTab = 'overview' | 'insights' | 'versions';

/** skills 네임스페이스 번역 함수 타입 */
type TSkills = TFunction<'skills'>;

// 생명주기 상태 배지 메타 (LEARNING_LOOP_DESIGN 5.1)
// ── 통합 상태축 — 생명주기(state)와 주입(enabled)을 하나의 사용자 표현으로 합친다.
// active·stale = 주입됨, archived·inactive = 주입 중단. enabled는 ChatScreen·패널이
// 쓰는 "주입 여부"의 진실로 유지하고, UI에서는 여기 단일 status로만 투영한다.
type SkillStatus = 'active' | 'stale' | 'archived' | 'inactive';

const STATUS_TAG: Record<SkillStatus, 'gray' | 'yellow'> = {
  active: 'gray', stale: 'yellow', archived: 'gray', inactive: 'gray',
};
const STATUS_DOT: Record<SkillStatus, string> = {
  active: 'bg-support-success', stale: 'bg-support-warning', archived: 'bg-icon-secondary', inactive: 'bg-icon-secondary',
};
/** 생명주기 상태 (큐레이터 내부용, 미설정 = active) */
const skillState = (s: Skill): NonNullable<Skill['state']> => s.state ?? 'active';

/** 통합 표현 상태 — state(생명주기) + enabled(주입)을 하나로 투영.
 *  archived(자동 중단) > inactive(수동 중단) > stale/active(주입 중) 순으로 결정.
 *  빌트인은 서버 상태가 active|archived 두 값뿐이고 archived가 곧 "수동으로 껐다"는
 *  뜻이라 '보관'이 아닌 '비활성'으로 투영한다. */
const skillStatus = (s: Skill): SkillStatus => {
  const st = skillState(s);
  if (st === 'archived') return s.origin === 'builtin' ? 'inactive' : 'archived';
  if (!s.enabled) return 'inactive';
  return st; // active | stale (둘 다 주입 중)
};
/** 이 상태가 "주입 중"을 의미하는가 */
const isInjected = (st: SkillStatus) => st === 'active' || st === 'stale';

// ── 출처(provenance) — 내가 만든 것 vs 다운받은 것 vs 앱 내장 (git fork 모델) ──
const ORIGIN_ICON: Record<SkillOrigin, IconName> = {
  learned: 'idea', installed: 'download', authored: 'user', builtin: 'sparkle',
};
const ORIGIN_CLS: Record<SkillOrigin, string> = {
  learned: 'text-icon-primary', installed: 'text-link-primary', authored: 'text-purple-60', builtin: 'text-teal-40',
};
/** 현재 출처 반환 (미설정 = learned) */
const skillOrigin = (s: Skill): SkillOrigin => s.origin ?? 'learned';
/** 기본 내장 스킬 — 읽기 전용: 활성/비활성 토글만 가능, 편집·삭제·공유·복원 UI를 숨긴다 */
const isBuiltinSkill = (s: Skill): boolean => skillOrigin(s) === 'builtin';

// 버전 표기에서 정수만 ("v4" → 4) — upstream 발산/업데이트 판단용
const verNum = (v: string): number => {
  const m = v.match(/\d+/);
  return m ? Number(m[0]) : 0;
};
/** 내 개선 건수 = installedVer(fork 지점) 위로 쌓인 내 버전 수 */
const divergenceCount = (versions: SkillVersion[], installedVer: string): number => {
  const base = verNum(installedVer);
  return versions.filter((v) => verNum(v.ver) > base).length;
};
/** 원작자가 더 최신 버전을 올렸는가 (업데이트 가능) */
const hasUpstreamUpdate = (u?: SkillUpstream): boolean =>
  !!u?.latestVer && verNum(u.latestVer) > verNum(u.installedVer);

// 큐레이터 시간 규칙 (LEARNING_LOOP_DESIGN 5.1): 마지막 사용 후 경과일 기준
const STALE_DAYS = 3;
const ARCHIVE_DAYS = 7;

/** idleDays → 규칙상 있어야 할 상태 */
const ruleState = (idleDays: number): NonNullable<Skill['state']> =>
  idleDays >= ARCHIVE_DAYS ? 'archived' : idleDays >= STALE_DAYS ? 'stale' : 'active';

// ── 정적 번역 키 매핑 (동적 키 조합 대신 컴파일 타임에 검증) ──
const STATUS_LABEL_KEYS = {
  active:   'mySkills.statusActive',
  stale:    'mySkills.statusStale',
  archived: 'mySkills.statusArchived',
  inactive: 'mySkills.statusInactive',
} as const satisfies Record<SkillStatus, `mySkills.status${'Active'|'Stale'|'Archived'|'Inactive'}`>;

const STATUS_DESC_KEYS = {
  active:   'mySkills.statusActiveDesc',
  stale:    'mySkills.statusStaleDesc',
  archived: 'mySkills.statusArchivedDesc',
  inactive: 'mySkills.statusInactiveDesc',
} as const satisfies Record<SkillStatus, `mySkills.status${'Active'|'Stale'|'Archived'|'Inactive'}Desc`>;

const ORIGIN_LABEL_KEYS = {
  learned:   'mySkills.originLearned',
  installed: 'mySkills.originInstalled',
  authored:  'mySkills.originAuthored',
  builtin:   'mySkills.originBuiltin',
} as const satisfies Record<SkillOrigin, `mySkills.origin${'Learned'|'Installed'|'Authored'|'Builtin'}`>;

const CONFIDENCE_LABEL_KEYS = {
  high:   'mySkills.confidenceHigh',
  medium: 'mySkills.confidenceMedium',
  low:    'mySkills.confidenceLow',
} as const satisfies Record<SkillAnalysisConfidence, `mySkills.confidence${'High'|'Medium'|'Low'}`>;

const TOOL_KIND_LABEL_KEYS = {
  observed: 'mySkills.toolKindObserved',
  declared: 'mySkills.toolKindDeclared',
  mentioned: 'mySkills.toolKindMentioned',
} as const satisfies Record<'observed'|'declared'|'mentioned', `mySkills.toolKind${'Observed'|'Declared'|'Mentioned'}`>;

/** 배지·버튼 툴팁에 붙는 "왜 이 상태인지" 사유 — t() 를 받아야 하므로 factory */
const makeStatusReason = (t: TSkills) =>
  (s: Skill): string => {
    const idle = s.idleDays ?? 0;
    switch (skillStatus(s)) {
      case 'stale':    return t('mySkills.statusReasonStale', { idle });
      case 'archived': return t('mySkills.statusReasonArchived', { idle });
      case 'inactive': return t('mySkills.statusReasonInactive');
      default:         return idle > 0
        ? t('mySkills.statusReasonActiveIdle', { idle })
        : t('mySkills.statusReasonActiveOk');
    }
  };

// 초안 = 갓 생성돼 아직 안 다듬어진 스킬 (v1 · 사용 적음)
const isDraft = (s: Skill) => s.ver === 'v1' && s.uses < 5;

// 상태 배지 — active 만 생략(기본값), stale·보관·비활성은 표시
const StatusBadge: React.FC<{ s: Skill }> = ({ s }) => {
  const { t } = useTranslation('skills');
  const statusReason = makeStatusReason(t);
  const st = skillStatus(s);
  if (st === 'active') return null;
  return (
    <span title={statusReason(s)}>
      <Tag type={STATUS_TAG[st]} size="sm">
        {t(STATUS_LABEL_KEYS[st])}
      </Tag>
    </span>
  );
};

// 목록 행의 출처 글리프 — 내가 만든 것/설치한 것을 한눈에 구분 (학습=💡 설치=⬇ 제작=👤)
const OriginGlyph: React.FC<{ s: Skill }> = ({ s }) => {
  const { t } = useTranslation('skills');
  const origin = skillOrigin(s);
  return (
    <span title={t('mySkills.originGlyphTitle', { label: t(ORIGIN_LABEL_KEYS[origin]) })} className="flex-shrink-0">
      <Icon name={ORIGIN_ICON[origin]} size={11} className={`${ORIGIN_CLS[origin]} opacity-80`} />
    </span>
  );
};

// 상세 헤더의 단일 상태 컨트롤 — 활성/Stale/보관/비활성을 한 곳에서.
// (기존 "상태 배지 + 주입 토글" 두 컨트롤을 하나로 합침.) 수동 설정 가능한 건
// 활성·비활성·보관 3가지(Stale은 큐레이터가 자동 부여).
const SETTABLE_STATUSES: SkillStatus[] = ['active', 'inactive', 'archived'];
const StatusControl: React.FC<{
  skill: Skill;
  onSet: (next: SkillStatus) => void;
}> = ({ skill, onSet }) => {
  const { t } = useTranslation('skills');
  const statusReason = makeStatusReason(t);
  const [open, setOpen] = useState(false);
  const ref = React.useRef<HTMLDivElement>(null);
  const st = skillStatus(skill);

  // 외부 클릭 닫기
  React.useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, [open]);

  const targets = SETTABLE_STATUSES.filter((tgt) => tgt !== st);

  return (
    <div ref={ref} className="relative flex-shrink-0">
      <button
        onClick={() => setOpen((o) => !o)}
        title={statusReason(skill)}
        className={`inline-flex items-center gap-1.5 text-caption-01 font-semibold border rounded-md px-2 py-1 transition-colors hover:bg-layer-hover-01 ${
          st === 'stale'    ? 'border-support-warning/50 text-support-warning'
          : st === 'active' ? 'border-support-success/50 text-support-success'
          : 'border-border-strong-01 text-text-helper'
        }`}
      >
        <span className={`w-1.5 h-1.5 rounded-full ${STATUS_DOT[st]}`} />
        {t(STATUS_LABEL_KEYS[st])}
        <Icon name="chevron-down" size={11} className="opacity-60" />
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 z-50 min-w-[208px] bg-background border border-border-subtle-01 rounded-lg shadow-elevation-2 overflow-hidden">
          <div className="px-3 py-2 text-caption-01 text-text-helper border-b border-border-subtle-01">
            {t('mySkills.statusChangeHint')}
          </div>
          {targets.map((tgt) => {
            return (
              <button
                key={tgt}
                onClick={() => { onSet(tgt); setOpen(false); }}
                className="flex items-start gap-2.5 w-full text-left px-3 py-2 hover:bg-layer-hover-01 transition-colors"
              >
                <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 mt-1.5 ${STATUS_DOT[tgt]}`} />
                <span className="min-w-0">
                  <span className="block text-body-short-01 text-text-primary">{t(STATUS_LABEL_KEYS[tgt])}</span>
                  <span className="block text-caption-01 text-text-helper">{t(STATUS_DESC_KEYS[tgt])}</span>
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};

// 빌트인 스킬의 단일 컨트롤 — 활성/비활성 토글 하나뿐이다(요구사항: 그 외 전이·편집 불가).
// 서버 상태는 active|archived 두 값으로 영속되고, UI 표현은 활성/비활성으로 통일한다.
const BuiltinStatusToggle: React.FC<{
  skill: Skill;
  onSet: (next: SkillStatus) => void;
}> = ({ skill, onSet }) => {
  const { t } = useTranslation('skills');
  const on = skillState(skill) === 'active';
  return (
    <span
      className="inline-flex items-center gap-2 border border-border-subtle-01 rounded-md pl-2.5 pr-1.5 py-1"
      title={on ? t('mySkills.builtinToggleOnTitle') : t('mySkills.builtinToggleOffTitle')}
    >
      <span className={`text-caption-01 font-semibold ${on ? 'text-support-success' : 'text-text-helper'}`}>
        {on ? t('mySkills.builtinActiveLabel') : t('mySkills.builtinInactiveLabel')}
      </span>
      <Toggle
        checked={on}
        onChange={() => onSet(on ? 'archived' : 'active')}
        aria-label={on ? t('mySkills.builtinToggleAriaDisable', { name: skill.name }) : t('mySkills.builtinToggleAriaEnable', { name: skill.name })}
      />
    </span>
  );
};

const LibraryManageMenu: React.FC<{
  impactActive: boolean;
  busy: boolean;
  proposalCount: number;
  onImpact: () => void;
  onCurate: () => void;
  onConsolidate: () => void;
}> = ({ impactActive, busy, proposalCount, onImpact, onCurate, onConsolidate }) => {
  const { t } = useTranslation('skills');
  const [open, setOpen] = useState(false);
  const ref = React.useRef<HTMLDivElement>(null);
  const triggerRef = React.useRef<HTMLButtonElement>(null);
  const itemRefs = React.useRef<Array<HTMLButtonElement | null>>([]);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    const frame = requestAnimationFrame(() => itemRefs.current.find((item) => item && !item.disabled)?.focus());
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const choose = (action: () => void) => {
    setOpen(false);
    triggerRef.current?.focus();
    action();
  };

  return (
    <div ref={ref} className="relative flex-shrink-0">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-label={t('mySkills.manageAriaLabel')}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-pressed={impactActive}
        className="inline-flex items-center gap-1 text-caption-01 text-link-primary hover:underline"
      >
        <Icon name="settings" size={13} />
        {t('mySkills.manageLabel')}
        {proposalCount > 0 && (
          <span className="font-mono text-label-micro no-underline tabular-nums">
            {proposalCount}
          </span>
        )}
      </button>
      {open && (
        <div
          role="menu"
          onKeyDown={(event) => {
            const enabled = itemRefs.current.filter((item): item is HTMLButtonElement => !!item && !item.disabled);
            const current = enabled.indexOf(document.activeElement as HTMLButtonElement);
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
              event.preventDefault();
              const delta = event.key === 'ArrowDown' ? 1 : -1;
              enabled[(current + delta + enabled.length) % enabled.length]?.focus();
            } else if (event.key === 'Tab') {
              setOpen(false);
            }
          }}
          className="absolute right-0 top-full z-50 mt-1 min-w-[190px] rounded-lg border border-border-subtle-01 bg-layer-02 py-1 shadow-elevation-2"
        >
          <button
            ref={(node) => { itemRefs.current[0] = node; }}
            role="menuitem"
            tabIndex={-1}
            onClick={() => choose(onImpact)}
            className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-caption-01 text-text-primary transition-colors hover:bg-layer-hover-01"
          >
            <Icon name="growth" size={12} className="flex-shrink-0" />
            {t('mySkills.libraryImpactTitle')}
          </button>
          <button
            ref={(node) => { itemRefs.current[1] = node; }}
            role="menuitem"
            tabIndex={-1}
            disabled={busy}
            onClick={() => choose(onCurate)}
            className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-caption-01 text-text-primary transition-colors hover:bg-layer-hover-01 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Icon name="renew" size={12} className="flex-shrink-0" />
            {t('mySkills.curateBtn')}
          </button>
          <button
            ref={(node) => { itemRefs.current[2] = node; }}
            role="menuitem"
            tabIndex={-1}
            disabled={busy}
            onClick={() => choose(onConsolidate)}
            className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-caption-01 text-text-primary transition-colors hover:bg-layer-hover-01 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Icon name="merge" size={12} className="flex-shrink-0" />
            {t('mySkills.consolidateBtn')}
            {proposalCount > 0 && <span className="ml-auto font-mono text-label-micro text-link-primary">{proposalCount}</span>}
          </button>
        </div>
      )}
    </div>
  );
};

const SectionLabel: React.FC<{ children: React.ReactNode; right?: React.ReactNode }> = ({ children, right }) => (
  <div className="flex items-center mb-2.5">
    <span className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">{children}</span>
    {right && <span className="ml-auto">{right}</span>}
  </div>
);

// ── 정보 텍스처 헬퍼 ───────────────────────────────────────────────
const pctStr = (n: number) => `${Math.round(n * 100)}%`;

const CONFIDENCE_CLS: Record<SkillAnalysisConfidence, string> = {
  high: 'text-support-success',
  medium: 'text-text-secondary',
  low: 'text-support-warning',
};

const ConfidencePill: React.FC<{ value?: SkillAnalysisConfidence }> = ({ value = 'low' }) => {
  const { t } = useTranslation('skills');
  return (
    <span className={`inline-flex items-center gap-1 text-caption-01 font-semibold ${CONFIDENCE_CLS[value]}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {t(CONFIDENCE_LABEL_KEYS[value])}
    </span>
  );
};

type ResolvedAnalysisStep = {
  title: string;
  description?: string;
  confidence: SkillAnalysisConfidence;
  evidence?: string;
};

type ResolvedToolSignal = SkillAnalysisToolMention & {
  confidence: SkillAnalysisConfidence;
};

type ResolvedUnderstanding = {
  summary: string;
  sourceLabel: string;
  statusLabel: string;
  confidence: SkillAnalysisConfidence;
  analyzedAt?: string;
  stale: boolean;
  steps: ResolvedAnalysisStep[];
  tools: ResolvedToolSignal[];
  warnings: string[];
};

type RuntimeSkillDetailPayload = {
  name: string;
  version: number;
  description: string;
  body?: string;
  tags?: string[];
  source?: string | null;
  files?: { path: string; content: string }[];
  analysis?: RuntimeSkillAnalysis | null;
};

const stripFrontmatter = (content: string): string =>
  content.replace(/^---\s*[\s\S]*?\n---\s*/m, '').trim();

/** SKILL.md frontmatter에서 description/tags 추출 — AI 편집이 메타데이터를 고친
 *  경우 body와 함께 저장해 유실을 막는다(stripFrontmatter만 하면 조용히 버려짐).
 *  tags는 런타임 직렬화의 YAML 블록 리스트(- a)와 프론트 합성본의 인라인([a, b])
 *  둘 다 지원하고, description의 YAML 따옴표는 벗긴다. */
const parseFrontmatterMeta = (content: string): { description?: string; tags?: string[] } => {
  const fm = /^---\n([\s\S]*?)\n---/.exec(content)?.[1];
  if (!fm) return {};
  const description = /^description:\s*(.+?)\s*$/m
    .exec(fm)?.[1]
    ?.replace(/^(['"])(.*)\1$/, '$2');
  const inline = /^tags:\s*\[([^\]]*)\]/m.exec(fm)?.[1];
  const block = /^tags:\s*\n((?:\s*-\s*.+\n?)+)/m.exec(fm)?.[1];
  const tags =
    inline !== undefined
      ? inline.split(',').map((t) => t.trim()).filter(Boolean)
      : block
          ?.split('\n')
          .map((l) => l.replace(/^\s*-\s*/, '').trim())
          .filter(Boolean);
  return { description, tags };
};

const runtimeSkillSource = (data: RuntimeSkillDetailPayload): string => {
  const frontmatter = [
    `name: ${data.name}`,
    `version: ${data.version}`,
    `description: ${data.description}`,
    ...(data.tags?.length ? [`tags: [${data.tags.join(', ')}]`] : []),
    ...(data.source ? [`source: ${data.source}`] : []),
  ].join('\n');
  return `---\n${frontmatter}\n---\n\n${data.body ?? ''}`;
};

const versionWhen = (value: string, tFn: TSkills, locale: string): string => {
  if (!value) return tFn('mySkills.versionWhenNone');
  const parsed = new Date(value);
  const localeTag = locale === 'ko' ? 'ko-KR' : 'en-US';
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString(localeTag);
};

const runtimeVersionList = (
  current: RuntimeSkillDetailPayload,
  history: RuntimeSkillVersion[],
  tFn: TSkills,
  locale: string,
): SkillVersion[] => [
  {
    ver: `v${current.version}`,
    when: tFn('mySkills.versionCurrentApplied'),
    note: tFn('mySkills.versionCurrentNote'),
    current: true,
    body: runtimeSkillSource(current),
  },
  ...history
    .filter((entry) => entry.version !== current.version)
    .sort((a, b) => b.version - a.version)
    .map((entry): SkillVersion => ({
      ver: `v${entry.version}`,
      when: versionWhen(entry.updated_at, tFn, locale),
      note: tFn('mySkills.versionArchivedNote', { ver: entry.version }),
      current: false,
      body: entry.body,
    })),
];

const cleanMdLine = (line: string): string =>
  line
    .replace(/^\s{0,3}(?:[-*+]\s+|\d+[.)]\s+)/, '')
    .replace(/[`*_>#]/g, '')
    .replace(/\s+/g, ' ')
    .trim();

const sentenceForTitle = (text: string): string => {
  const cleaned = cleanMdLine(text);
  const [first] = cleaned.split(/[.!?。]/);
  return (first || cleaned).slice(0, 54);
};

const findMarkdownSection = (body: string, headingPattern: RegExp): string[] => {
  const lines = body.split('\n');
  const start = lines.findIndex((line) => /^#{2,4}\s+/.test(line) && headingPattern.test(line));
  if (start === -1) return [];
  const out: string[] = [];
  for (let i = start + 1; i < lines.length; i += 1) {
    if (/^#{2,4}\s+/.test(lines[i])) break;
    out.push(lines[i]);
  }
  return out;
};

const stepCandidatesFromBody = (
  body: string,
  tFn: TSkills,
): ResolvedAnalysisStep[] => {
  const section = findMarkdownSection(body, /(workflow|how it works|steps|procedure|절차|작동 방식|사용 방법|흐름)/i);
  const source = section.length ? section : body.split('\n');
  return source
    .filter((line) => /^\s*(?:[-*+]\s+|\d+[.)]\s+)/.test(line))
    .map((line): ResolvedAnalysisStep => ({
      title: sentenceForTitle(line),
      description: section.length
        ? tFn('mySkills.analysisStepDescSection')
        : tFn('mySkills.analysisStepDescBody'),
      confidence: section.length ? 'medium' : 'low',
      evidence: cleanMdLine(line),
    }))
    .filter((s) => s.title.length > 0)
    .slice(0, 6);
};

// 본문 도구 섹션의 항목 — 출처 분리 원칙상 body 에서 온 건 전부 'mentioned'(언급
// 후보)다. declared 는 frontmatter(tools:)/manifest 선언만이 정본이고 그건 서버
// 분석 payload 로만 온다 (learning/skill/analysis.py 계약).
const toolNamesFromSection = (body: string, tFn: TSkills): ResolvedToolSignal[] =>
  findMarkdownSection(body, /(tools|tooling|사용 도구|도구)/i)
    .filter((line) => /^\s*(?:[-*+]\s+|\d+[.)]\s+)/.test(line))
    .map((line): ResolvedToolSignal => ({
      name: sentenceForTitle(line),
      kind: 'mentioned',
      confidence: 'medium',
      basis: tFn('mySkills.analysisBasisSection'),
      evidence: cleanMdLine(line),
    }))
    .filter((t) => t.name.length > 0)
    .slice(0, 8);

const KNOWN_TOOL_PATTERNS: { name: string; pattern: RegExp }[] = [
  { name: 'terminal', pattern: /\b(terminal|shell|bash|zsh|command line|CLI|npm|pnpm|yarn|pytest|git|rg|grep)\b/i },
  { name: 'file tools', pattern: /\b(read_file|write_file|edit_file|filesystem|file|workspace)\b/i },
  { name: 'web/search', pattern: /\b(web search|browser|url|http|docs|검색|브라우저)\b/i },
  { name: 'database', pattern: /\b(sql|db2|postgres|mysql|database|schema|query)\b/i },
  { name: 'python', pattern: /\b(python|pandas|notebook|script\.py)\b/i },
  { name: 'connector', pattern: /\b(gmail|calendar|drive|slack|salesforce|connector|oauth|api)\b/i },
];

const mentionedToolsFromBody = (body: string, tFn: TSkills): ResolvedToolSignal[] =>
  KNOWN_TOOL_PATTERNS
    .filter((t) => t.pattern.test(body))
    .map((t): ResolvedToolSignal => ({
      name: t.name,
      kind: 'mentioned',
      confidence: 'low',
      basis: tFn('mySkills.analysisBasisKeyword'),
    }));

const dedupeTools = (tools: ResolvedToolSignal[]): ResolvedToolSignal[] => {
  const seen = new Set<string>();
  const out: ResolvedToolSignal[] = [];
  for (const tool of tools) {
    const key = `${tool.kind}:${tool.name.toLowerCase()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(tool);
  }
  return out;
};

const skillMdBody = (skill: Skill, detail: SkillDetail): string => {
  const { files } = ownedSkillFiles(skill, detail);
  const skillMd = files.find((f) => f.path.toLowerCase().endsWith('skill.md')) ?? files[0];
  return stripFrontmatter(skillMd?.content ?? '');
};

const resolveUnderstanding = (
  skill: Skill,
  detail: SkillDetail,
  tFn: TSkills,
): ResolvedUnderstanding => {
  const body = skillMdBody(skill, detail);
  const analysis = detail.analysis;
  // 사이드카가 실재하면(missing 이 아니면) 서버 분석이 정본 — stale 이어도 클라이언트
  // 휴리스틱보다 낫다(같은 휴리스틱 + LLM 층 결과이므로). missing/오프라인일 때만
  // 원문 기반 미리보기 휴리스틱으로 폴백한다.
  const hasAnalysis = !!analysis && analysis.status !== 'missing';
  const analysisSteps = (analysis?.proceduralSteps ?? []).map((s): ResolvedAnalysisStep => ({
    title: s.title,
    description: s.description,
    confidence: s.confidence ?? 'medium',
    evidence: s.evidence,
  }));
  const workflowSteps = (detail.steps ?? []).map((s: SkillStep): ResolvedAnalysisStep => ({
    title: s.label,
    description: s.desc,
    confidence: 'high',
    evidence: s.tool,
  }));
  const steps = analysisSteps.length
    ? analysisSteps
    : workflowSteps.length
      ? workflowSteps
      : stepCandidatesFromBody(body, tFn);

  // 도구 단서: 서버 payload 는 이미 observed(실행 로그)/declared(frontmatter 선언)/
  // mentioned(본문 감지) 세 출처가 합류된 결과라 그대로 신뢰한다. 사이드카가 없을
  // 때만 클라이언트가 본문에서 mentioned 후보를 임시로 긁는다.
  const serverTools = (analysis?.toolMentions ?? []).map((t): ResolvedToolSignal => ({
    ...t,
    confidence: t.confidence ?? (t.kind === 'mentioned' ? 'low' : 'high'),
  }));
  const tools = hasAnalysis
    ? dedupeTools(serverTools)
    : dedupeTools([...toolNamesFromSection(body, tFn), ...mentionedToolsFromBody(body, tFn)]);

  const sourceLabel = !hasAnalysis
    ? tFn('mySkills.analysisSourcePreview')
    : analysis?.source === 'llm'
      ? tFn('mySkills.analysisSourceLlm')
      : tFn('mySkills.analysisSourceHeuristic');

  const statusLabel = !hasAnalysis
    ? tFn('mySkills.analysisStatusPending')
    : analysis!.status === 'fresh'
      ? tFn('mySkills.analysisStatusFresh')
      : analysis!.status === 'stale'
        ? tFn('mySkills.analysisStatusStale')
        : tFn('mySkills.analysisStatusFailed');

  return {
    summary: analysis?.summary?.trim() || detail.summary,
    sourceLabel,
    statusLabel,
    confidence: analysis?.confidence ?? (hasAnalysis ? 'medium' : 'low'),
    analyzedAt: analysis?.analyzedAt,
    stale: !analysis || analysis.status !== 'fresh',
    steps,
    tools,
    warnings: analysis?.warnings ?? [],
  };
};

const TOOL_KIND_ICON: Record<ResolvedToolSignal['kind'], IconName> = {
  observed: 'check-circle', declared: 'rule', mentioned: 'search',
};
const TOOL_KIND_CLS: Record<ResolvedToolSignal['kind'], string> = {
  observed: 'text-support-success', declared: 'text-link-primary', mentioned: 'text-support-warning',
};

// next version label, e.g. v3 → v4
const bumpVer = (v: string) => {
  const m = v.match(/\d+/);
  return m ? v.replace(/\d+/, String(Number(m[0]) + 1)) : `${v}.1`;
};

// 규칙 텍스트 정규화 — 버전 간 diff 비교용
const normRule = (t: string) => t.trim();

// 두 지표값을 "이 버전 vs 현재"로 비교 — better 방향(higher/lower)에 따라 색을 정한다.
const compareTone = (view?: number, cur?: number, lowerBetter = false): 'good' | 'bad' | 'neutral' => {
  if (view == null || cur == null || view === cur) return 'neutral';
  const viewBetter = lowerBetter ? view < cur : view > cur;
  return viewBetter ? 'good' : 'bad';
};
const TONE_CLS = { good: 'text-support-success', bad: 'text-support-error', neutral: 'text-text-primary' } as const;

// 복원 판단 카드의 지표 한 줄 — 이 버전 값 vs 현재 값
const CompareRow: React.FC<{ label: string; view: string; cur: string; tone: 'good' | 'bad' | 'neutral'; curLabel: string }> = ({ label, view, cur, tone, curLabel }) => (
  <div className="flex items-center gap-2 py-1.5">
    <span className="text-caption-01 text-text-helper w-20 flex-shrink-0">{label}</span>
    <span className={`text-body-short-01 font-mono font-semibold ${TONE_CLS[tone]}`}>{view}</span>
    <Icon name="arrow-right" size={12} className="text-icon-secondary opacity-40" />
    <span className="text-body-short-01 font-mono text-text-helper">{cur} <span className="text-caption-01">{curLabel}</span></span>
  </div>
);

// read-only snapshot of one version's rule set, with restore decision support.
// Reached from the ver chip in the header or a version-history row.
const VersionSnapshot: React.FC<{
  skillName: string;
  version: SkillVersion;
  currentVersion?: SkillVersion;
  onBack: () => void;
  onRestore: () => void;
}> = ({ skillName, version, currentVersion, onBack, onRestore }) => {
  const { t } = useTranslation('skills');
  const { t: tChatRaw, i18n } = useTranslation('chat');
  const tChat = wrapT(tChatRaw);
  const rules = version.rules ?? [];
  const added = rules.filter((r) => r.added).length;
  const m = version.metrics;
  const hasRuleComparison = rules.length > 0 || (currentVersion?.rules?.length ?? 0) > 0;

  // ── 복원 판단 근거 (현재 버전이 아닐 때만) ──
  const decision = (() => {
    if (version.current || !currentVersion) return null;
    if (!hasRuleComparison && !(m && currentVersion.metrics)) return null;
    const curTexts = new Set((currentVersion.rules ?? []).map((r) => normRule(r.text)));
    const viewTexts = new Set(rules.map((r) => normRule(r.text)));
    const gain = rules.filter((r) => !curTexts.has(normRule(r.text)));
    const lose = (currentVersion.rules ?? []).filter((r) => !viewTexts.has(normRule(r.text)));
    const cM = currentVersion.metrics;
    let verdict: 'restore' | 'caution' | 'neutral' = 'neutral';
    if (m && cM) {
      if (m.successRate >= cM.successRate + 0.02) verdict = 'restore';
      else if (m.successRate <= cM.successRate - 0.02) verdict = 'caution';
    }
    return { gain, lose, cM, verdict };
  })();

  const VERDICT_META = {
    restore: {
      cls: 'border-support-success/40 bg-support-success/10',
      icon: 'growth' as IconName,
      iconCls: 'text-support-success',
      title: t('mySkills.verdictRestoreTitle', { ver: currentVersion?.ver }),
      body: t('mySkills.verdictRestoreBody'),
    },
    caution: {
      cls: 'border-support-warning/40 bg-support-warning/10',
      icon: 'warning' as IconName,
      iconCls: 'text-support-warning',
      title: t('mySkills.verdictCautionTitle', { ver: currentVersion?.ver }),
      body: t('mySkills.verdictCautionBody'),
    },
    neutral: {
      cls: 'border-border-subtle-01 bg-layer-01',
      icon: 'idea' as IconName,
      iconCls: 'text-icon-primary',
      title: t('mySkills.verdictNeutralTitle'),
      body: t('mySkills.verdictNeutralBody'),
    },
  } as const;

  return (
    <div className="flex-1 min-w-0 overflow-y-auto">
      {/* breadcrumb */}
      <div className="flex items-center gap-1.5 px-7 py-3 border-b border-border-subtle-01 bg-layer-01">
        <button
          onClick={onBack}
          className="inline-flex items-center gap-1.5 text-body-short-01 text-text-secondary hover:text-text-primary flex-shrink-0"
        >
          <Icon name="arrow-right" size={16} className="rotate-180" /> {skillName}
        </button>
        <Icon name="arrow-right" size={13} className="text-icon-secondary opacity-40 flex-shrink-0" />
        <span className="text-body-short-01 font-mono text-text-primary">{version.ver}</span>
      </div>

      <div className="px-7 pt-6 pb-10">
        <div className="flex items-center gap-2.5 flex-wrap">
          <h2 className="text-heading-03 font-light font-mono text-text-primary">{version.ver}</h2>
          {version.current
            ? <Tag type="blue" size="sm">{t('mySkills.versionCurrentTagFull')}</Tag>
            : <Tag type="gray" size="sm">{t('mySkills.versionPreviousTag')}</Tag>}
          <span className="text-caption-01 text-text-helper">{renderVersionWhen(version.when, tChat, i18n.language)}</span>
          {version.author && (
            <span className="inline-flex items-center gap-1 text-caption-01 text-text-helper">
              <Icon name="user" size={11} /> {version.author}
            </span>
          )}
          {!version.current && (
            <Button
              kind="secondary"
              size="xs"
              className="ml-auto"
              icon={<Icon name="renew" size={12} />}
              onClick={onRestore}
              title={t('mySkills.versionRestoreTitle')}
            >
              {t('mySkills.versionRestoreBtn2', { ver: version.ver })}
            </Button>
          )}
        </div>
        <p className="mt-2 text-body-short-01 leading-5 text-text-secondary">{renderVersionNote(version.note, tChat, version.noteParams)}</p>

        {/* 이 버전의 성과 지표 칩 */}
        {m && (
          <div className="flex gap-4 mt-4 flex-wrap">
            {([
              [t('mySkills.metricSuccessRate'), pctStr(m.successRate)],
              [t('mySkills.metricUses'), t('mySkills.metricUsesUnit', { value: m.uses })],
              ...(m.avgTurns != null ? [[t('mySkills.metricAvgTurns'), t('mySkills.metricTurnsUnit', { value: m.avgTurns })]] : []),
              ...(m.editRate != null ? [[t('mySkills.metricEditRate'), pctStr(m.editRate)]] : []),
            ]).map(([k, v]) => (
              <div key={k}>
                <div className="text-caption-01 text-text-helper">{k}</div>
                <div className="text-body-short-01 font-mono text-text-primary">{v}</div>
              </div>
            ))}
          </div>
        )}

        {/* ── 복원 판단 카드 ── */}
        {decision && (
          <div className={`mt-6 rounded-xl border p-4 ${VERDICT_META[decision.verdict].cls}`}>
            <div className="flex items-start gap-2.5">
              <Icon name={VERDICT_META[decision.verdict].icon} size={18} className={`flex-shrink-0 mt-0.5 ${VERDICT_META[decision.verdict].iconCls}`} />
              <div className="flex-1 min-w-0">
                <div className="text-body-short-01 font-semibold text-text-primary">{VERDICT_META[decision.verdict].title}</div>
                <div className="text-caption-01 text-text-secondary mt-0.5 leading-4">{VERDICT_META[decision.verdict].body}</div>
              </div>
            </div>

            {/* 지표 대비 */}
            {m && decision.cM && (
              <div className="mt-3 pt-3 border-t border-border-subtle-01/60">
                <CompareRow label={t('mySkills.metricSuccessRate')} view={pctStr(m.successRate)} cur={pctStr(decision.cM.successRate)} tone={compareTone(m.successRate, decision.cM.successRate)} curLabel={t('mySkills.metricCurrentLabel')} />
                {m.avgTurns != null && decision.cM.avgTurns != null && (
                  <CompareRow label={t('mySkills.metricAvgTurns')} view={t('mySkills.metricTurnsUnit', { value: m.avgTurns })} cur={t('mySkills.metricTurnsUnit', { value: decision.cM.avgTurns })} tone={compareTone(m.avgTurns, decision.cM.avgTurns, true)} curLabel={t('mySkills.metricCurrentLabel')} />
                )}
                {m.editRate != null && decision.cM.editRate != null && (
                  <CompareRow label={t('mySkills.metricEditRate')} view={pctStr(m.editRate)} cur={pctStr(decision.cM.editRate)} tone={compareTone(m.editRate, decision.cM.editRate, true)} curLabel={t('mySkills.metricCurrentLabel')} />
                )}
              </div>
            )}

            {/* 복원 시 규칙 변화 */}
            <div className="mt-3 pt-3 border-t border-border-subtle-01/60 flex flex-col gap-2">
              {decision.lose.length === 0 && decision.gain.length === 0 ? (
                <div className="text-caption-01 text-text-helper">{t('mySkills.versionSnapshotRulesSame')}</div>
              ) : (
                <>
                  {decision.lose.map((r, i) => (
                    <div key={`lose-${i}`} className="flex items-center gap-2 text-body-short-01">
                      <Icon name="close" size={13} className="text-support-error flex-shrink-0" />
                      <span className="text-text-secondary line-through decoration-support-error/50">{r.text}</span>
                      <span className="text-caption-01 text-support-error flex-shrink-0 ml-auto">{t('mySkills.versionSnapshotRuleLose')}</span>
                    </div>
                  ))}
                  {decision.gain.map((r, i) => (
                    <div key={`gain-${i}`} className="flex items-center gap-2 text-body-short-01">
                      <Icon name="add" size={13} className="text-support-success flex-shrink-0" />
                      <span className="text-text-primary">{r.text}</span>
                      <span className="text-caption-01 text-support-success flex-shrink-0 ml-auto">{t('mySkills.versionSnapshotRuleGain')}</span>
                    </div>
                  ))}
                </>
              )}
            </div>
          </div>
        )}

        {version.body ? (
          <div className="mt-6">
            <div className="mb-2.5 flex items-center gap-2">
              <span className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">
                {t('mySkills.versionSnapshotSectionTitle')}
              </span>
              <span className="text-caption-01 text-text-helper">{t('mySkills.versionSnapshotReadOnly')}</span>
            </div>
            <div className="max-w-[920px] overflow-x-auto rounded-lg border border-border-subtle-01 bg-background px-4 py-3 [&_h1]:!mb-2 [&_h1]:!text-heading-03 [&_h2]:!mb-1.5 [&_h2]:!mt-4 [&_h2]:!text-body-short-02 [&_h3]:!mt-3 [&_h3]:!text-body-short-01 [&_li]:!text-caption-01 [&_p]:!text-caption-01">
              <Markdown source={version.body} />
            </div>
          </div>
        ) : (
          <div className="mt-6">
            <div className="flex items-center mb-2.5">
              <span className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">
                {t('mySkills.versionSnapshotRulesTitle', { count: rules.length })}
              </span>
              {added > 0 && <span className="ml-auto text-caption-01 text-link-primary">{t('mySkills.versionSnapshotRulesAdded', { count: added })}</span>}
            </div>
            <div className="flex flex-col gap-px bg-border-subtle-01 rounded-lg overflow-hidden">
              {rules.map((r, i) => (
                <div
                  key={i}
                  className={`flex items-start gap-2.5 bg-background px-3.5 py-2.5 border-l-2 ${
                    r.added ? 'border-l-blue-50' : 'border-l-transparent'
                  }`}
                >
                  <Icon
                    name={r.added ? 'add' : 'check'}
                    size={15}
                    className={`mt-0.5 flex-shrink-0 ${r.added ? 'text-link-primary' : 'text-icon-secondary opacity-55'}`}
                  />
                  <span className="flex-1 min-w-0 text-body-short-01 text-text-primary leading-[18px]">{r.text}</span>
                  {r.added && <Tag type="blue" size="sm">{t('mySkills.versionSnapshotRuleAdded')}</Tag>}
                </div>
              ))}
              {rules.length === 0 && (
                <div className="bg-background px-3.5 py-3.5 text-caption-01 text-text-helper">{t('mySkills.versionSnapshotNoRules')}</div>
              )}
            </div>
          </div>
        )}

        {version.current && (
          <div className="mt-5 pt-3 border-t border-border-subtle-01 flex items-center gap-1.5 text-caption-01 text-text-helper">
            <Icon name="check-circle" size={14} className="text-support-success" /> {t('mySkills.versionCurrentAppliedNote')}
          </div>
        )}
      </div>
    </div>
  );
};

// 상세 헤더의 출처 스트립 — 설치형은 원작자·fork 지점·내 개선·업데이트를, 제작형은 "직접 제작"을 보여준다.
// 학습형(default)은 stats의 "생성"으로 충분하므로 표시하지 않는다.
const ProvenanceStrip: React.FC<{
  skill: Skill;
  detail: SkillDetail;
  onOpenMarket: () => void;
}> = ({ skill, detail, onOpenMarket }) => {
  const { t } = useTranslation('skills');
  const origin = skillOrigin(skill);

  if (origin === 'builtin') {
    return (
      <div className="mt-3 rounded-lg border border-teal-70/40 bg-teal-70/10 px-3 py-2.5">
        <div className="flex items-start gap-2 text-caption-01">
          <Icon name="sparkle" size={14} className="mt-px flex-shrink-0 text-teal-40" />
          <span className="leading-4 text-text-secondary">
            <span className="font-semibold text-text-primary">{t('mySkills.provenanceBuiltinTitle')}</span> — {t('mySkills.provenanceBuiltinDesc')}{' '}
            <span className="text-text-primary">{t('mySkills.provenanceBuiltinReadOnly')}</span>{' '}
            {t('mySkills.provenanceBuiltinSourceHint')}
          </span>
        </div>
      </div>
    );
  }

  if (origin === 'authored') {
    return (
      <div className="inline-flex items-center gap-2 rounded-lg border border-border-subtle-01 bg-layer-01 px-3 py-2 text-caption-01">
        <Icon name="user" size={14} className="text-purple-60 flex-shrink-0" />
        <span className="text-text-secondary">
          {t('mySkills.provenanceAuthoredBase')}{skill.shared ? t('mySkills.provenanceAuthoredShared') : ''}
        </span>
      </div>
    );
  }

  if (origin === 'installed' && skill.upstream) {
    const u = skill.upstream;
    const diverged = divergenceCount(detail.versions, u.installedVer);
    const update = hasUpstreamUpdate(u);
    return (
      <div className="rounded-lg border border-border-subtle-01 bg-layer-01 px-3 py-2.5">
        <div className="flex items-center gap-2 text-caption-01 flex-wrap">
          <Icon name="download" size={14} className="text-link-primary flex-shrink-0" />
          <span className="text-text-secondary">
            <span className="text-text-primary font-semibold">{u.author}</span>
            {u.team ? ` · ${u.team}` : ''}{t('mySkills.provenanceInstalledFrom')}
          </span>
          <span className="w-px h-3 bg-border-strong-01" />
          <span className="font-mono text-text-helper">{t('mySkills.provenanceInstalledBasis', { ver: u.installedVer })}</span>
          {diverged > 0 && (
            <>
              <span className="w-px h-3 bg-border-strong-01" />
              <span className="inline-flex items-center gap-1 text-support-success">
                <Icon name="growth" size={12} /> {t('mySkills.provenanceInstalledDiverge', { count: diverged })}
              </span>
            </>
          )}
        </div>
        {update && (
          <div className="mt-2 pt-2 border-t border-border-subtle-01 flex items-center gap-2 flex-wrap">
            <Icon name="renew" size={14} className="text-support-warning flex-shrink-0" />
            <span className="text-caption-01 text-text-secondary">
              <Trans
                i18nKey="mySkills.provenanceUpstreamDesc"
                ns="skills"
                values={{ latestVer: u.latestVer, installedVer: u.installedVer, basis: t('mySkills.provenanceInstalledBasis', { ver: '' }), diverge: t('mySkills.provenanceInstalledDiverge', { count: diverged }) }}
                components={{ ver: <span className="font-mono font-semibold text-text-primary" /> }}
              />
            </span>
            <button
              onClick={onOpenMarket}
              className="ml-auto inline-flex items-center gap-1 text-caption-01 font-semibold text-link-primary hover:underline flex-shrink-0"
            >
              {t('mySkills.provenanceUpstreamMarket')} <Icon name="arrow-up-right" size={12} />
            </button>
          </div>
        )}
      </div>
    );
  }

  return null;
};

// 스킬 번들을 .skill.json 파일로 내보낸다 (브라우저 다운로드 · 휴대 가능한 패키지).
const downloadSkillBundle = (skill: Skill, detail: SkillDetail) => {
  const { files } = ownedSkillFiles(skill, detail);
  const bundle = {
    name: skill.name,
    version: skill.ver,
    origin: skillOrigin(skill),
    ...(skill.upstream ? { upstream: skill.upstream } : {}),
    exportedAt: new Date().toISOString(),
    files: files.map((f) => ({ path: f.path, language: f.language, content: f.content })),
  };
  const slug = skill.name.toLowerCase().replace(/[^a-z0-9가-힣]+/g, '-').replace(/(^-|-$)/g, '') || skill.id;
  const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${slug}.skill.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
};

// 소스 보기 런처 — 아이콘만 남기면 의미 발견성이 떨어진다.
// 스킬의 차별점(소스 열람·편집 가능)을 텍스트로 드러내되, 플러그인 헤더보다 무겁지 않게 유지한다.
const SourceLauncher: React.FC<{ skill: Skill; detail: SkillDetail; onOpen: () => void }> = ({ skill, detail, onOpen }) => {
  const { t } = useTranslation('skills');
  const { files } = ownedSkillFiles(skill, detail);
  const readOnly = isBuiltinSkill(skill);
  return (
    <button
      onClick={onOpen}
      title={readOnly ? t('mySkills.sourceLauncherReadOnlyTitle') : t('mySkills.sourceLauncherTitle')}
      className="group inline-flex h-8 items-center gap-2 rounded-lg border border-border-strong-01 bg-layer-01 px-3 text-body-short-01 font-semibold text-text-secondary transition-colors duration-150 hover:bg-layer-hover-01 hover:text-text-primary flex-shrink-0"
    >
      <Icon name="folder" size={15} className="text-icon-secondary group-hover:text-icon-primary" />
      {t('mySkills.sourceLauncherLabel')}
      <span className="text-caption-01 font-normal text-text-helper">{files.length}</span>
      <Icon name="arrow-right" size={13} className="text-icon-secondary opacity-70" />
    </button>
  );
};

const ToolSignalGroup: React.FC<{
  title: string;
  tools: ResolvedToolSignal[];
  empty: string;
}> = ({ title, tools, empty }) => (
  <div className="min-w-0">
    <div className="mb-2 flex items-center gap-1.5 text-caption-01 font-semibold text-text-secondary">
      {title}
      <span className="font-mono text-text-helper">{tools.length}</span>
    </div>
    {tools.length ? (
      <div className="flex flex-wrap gap-1.5">
        {tools.map((tool) => (
          <ToolSignalItem key={`${tool.kind}-${tool.name}`} tool={tool} />
        ))}
      </div>
    ) : (
      <div className="text-caption-01 text-text-helper leading-4">{empty}</div>
    )}
  </div>
);

// ToolSignalItem은 useTranslation이 필요해 별도 컴포넌트로 분리
const ToolSignalItem: React.FC<{ tool: ResolvedToolSignal }> = ({ tool }) => {
  const { t } = useTranslation('skills');
  return (
    <span
      title={tool.evidence || tool.basis || t(TOOL_KIND_LABEL_KEYS[tool.kind])}
      className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-border-subtle-01 bg-layer-01 px-2 py-1 text-caption-01 text-text-secondary"
    >
      <Icon name={TOOL_KIND_ICON[tool.kind]} size={12} className={`${TOOL_KIND_CLS[tool.kind]} flex-shrink-0`} />
      <span className="truncate font-mono">{tool.name}</span>
      {tool.confidence === 'low' && <span className="text-support-warning">{t('mySkills.toolKindLowConfidence')}</span>}
    </span>
  );
};

const SkillRoutingMetrics: React.FC<{ skill: Skill }> = ({ skill }) => {
  const { t } = useTranslation('skills');
  const injected = skill.injected ?? 0;
  const friction = skill.friction ?? 0;
  const routingMiss = skill.routingMiss ?? 0;
  // use에는 명시 호출·수동 skill_view도 포함되므로 100%를 넘을 수 있다. 전환율이
  // 아니라 자동 노출과 전체 관여의 관계를 보는 방향성 지표로 그대로 표시한다.
  const engagementRate = injected > 0 ? Math.round((skill.uses / injected) * 100) : null;
  const metrics = [
    {
      label: t('mySkills.routingEngagement'),
      value: engagementRate == null ? '—' : `${engagementRate}%`,
      helper: injected > 0 ? t('mySkills.routingEngagementHelper', { uses: skill.uses, injected }) : t('mySkills.routingEngagementNoData'),
      title: t('mySkills.routingEngagementTitle'),
    },
    { label: t('mySkills.routingInjected'), value: t('mySkills.telemetryEngagementUnit', { count: injected }), helper: t('mySkills.routingInjectedHelper'), title: t('mySkills.routingInjectedTitle') },
    { label: t('mySkills.routingFriction'), value: t('mySkills.telemetryEngagementUnit', { count: friction }), helper: t('mySkills.routingFrictionHelper'), title: t('mySkills.routingFrictionTitle') },
    { label: t('mySkills.routingMiss'), value: t('mySkills.telemetryEngagementUnit', { count: routingMiss }), helper: t('mySkills.routingMissHelper'), title: t('mySkills.routingMissTitle') },
  ];

  return (
    <div className="grid grid-cols-2 gap-px border-b border-border-subtle-01 bg-border-subtle-01 md:grid-cols-4">
      {metrics.map((metric) => (
        <div key={metric.label} className="min-w-0 bg-background px-4 py-3" title={metric.title}>
          <div className="text-caption-01 text-text-helper">{metric.label}</div>
          <div className="mt-1 font-mono text-body-short-01 font-semibold text-text-primary">{metric.value}</div>
          <div className="mt-0.5 truncate text-caption-01 text-text-helper">{metric.helper}</div>
        </div>
      ))}
    </div>
  );
};

type AnalysisRefreshFeedback = {
  tone: 'success' | 'warning' | 'error';
  message: string;
};

const ANALYSIS_FEEDBACK_META: Record<AnalysisRefreshFeedback['tone'], { icon: IconName; cls: string }> = {
  success: { icon: 'check-circle', cls: 'text-support-success' },
  warning: { icon: 'warning', cls: 'text-support-warning' },
  error: { icon: 'warning', cls: 'text-support-error' },
};

const SkillUnderstandingPanel: React.FC<{
  skill: Skill;
  detail: SkillDetail;
  refreshing: boolean;
  feedback: AnalysisRefreshFeedback | null;
  onRefresh: () => void;
  onOpenConnectors: () => void;
}> = ({ skill, detail, refreshing, feedback, onRefresh, onOpenConnectors }) => {
  const { t } = useTranslation('skills');
  const { language } = useI18n();
  const understanding = useMemo(() => resolveUnderstanding(skill, detail, t), [skill, detail, t]);
  const observed = understanding.tools.filter((tool) => tool.kind === 'observed');
  const declared = understanding.tools.filter((tool) => tool.kind === 'declared');
  const mentioned = understanding.tools.filter((tool) => tool.kind === 'mentioned');
  const feedbackMeta = feedback ? ANALYSIS_FEEDBACK_META[feedback.tone] : null;

  return (
    <div>
      <SectionLabel
        right={
          <button
            onClick={onRefresh}
            disabled={refreshing}
            className="inline-flex items-center gap-1.5 rounded-md border border-border-subtle-01 px-2 py-1 text-caption-01 font-semibold text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary disabled:opacity-50"
            title={t('mySkills.analysisRefreshTitle')}
          >
            <Icon name="renew" size={12} className={refreshing ? 'animate-spin text-icon-primary' : 'text-icon-secondary'} />
            {refreshing ? t('mySkills.analysisRefreshBusy') : t('mySkills.analysisRefreshBtn')}
          </button>
        }
      >
        {t('mySkills.understandingTitle')}
      </SectionLabel>

      <div className="rounded-lg border border-border-subtle-01 bg-layer-01">
        <div className="border-b border-border-subtle-01 px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <Icon name="model" size={15} className="text-icon-primary" />
            <span className="text-body-short-01 font-semibold text-text-primary">{understanding.sourceLabel}</span>
            <Tag type={understanding.stale ? 'yellow' : 'gray'} size="sm">
              {understanding.statusLabel}
            </Tag>
            <ConfidencePill value={understanding.confidence} />
            {understanding.analyzedAt && (
              <span className="text-caption-01 text-text-helper">
                {new Date(understanding.analyzedAt).toLocaleString(language === 'ko' ? 'ko-KR' : 'en-US')}
              </span>
            )}
          </div>
          <p className="mt-2 max-w-[840px] text-body-short-01 leading-5 text-text-secondary">{understanding.summary}</p>
          {feedback && feedbackMeta && (
            <div
              className={`mt-2 inline-flex items-center gap-1.5 text-caption-01 font-medium ${feedbackMeta.cls}`}
              role={feedback.tone === 'error' ? 'alert' : 'status'}
            >
              <Icon name={feedbackMeta.icon} size={12} />
              {feedback.message}
            </div>
          )}
        </div>

        <SkillRoutingMetrics skill={skill} />

        <div className="grid gap-px bg-border-subtle-01 lg:grid-cols-[minmax(0,1.1fr)_minmax(280px,0.9fr)]">
          <div className="bg-background p-4">
            <div className="mb-3 flex items-center gap-2">
              <Icon name="flow" size={15} className="text-icon-secondary" />
              <span className="text-body-short-01 font-semibold text-text-primary">{t('mySkills.understandingFlowTitle')}</span>
              <span className="text-caption-01 text-text-helper">{t('mySkills.understandingFlowHint')}</span>
            </div>
            {understanding.steps.length ? (
              <div className="flex flex-col gap-2.5">
                {understanding.steps.map((step, i) => (
                  <div key={`${step.title}-${i}`} className="flex gap-3">
                    <div className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md border border-border-subtle-01 bg-layer-01 text-caption-01 font-mono text-text-secondary">
                      {i + 1}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-body-short-01 font-semibold text-text-primary">{step.title}</span>
                        <ConfidencePill value={step.confidence} />
                      </div>
                      {step.description && <div className="mt-0.5 text-caption-01 text-text-helper">{step.description}</div>}
                      {step.evidence && (
                        <div className="mt-1 truncate text-label-micro font-mono text-text-helper" title={step.evidence}>
                          {step.evidence}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-md border border-border-subtle-01 bg-layer-01 px-3 py-2.5 text-caption-01 text-text-helper">
                {t('mySkills.understandingFlowEmpty')}
              </div>
            )}
          </div>

          <div className="bg-background p-4">
            <div className="mb-3 flex items-center gap-2">
              <Icon name="tools" size={15} className="text-icon-secondary" />
              <span className="text-body-short-01 font-semibold text-text-primary">{t('mySkills.understandingToolTitle')}</span>
              <button
                onClick={onOpenConnectors}
                className="ml-auto inline-flex items-center gap-1 text-caption-01 text-link-primary hover:underline"
              >
                {t('mySkills.understandingPermissionLink')} <Icon name="arrow-up-right" size={11} />
              </button>
            </div>
            <div className="grid gap-4">
              <ToolSignalGroup title={t('mySkills.toolGroupObserved')} tools={observed} empty={t('mySkills.toolGroupObservedEmpty')} />
              <ToolSignalGroup title={t('mySkills.toolGroupDeclared')} tools={declared} empty={t('mySkills.toolGroupDeclaredEmpty')} />
              <ToolSignalGroup title={t('mySkills.toolGroupMentioned')} tools={mentioned} empty={t('mySkills.toolGroupMentionedEmpty')} />
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t border-border-subtle-01 px-4 py-2.5 text-caption-01 text-text-helper">
          <Icon name="lock" size={12} className="text-icon-secondary" />
          {t('mySkills.analysisDisclaimer')}
          {understanding.warnings.map((w, i) => (
            <span key={i} className="inline-flex items-center gap-1 text-support-warning">
              <Icon name="warning" size={11} /> {w}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
};

const SkillTelemetryPanel: React.FC<{ skill: Skill; detail: SkillDetail }> = ({ skill, detail }) => {
  const { t, i18n } = useTranslation('skills');
  const tChat = wrapT(useTranslation('chat').t);
  const statusReason = makeStatusReason(t);
  const origin = skillOrigin(skill);
  const originLabel = t(ORIGIN_LABEL_KEYS[origin]);
  const metrics: Array<{ label: string; value: string | undefined; helper: string | undefined }> = [
    { label: t('mySkills.telemetryEngagement'), value: t('mySkills.telemetryEngagementUnit', { count: skill.uses }), helper: renderTrend(skill.trend, tChat) },
    { label: t('mySkills.telemetryPatch'), value: t('mySkills.telemetryPatchUnit', { count: skill.patches ?? 0 }), helper: t('mySkills.telemetryPatchHelper') },
    { label: t('mySkills.telemetryLastUsed'), value: renderLastUsed(detail.lastUsed, tChat, i18n.language), helper: statusReason(skill) },
    { label: t('mySkills.telemetryOrigin'), value: originLabel, helper: renderCreatedFrom(detail.createdFrom, tChat) },
  ];
  return (
    <div>
      <SectionLabel>{t('mySkills.telemetryTitle')}</SectionLabel>
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border-subtle-01 bg-border-subtle-01 md:grid-cols-4">
        {metrics.map((m) => (
          <div key={m.label} className="min-w-0 bg-background px-3.5 py-3">
            <div className="text-caption-01 text-text-helper">{m.label}</div>
            <div className="mt-1 truncate text-body-short-01 font-semibold text-text-primary" title={m.value}>
              {m.value}
            </div>
            <div className="mt-0.5 truncate text-caption-01 text-text-helper" title={m.helper}>
              {m.helper}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

const SkillOverviewPanel: React.FC<{
  skill: Skill;
  detail: SkillDetail;
  onOpenInsights: () => void;
  onOpenConnectors: () => void;
  onOpenMarket: () => void;
}> = ({ skill, detail, onOpenInsights, onOpenConnectors, onOpenMarket }) => {
  const { t } = useTranslation('skills');
  const understanding = useMemo(() => resolveUnderstanding(skill, detail, t), [skill, detail, t]);
  const status = skillStatus(skill);
  const observed = understanding.tools.filter((tool) => tool.kind === 'observed');
  const declared = understanding.tools.filter((tool) => tool.kind === 'declared');
  const usefulTools = (observed.length ? observed : declared.length ? declared : understanding.tools).slice(0, 5);
  const hasAttention = detail.analysis?.status === 'stale'
    || detail.analysis?.status === 'failed'
    || status === 'stale'
    || (skill.friction ?? 0) > 0
    || (skill.routingMiss ?? 0) > 0
    || hasUpstreamUpdate(skill.upstream);
  const visibleSteps = understanding.steps.slice(0, 4);

  return (
    <div className="mx-auto w-full max-w-[1120px] space-y-4">
      {hasAttention && (
        <div className="flex items-center gap-3 rounded-xl border border-support-warning/35 bg-support-warning/10 px-4 py-3">
          <Icon name="warning" size={16} className="flex-shrink-0 text-support-warning" />
          <div className="min-w-0 flex-1">
            <div className="text-body-short-01 font-semibold text-text-primary">{t('mySkills.overviewAttentionTitle')}</div>
            <div className="text-caption-01 text-text-secondary">{t('mySkills.overviewAttentionDesc')}</div>
          </div>
          <button onClick={onOpenInsights} className="flex-shrink-0 text-caption-01 font-semibold text-link-primary hover:underline">
            {t('mySkills.overviewInsightsLink')}
          </button>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.45fr)_minmax(280px,0.75fr)]">
        <section className="rounded-xl border border-border-subtle-01 bg-layer-01 p-4">
          <div className="flex items-start gap-3">
            <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border border-border-subtle-02 bg-layer-02 text-icon-primary">
              <Icon name="flow" size={16} />
            </div>
            <div className="min-w-0 flex-1">
              <h3 className="text-body-short-02 font-semibold text-text-primary">{t('mySkills.understandingFlowTitle')}</h3>
              <p className="mt-0.5 text-caption-01 text-text-helper">{t('mySkills.understandingFlowHint')}</p>
            </div>
          </div>

          {visibleSteps.length ? (
            <div className="mt-4 space-y-3">
              {visibleSteps.map((step, index) => (
                <div key={`${step.title}-${index}`} className="flex items-start gap-3">
                  <span className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full border border-border-subtle-02 bg-background font-mono text-caption-01 text-text-secondary">
                    {index + 1}
                  </span>
                  <div className="min-w-0 pt-0.5">
                    <div className="text-body-short-01 font-semibold text-text-primary">{step.title}</div>
                    {step.description && <div className="mt-0.5 line-clamp-1 text-caption-01 text-text-helper">{step.description}</div>}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="mt-4 rounded-lg border border-border-subtle-01 bg-background px-3 py-2.5 text-caption-01 text-text-helper">
              {t('mySkills.understandingFlowEmpty')}
            </div>
          )}

          <button onClick={onOpenInsights} className="mt-4 inline-flex items-center gap-1 text-caption-01 font-semibold text-link-primary hover:underline">
            {t('mySkills.overviewShowAllSteps')} <Icon name="arrow-right" size={12} />
          </button>
        </section>

        <div>
          <section className="rounded-xl border border-border-subtle-01 bg-layer-01 p-4">
            <div className="flex items-center gap-2">
              <Icon name="tools" size={15} className="text-icon-secondary" />
              <h3 className="text-body-short-01 font-semibold text-text-primary">{t('mySkills.overviewToolsTitle')}</h3>
              <button onClick={onOpenConnectors} className="ml-auto text-caption-01 text-link-primary hover:underline">
                {t('mySkills.understandingPermissionLink')}
              </button>
            </div>
            {usefulTools.length ? (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {usefulTools.map((tool) => (
                  <span key={`${tool.kind}-${tool.name}`} className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-border-subtle-01 bg-background px-2 py-1 text-caption-01 text-text-secondary">
                    <Icon name={TOOL_KIND_ICON[tool.kind]} size={11} className={`${TOOL_KIND_CLS[tool.kind]} flex-shrink-0`} />
                    <span className="truncate font-mono">{tool.name}</span>
                  </span>
                ))}
              </div>
            ) : (
              <p className="mt-3 text-caption-01 text-text-helper">{t('mySkills.overviewNoTools')}</p>
            )}
          </section>
        </div>
      </div>

      {!isBuiltinSkill(skill) && <ProvenanceStrip skill={skill} detail={detail} onOpenMarket={onOpenMarket} />}
    </div>
  );
};

const SkillVersionsPanel: React.FC<{
  detail: SkillDetail;
  onRetry: () => void;
  onView: (version: string) => void;
  onRestore: (version: SkillVersion) => void;
}> = ({ detail, onRetry, onView, onRestore }) => {
  const { t } = useTranslation('skills');
  const tChat = wrapT(useTranslation('chat').t);
  const { language } = useI18n();

  return (
    <div className="mx-auto w-full max-w-[880px]">
      <div className="mb-4 flex items-center gap-3">
        <div>
          <h3 className="text-heading-01 font-semibold text-text-primary">
            {detail.historyStatus === 'loading'
              ? t('mySkills.versionTitleLoading')
              : t('mySkills.versionTitle', { count: detail.versions.length })}
          </h3>
          <p className="mt-1 text-caption-01 text-text-helper">
            {detail.versions.length > 1 ? t('mySkills.historyViewNote') : t('mySkills.historyViewSingle')}
          </p>
        </div>
        {detail.historyStatus === 'loading' ? (
          <span className="ml-auto inline-flex items-center gap-1.5 text-caption-01 text-text-helper">
            <Icon name="renew" size={11} className="animate-spin" /> {t('mySkills.historyLoading')}
          </span>
        ) : detail.historyStatus === 'error' ? (
          <button onClick={onRetry} className="ml-auto inline-flex items-center gap-1 text-caption-01 font-semibold text-support-warning hover:underline">
            <Icon name="renew" size={11} /> {t('mySkills.historyRetry')}
          </button>
        ) : null}
      </div>

      {detail.historyStatus === 'error' && (
        <div className="mb-4 flex items-start gap-2 rounded-xl border border-support-warning/35 bg-support-warning/10 px-3 py-2.5 text-caption-01 text-text-secondary">
          <Icon name="warning" size={13} className="mt-0.5 flex-shrink-0 text-support-warning" />
          <span>{t('mySkills.historyError')}{detail.historyError ? ` · ${detail.historyError}` : ''}</span>
        </div>
      )}

      <div>
        {detail.versions.map((version, index) => (
          <div key={version.ver} className="flex gap-3">
            <div className="flex flex-col items-center flex-shrink-0">
              <div className={`mt-4 h-2 w-2 rounded-full ${version.current ? 'bg-interactive ring-2 ring-blue-100' : 'bg-layer-03'}`} />
              {index < detail.versions.length - 1 && <div className="min-h-[18px] w-px flex-1 bg-border-strong-01" />}
            </div>
            <div className="mb-2.5 flex min-w-0 flex-1 items-center rounded-xl border border-border-subtle-01 bg-layer-01 transition-colors duration-150 hover:border-border-strong-01">
              <button
                onClick={() => onView(version.ver)}
                className="flex min-w-0 flex-1 items-center gap-3 rounded-l-xl px-3.5 py-3 text-left transition-colors duration-150 hover:bg-layer-hover-01"
              >
                <div className="min-w-0 flex-1">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="font-mono text-body-short-01 font-semibold text-text-primary">{version.ver}</span>
                  {version.current && <Tag type="blue" size="sm">{t('mySkills.versionCurrentTag')}</Tag>}
                  <span className="truncate text-caption-01 text-text-helper">{renderVersionWhen(version.when, tChat, language)}</span>
                  {version.author && (
                    <span className="inline-flex items-center gap-1 text-caption-01 text-text-helper">
                      <Icon name="user" size={10} /> {version.author}
                    </span>
                  )}
                </div>
                <div className="mt-1 truncate text-caption-01 text-text-secondary">{renderVersionNote(version.note, tChat, version.noteParams)}</div>
                {version.metrics && (
                  <div className="mt-1.5 flex items-center gap-2 font-mono text-label-micro text-text-helper">
                    <span>{t('mySkills.metricSuccessRate')} {pctStr(version.metrics.successRate)}</span>
                    <span>· {t('mySkills.metricUsesUnit', { value: version.metrics.uses })}</span>
                    {version.metrics.avgTurns != null && <span>· {t('mySkills.metricTurnsUnit', { value: version.metrics.avgTurns })}</span>}
                  </div>
                )}
                </div>
                <Icon name="arrow-right" size={13} className="flex-shrink-0 text-icon-secondary opacity-60" />
              </button>
              {!version.current && (
                <button
                  type="button"
                  onClick={() => onRestore(version)}
                  className="mr-3 inline-flex h-7 flex-shrink-0 items-center gap-1 rounded-lg border border-border-subtle-02 px-2 text-caption-01 font-semibold text-text-secondary transition-colors duration-150 hover:bg-layer-hover-01 hover:text-text-primary"
                >
                  <Icon name="renew" size={11} /> {t('mySkills.versionRestoreBtn')}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

const SkillDetailView: React.FC<{
  skillId: string | null;
  onSetStatus: (id: string, next: SkillStatus) => void;
}> = ({ skillId, onSetStatus }) => {
  const { t } = useTranslation('skills');
  const tChat = wrapT(useTranslation('chat').t);
  const { language } = useI18n();
  const navigate = useNavigate();
  const { skills, setSkills, details, setDetails } = useLearning();
  const { publishOwnedSkill, unpublishSkill } = useMarketplace();
  const toast = useToast();
  const [snapVer, setSnapVer] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<DetailTab>('overview');
  // 풀스크린 소스 워크스페이스(IDE) 열림 여부
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [summaryOpen, setSummaryOpen] = useState(false);
  // 채팅 매개 편집 드로어 열림 여부 (Phase 4)
  const [editOpen, setEditOpen] = useState(false);
  // 편집 대상 파일 — 워크스페이스에서 연 파일. undefined면 SKILL.md(지침).
  const [editTarget, setEditTarget] = useState<string | undefined>(undefined);
  // 복원 확인 모달 — 전환 대상 버전을 담아두고, 확인 시에만 실제 복원한다.
  const [confirmRestore, setConfirmRestore] = useState<SkillVersion | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  // Skill Market 공개 전용 다이얼로그 / 마켓에서 내리기 확인
  const [publishOpen, setPublishOpen] = useState(false);
  const [confirmUnshare, setConfirmUnshare] = useState(false);
  const [analysisRefreshing, setAnalysisRefreshing] = useState(false);
  const [analysisFeedback, setAnalysisFeedback] = useState<AnalysisRefreshFeedback | null>(null);

  const loadRuntimeSkillDetail = React.useCallback(async (targetSkillId: string): Promise<boolean> => {
    setDetails((prev) => {
      const cur = prev[targetSkillId];
      if (!cur || cur.historyStatus === undefined) return prev;
      return {
        ...prev,
        [targetSkillId]: { ...cur, historyStatus: 'loading', historyError: undefined },
      };
    });

    const [detailRes, historyRes] = await Promise.all([
      api.getSkill(targetSkillId),
      api.getSkillHistory(targetSkillId),
    ]);
    if (!detailRes.success || !detailRes.data) {
      setDetails((prev) => {
        const cur = prev[targetSkillId];
        if (!cur || cur.historyStatus === undefined) return prev;
        return {
          ...prev,
          [targetSkillId]: {
            ...cur,
            historyStatus: 'error',
            historyError: detailRes.error || t('mySkills.historyErrorLoading'),
          },
        };
      });
      return false;
    }

    const data = detailRes.data as RuntimeSkillDetailPayload;
    const source = runtimeSkillSource(data);
    const history = historyRes.success && Array.isArray(historyRes.data) ? historyRes.data : null;

    setSkills((prev) => prev.map((item) => (
      item.id === targetSkillId ? { ...item, ver: `v${data.version}` } : item
    )));
    setDetails((prev) => {
      const cur = prev[targetSkillId];
      if (!cur) return prev;
      const runtimeFiles = data.files?.map((file) => ({
        path: file.path,
        language: languageFromPath(file.path),
        content: file.path.toLowerCase().endsWith('skill.md') ? source : file.content,
      }));
      const files = runtimeFiles?.length ? runtimeFiles : [...(cur.files ?? [])];
      const skillMdIndex = files.findIndex((file) => file.path.toLowerCase().endsWith('skill.md'));
      const skillMd = { path: 'SKILL.md', language: languageFromPath('SKILL.md'), content: source } as const;
      if (skillMdIndex >= 0) files[skillMdIndex] = { ...files[skillMdIndex], content: source };
      else files.unshift(skillMd);

      const currentVersion: SkillVersion = {
        ver: `v${data.version}`,
        when: t('mySkills.versionCurrentApplied'),
        note: t('mySkills.versionCurrentNote'),
        current: true,
        body: source,
      };
      const versions = history
        ? runtimeVersionList(data, history, t, language)
        : [
            currentVersion,
            ...cur.versions
              .filter((version) => version.ver !== currentVersion.ver)
              .map((version) => ({ ...version, current: false })),
          ];
      return {
        ...prev,
        [targetSkillId]: {
          ...cur,
          summary: data.description || cur.summary,
          analysis: data.analysis ? runtimeAnalysisToSkillAnalysis(data.analysis) : cur.analysis,
          files,
          versions,
          historyStatus: history ? 'loaded' : 'error',
          historyError: history ? undefined : (historyRes.error || t('mySkills.historyVersionError')),
        },
      };
    });
    return true;
  }, [setDetails, setSkills, t, language]);

  // leaving a skill closes any open version snapshot / 확인 모달 / 분석 상태 / 소스·편집
  useEffect(() => {
    setSnapVer(null);
    setActiveTab('overview');
    setConfirmRestore(null);
    setAnalysisRefreshing(false);
    setAnalysisFeedback(null);
    setWorkspaceOpen(false);
    setSummaryOpen(false);
    setEditOpen(false);
    setEditTarget(undefined);
    setPublishOpen(false);
    setConfirmUnshare(false);
    setConfirmDelete(false);
  }, [skillId]);

  // 런타임 상세 + 실제 버전 히스토리를 함께 hydrate한다. 마켓/로컬 전용 detail은
  // historyStatus가 undefined라 기존 로컬 스냅샷을 그대로 유지한다.
  const runtimeBacked = skillId ? details[skillId]?.historyStatus !== undefined : false;
  useEffect(() => {
    if (!skillId || !runtimeBacked) return;
    void loadRuntimeSkillDetail(skillId);
  }, [skillId, runtimeBacked, loadRuntimeSkillDetail]);

  const skill = skills.find((s) => s.id === skillId);
  const detail = skillId ? details[skillId] : undefined;

  if (!skill || !detail || !skillId) {
    return <div className="flex-1 flex items-center justify-center text-body-short-02 text-text-helper">{t('mySkills.selectSkillHint')}</div>;
  }

  const builtin = isBuiltinSkill(skill);
  const patch = (fn: (d: SkillDetail) => SkillDetail) => setDetails((d) => ({ ...d, [skillId]: fn(d[skillId]) }));
  const summaryExpandable = detail.summary.length > 170;

  // 분석 새로고침 — 런타임이 사이드카(<name>.analysis.json)만 재생성한다.
  // SKILL.md 본문/버전은 절대 변하지 않는다 (POST /skills/{id}/analysis/refresh 계약).
  const refreshAnalysis = async () => {
    if (analysisRefreshing) return;
    setAnalysisRefreshing(true);
    setAnalysisFeedback(null);
    const progress = toast.progress({
      label: t('mySkills.analysisProgressLabel'),
      sublabel: t('mySkills.analysisProgressSublabel', { name: skill.name }),
    });
    try {
      const res = await api.refreshSkillAnalysis(skillId);
      if (res.success && res.data) {
        const mapped = runtimeAnalysisToSkillAnalysis(res.data);
        setDetails((prev) => {
          const cur = prev[skillId];
          if (!cur) return prev;
          return { ...prev, [skillId]: { ...cur, analysis: mapped } };
        });
        const localeTag = language === 'ko' ? 'ko-KR' : 'en-US';
        const savedAt = mapped.analyzedAt
          ? new Date(mapped.analyzedAt).toLocaleString(localeTag)
          : t('mySkills.versionRestoreWhen');
        if (mapped.source === 'llm') {
          setAnalysisFeedback({ tone: 'success', message: t('mySkills.analysisFeedbackSuccess', { savedAt }) });
          progress.done('success', t('mySkills.analysisProgressDone'));
        } else if (mapped.status === 'failed') {
          setAnalysisFeedback({ tone: 'warning', message: t('mySkills.analysisFeedbackWarnLlm', { savedAt }) });
          progress.dismiss();
          toast.notify({ message: t('mySkills.analysisFeedbackLlmFallback'), variant: 'warning' });
        } else {
          setAnalysisFeedback({ tone: 'warning', message: t('mySkills.analysisFeedbackWarnHeuristic', { savedAt }) });
          progress.done('success', t('mySkills.analysisProgressDoneHeuristic'));
        }
        return;
      }
      const reason = res.error ? ` · ${res.error}` : '';
      setAnalysisFeedback({ tone: 'error', message: t('mySkills.analysisFeedbackError', { reason }) });
      progress.done('error', t('mySkills.analysisProgressFail'));
    } catch (error) {
      const reason = error instanceof Error ? ` · ${error.message}` : '';
      setAnalysisFeedback({ tone: 'error', message: t('mySkills.analysisFeedbackErrorCatch', { reason }) });
      progress.done('error', t('mySkills.analysisProgressFail'));
    } finally {
      setAnalysisRefreshing(false);
    }
  };

  const restoreLocalVersion = (version: SkillVersion) => {
    const newVer = bumpVer(skill.ver);
    setSkills((a) => a.map((x) => (x.id === skillId ? { ...x, ver: newVer } : x)));
    patch((d) => ({
      ...d,
      analysis: d.analysis ? { ...d.analysis, status: 'stale' } : d.analysis,
      rules: (version.rules ?? []).map((r, i) => ({
        id: `rv${Date.now()}-${i}`,
        text: r.text,
        source: t('mySkills.versionRestoreSource', { ver: version.ver }),
        on: true,
      })),
      versions: [
        {
          ver: newVer,
          when: t('mySkills.versionRestoreWhen'),
          note: t('mySkills.versionRestoreNote', { ver: version.ver }),
          current: true,
          rules: (version.rules ?? []).map((r) => ({ text: r.text })),
        },
        ...d.versions.map((v) => ({ ...v, current: false })),
      ],
    }));
    setSnapVer(null);
  };

  // 런타임 버전 복원은 낙관 갱신하지 않는다. 서버가 현재 원본 보관 → 과거 본문
  // 복원 → 새 단조 증가 버전 발급을 모두 마친 뒤, 상세/히스토리를 다시 읽어 UI를
  // 서버 정본과 맞춘다. 마켓/로컬 fixture만 기존 로컬 스냅샷 경로를 사용한다.
  const restore = async (version: SkillVersion) => {
    if (detail.historyStatus === undefined) {
      restoreLocalVersion(version);
      return;
    }

    const progress = toast.progress({
      label: t('mySkills.restoreProgressLabel', { ver: version.ver }),
      sublabel: t('mySkills.restoreProgressSublabel', { name: skill.name }),
    });
    try {
      const res = await api.rollbackSkill(skillId, verNum(version.ver));
      if (!res.success || !res.data) {
        const reason = res.error ? ` · ${res.error}` : '';
        progress.done('error', t('mySkills.restoreProgressFail'));
        toast.notify({ message: t('mySkills.restoreErrorMsg', { ver: version.ver, reason }), variant: 'error' });
        return;
      }

      const refreshed = await loadRuntimeSkillDetail(skillId);
      if (!refreshed) {
        setSkills((prev) => prev.map((item) => (
          item.id === skillId ? { ...item, ver: `v${res.data!.version}` } : item
        )));
        progress.done('success', t('mySkills.restoreProgressDone', { ver: version.ver }));
        toast.notify({ message: t('mySkills.restoreReloadWarn'), variant: 'warning' });
      } else {
        progress.done('success', t('mySkills.restoreProgressDoneNew', { from: version.ver, to: `v${res.data.version}` }));
      }
      setSnapVer(null);
    } catch (error) {
      const reason = error instanceof Error ? ` · ${error.message}` : '';
      progress.done('error', t('mySkills.restoreProgressFail'));
      toast.notify({ message: t('mySkills.restoreErrorCatch', { reason }), variant: 'error' });
    }
  };

  // 편집 어시스턴트 적용 — patch 승인 → version++ + 대상 파일 갱신 + 활동 기록.
  // 구조화된 절차/도구 표시는 본문에서 다시 분석되는 설명용 sidecar가 담당한다.
  const applyEditPatch = (p: EditPatch, editedBody?: string) => {
    const finalBody = editedBody ?? p.proposedBody;
    // AI가 frontmatter의 description/tags를 고쳤다면 body와 함께 저장한다 —
    // stripFrontmatter만 하면 "설명 바꿔줘" 류 지시가 조용히 유실된다.
    const meta = parseFrontmatterMeta(finalBody);
    // 런타임 영속화 — SKILL.md와 번들 파일이 같은 버전 축을 공유한다. 서버는
    // SKILL.md 및 reference/script 스냅샷을 함께 history에 보관한다.
    const persistence = p.targetPath.toLowerCase().endsWith('skill.md')
      ? api.updateSkill(skillId, {
          body: stripFrontmatter(finalBody),
          ...(meta.description ? { description: meta.description } : {}),
          ...(meta.tags?.length ? { tags: meta.tags } : {}),
        })
      : api.updateSkillFile(skillId, p.targetPath, finalBody);
    void persistence.then((result) => {
      if (!result.success) {
        if (detail.historyStatus !== undefined) {
          toast.notify({
            message: t('mySkills.editSaveFailMsg', { targetLabel: p.targetLabel }) + (result.error ? ` — ${apiErrorMessage(result)}` : ''),
            variant: 'warning',
          });
        }
        return;
      }
      // 서버가 발급한 버전과 파일 트리를 다시 읽어 낙관 UI를 정본에 맞춘다.
      if (detail.historyStatus !== undefined) void loadRuntimeSkillDetail(skillId);
    });
    setSkills((a) => a.map((x) => (x.id === skillId ? { ...x, ver: p.toVer, patches: (x.patches ?? 0) + 1 } : x)));
    patch((d) => {
      // 대상 파일 갱신(없으면 생성) — 소스 뷰가 승인 내용을 그대로 반영하도록
      const files = d.files ? [...d.files] : [];
      const idx = files.findIndex((f) => f.path === p.targetPath);
      if (idx >= 0) {
        files[idx] = { ...files[idx], content: finalBody };
      } else {
        files.unshift({ path: p.targetPath, language: languageFromPath(p.targetPath), content: finalBody });
      }

      return {
        ...d,
        files,
        analysis: d.analysis ? { ...d.analysis, status: 'stale' } : d.analysis,
        versions: [
          {
            ver: p.toVer,
            when: t('mySkills.versionEditWhen'),
            note: p.changelog,
            current: true,
            author: t('mySkills.versionEditAuthor'),
            rules: d.rules.map((r) => ({ text: r.text })),
          },
          ...d.versions.map((v) => ({ ...v, current: false })),
        ],
        activity: [
          { when: t('mySkills.versionEditWhen'), text: t('mySkills.activityEditText', { toVer: p.toVer, targetLabel: p.targetLabel, changelog: p.changelog }), kind: 'edit' as const },
          ...(d.activity ?? []),
        ],
      };
    });
    setAnalysisFeedback({ tone: 'warning', message: t('mySkills.analysisFeedbackBodyChanged') });
  };

  // 복원 확인 팝업 — 칩 메뉴의 "전환"과 스냅샷의 "이 버전 복원" 두 경로가 모두 거친다.
  const confirmModal = confirmRestore && (
    <ConfirmDialog
      open
      tone="primary"
      title={t('mySkills.restoreConfirmTitle')}
      confirmLabel={t('mySkills.restoreConfirmLabel', { ver: confirmRestore.ver })}
      confirmIcon="renew"
      onClose={() => setConfirmRestore(null)}
      onConfirm={() => {
        void restore(confirmRestore);
        setConfirmRestore(null);
      }}
      description={
        <Trans
          i18nKey="mySkills.restoreConfirmDesc"
          ns="skills"
          values={{ ver: confirmRestore.ver, newVer: bumpVer(skill.ver) }}
          components={{
            ver: <span className="font-mono font-semibold text-text-primary" />,
            newVer: <span className="font-mono" />,
          }}
        />
      }
    />
  );

  // version snapshot sub-depth
  const snap = snapVer ? detail.versions.find((v) => v.ver === snapVer) : undefined;
  const currentVersion = detail.versions.find((v) => v.current);
  if (snap) {
    return (
      <>
        <VersionSnapshot
          skillName={skill.name}
          version={snap}
          currentVersion={currentVersion}
          onBack={() => setSnapVer(null)}
          onRestore={() => setConfirmRestore(snap)}
        />
        {confirmModal}
      </>
    );
  }

  return (
    <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
      {/* header */}
      <div className="flex-shrink-0 px-7 pt-6 pb-5 border-b border-border-subtle-01">
        <div className="flex items-start gap-3.5">
          <div className={`w-11 h-11 flex-shrink-0 flex items-center justify-center rounded-lg border ${
            isInjected(skillStatus(skill))
              ? 'bg-blue-60/14 border-blue-50/30 text-blue-30'
              : 'bg-layer-03 border-border-subtle-02 text-text-helper'
          }`}>
            <Icon name="skill" size={22} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-heading-03 font-semibold text-text-primary">{skill.name}</h2>
              {builtin ? (
                <span className="text-caption-01 font-mono text-text-secondary bg-layer-02 border border-border-subtle-01 rounded px-1.5 py-px">
                  {skill.ver}
                </span>
              ) : (
                <button
                  onClick={() => setActiveTab('versions')}
                  title={t('mySkills.versionChipTitle')}
                  className="rounded border border-border-subtle-01 bg-layer-02 px-1.5 py-px font-mono text-caption-01 text-text-secondary transition-colors duration-150 hover:bg-layer-hover-01 hover:text-text-primary"
                >
                  {skill.ver}
                </button>
              )}
              {builtin && (
                <Tag type="teal" size="sm">
                  {t('mySkills.headerBuiltinTag')}
                </Tag>
              )}
              {!builtin && (
                <span className="inline-flex items-center gap-1.5 text-caption-01 text-text-helper">
                  <OriginGlyph s={skill} /> {t(ORIGIN_LABEL_KEYS[skillOrigin(skill)])}
                </span>
              )}
              {skill.shared && (
                <Tag type="purple" size="sm">
                  {t('mySkills.headerSharedTag')}
                </Tag>
              )}
              {/* 상태 컨트롤 — 빌트인은 활성/비활성 토글 하나, 그 외는 활성/Stale/보관/비활성 */}
              {builtin ? (
                <BuiltinStatusToggle skill={skill} onSet={(next) => onSetStatus(skill.id, next)} />
              ) : (
                <StatusControl skill={skill} onSet={(next) => onSetStatus(skill.id, next)} />
              )}
            </div>
            <div className="mt-1.5 max-w-[780px]">
              <p className={`text-body-short-01 leading-5 text-text-secondary ${summaryExpandable && !summaryOpen ? 'line-clamp-2' : ''}`}>
                {detail.summary}
              </p>
              {summaryExpandable && (
                <button
                  onClick={() => setSummaryOpen((open) => !open)}
                  className="mt-1 inline-flex items-center gap-1 text-caption-01 font-semibold text-link-primary hover:underline"
                  aria-expanded={summaryOpen}
                >
                  {summaryOpen ? t('mySkills.headerSummaryCollapse') : t('mySkills.headerSummaryExpand')}
                  <Icon name={summaryOpen ? 'chevron-up' : 'chevron-down'} size={12} />
                </button>
              )}
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1.5 mt-3 text-caption-01">
              {([
                [t('mySkills.headerUsesLabel'), t('mySkills.headerUsesUnit', { count: skill.uses })],
                [t('mySkills.headerLastUsedLabel'), renderLastUsed(detail.lastUsed, tChat, language)],
              ]).map(([k, v]) => (
                <span key={k} className="inline-flex items-baseline gap-1.5 text-text-helper">
                  <span>{k}</span>
                  <span className="text-text-secondary">{v}</span>
                </span>
              ))}
            </div>
            {builtin && <ProvenanceStrip skill={skill} detail={detail} onOpenMarket={() => navigate('/skills')} />}
          </div>
          <div className="flex flex-shrink-0 items-center gap-2">
            {!builtin && (
              <Button
                kind="primary"
                size="sm"
                onClick={() => { setEditTarget(undefined); setEditOpen(true); }}
                title={t('mySkills.headerAiEditTitle')}
                icon={<Icon name="sparkle" size={14} />}
              >
                {t('mySkills.headerAiEditBtn')}
              </Button>
            )}
            <SourceLauncher skill={skill} detail={detail} onOpen={() => setWorkspaceOpen(true)} />
            <OverflowMenu
              align="right"
              direction="down"
              variant="compact"
              items={builtin
                ? [
                    { label: t('mySkills.menuExportLabel'), icon: 'export', onClick: () => downloadSkillBundle(skill, detail) },
                  ]
                : [
                    skill.shared
                      ? { label: t('mySkills.menuUnshareLabel'), icon: 'share', onClick: () => setConfirmUnshare(true) }
                      : { label: t('mySkills.menuShareLabel'), icon: 'share', onClick: () => setPublishOpen(true) },
                    { label: t('mySkills.menuExportLabel'), icon: 'export', onClick: () => downloadSkillBundle(skill, detail) },
                    { label: t('mySkills.menuDeleteLabel'), icon: 'trash', tone: 'danger' as const, onClick: () => setConfirmDelete(true) },
                  ]}
            />
          </div>
        </div>
      </div>

      <Tabs
        className="min-h-0 flex-1"
        tabListClassName="flex-shrink-0 px-7"
        panelClassName="min-h-0 flex-1 overflow-y-auto px-7 pt-5 pb-10"
        ariaLabel={t('mySkills.detailTabsAriaLabel')}
        activeTab={builtin && activeTab === 'versions' ? 'overview' : activeTab}
        onChange={(tab) => setActiveTab(tab as DetailTab)}
        tabs={[
          {
            id: 'overview',
            label: t('mySkills.tabOverview'),
            content: (
              <SkillOverviewPanel
                skill={skill}
                detail={detail}
                onOpenInsights={() => setActiveTab('insights')}
                onOpenConnectors={() => navigate('/connectors')}
                onOpenMarket={() => navigate('/skills')}
              />
            ),
          },
          {
            id: 'insights',
            label: t('mySkills.tabInsights'),
            content: (
              <div className="mx-auto flex w-full max-w-[1120px] flex-col gap-7">
                <SkillTelemetryPanel skill={skill} detail={detail} />
                <SkillUnderstandingPanel
                  skill={skill}
                  detail={detail}
                  refreshing={analysisRefreshing}
                  feedback={analysisFeedback}
                  onRefresh={() => void refreshAnalysis()}
                  onOpenConnectors={() => navigate('/connectors')}
                />
              </div>
            ),
          },
          ...(!builtin ? [{
            id: 'versions',
            label: `${t('mySkills.tabVersions')} · ${detail.versions.length}`,
            content: (
              <SkillVersionsPanel
                detail={detail}
                onRetry={() => void loadRuntimeSkillDetail(skillId)}
                onView={setSnapVer}
                onRestore={setConfirmRestore}
              />
            ),
          }] : []),
        ]}
      />

      <ConfirmDialog
        open={confirmDelete}
        tone="danger"
        title={t('mySkills.deleteConfirmTitle', { name: skill.name })}
        confirmLabel={t('mySkills.deleteConfirmLabel')}
        confirmIcon="trash"
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => {
          setSkills((a) => a.filter((x) => x.id !== skillId));
          void api.deleteSkill(skillId);
          setConfirmDelete(false);
        }}
        description={t('mySkills.deleteConfirmDesc')}
      />

      {/* Skill Market 공개 — 무엇이 공개되는지 미리보기로 보여주고 한 번 더 묻는 전용 흐름 */}
      <SkillPublishDialog
        open={publishOpen}
        skill={skill}
        detail={detail}
        onClose={() => setPublishOpen(false)}
        onConfirm={() => {
          setSkills((a) => a.map((x) => (x.id === skillId ? { ...x, shared: true } : x)));
          // 설치형 재공유 시 원작자 attribution 유지 (ownedToMarketSkill 내부 처리)
          // author:'me' sentinel을 현재 언어로 번역해 마켓 카드에 표시
          publishOwnedSkill({ ...ownedToMarketSkill(skill, detail), author: t('author.previewAuthorMe') });
          setPublishOpen(false);
          // 공개의 실체 — 런타임이 워크스페이스 skill-exports/ 아래에 유통 가능한
          // artifact(디렉토리 + tar.gz + checksum)를 만든다. 유통본과 원본의
          // 계보는 없다(관계 단절). 실패해도 마켓 카드(로컬)는 유지 — 재시도 가능.
          void api.exportSkill(skillId).then((res) => {
            if (res.success && res.data) {
              toast.notify({ message: t('mySkills.exportSuccessMsg', { name: skill.name }), variant: 'success' });
            } else {
              toast.notify({ message: t('mySkills.exportFailMsg', { error: res.error ? ` — ${res.error}` : '' }), variant: 'warning' });
            }
          });
        }}
      />

      <ConfirmDialog
        open={confirmUnshare}
        tone="danger"
        title={t('mySkills.unshareConfirmTitle', { name: skill.name })}
        confirmLabel={t('mySkills.unshareConfirmLabel')}
        confirmIcon="close"
        onClose={() => setConfirmUnshare(false)}
        onConfirm={() => {
          setSkills((a) => a.map((x) => (x.id === skillId ? { ...x, shared: false } : x)));
          unpublishSkill(publishedMarketId(skill.id));
          setConfirmUnshare(false);
        }}
        description={t('mySkills.unshareConfirmDesc')}
      />

      {/* 소스 워크스페이스(풀스크린 IDE) — 편집은 보고 있는 파일을 대상으로 chat-patch 드로어를 띄운다 */}
      <SkillSourceWorkspace
        open={workspaceOpen}
        skill={skill}
        detail={detail}
        onClose={() => setWorkspaceOpen(false)}
        onEdit={builtin ? undefined : (path) => { setEditTarget(path); setEditOpen(true); }}
      />

      {!builtin && (
        <SkillEditDrawer
          open={editOpen}
          skill={skill}
          detail={detail}
          targetPath={editTarget}
          onClose={() => setEditOpen(false)}
          onApply={applyEditPatch}
        />
      )}

      {confirmModal}
    </div>
  );
};

function MySkillsScreen() {
  const { t } = useTranslation('skills');
  const tChat = wrapT(useTranslation('chat').t);
  const navigate = useNavigate();
  const { skills, setSkills, details, hydrating } = useLearning();
  const [filter, setFilter] = useState<Filter>('all');
  const [sel, setSel] = useState<string | null>('s1');
  // "학습 효과" (Learning Impact) panel swaps into the detail pane (LE-1: keep 7 screens)
  const [showImpact, setShowImpact] = useState(false);
  // cold→warm 정량 수치의 정본은 eval harness 산출물뿐이다(GET /eval/impact).
  // 결과가 없으면(404/오프라인) 데모 fixture 로 폴백하되 "데모 데이터"로 명시한다 —
  // 일반 사용 데이터로 성공률을 지어내지 않는 분리 원칙.
  const [impact, setImpact] = useState<ImpactReport | null>(null);
  const [impactLoaded, setImpactLoaded] = useState(false);
  useEffect(() => {
    if (!showImpact || impactLoaded) return;
    void api.getImpactReport().then((res) => {
      setImpactLoaded(true);
      if (res.success && res.data) setImpact(res.data);
    });
  }, [showImpact, impactLoaded]);
  // 수동 "스킬 정리"(curator) 트리거 상태
  const [curating, setCurating] = useState(false);
  const [curateNote, setCurateNote] = useState<string | null>(null);
  // Phase B: curator LLM 통합 제안 — /skills/curate?mode=consolidate 결과를 들고
  // 있다가 사용자가 각 제안을 개별 승인/거부한다(approval_id 없음, §서버 설계).
  const [consolidating, setConsolidating] = useState(false);
  const [consolidateProposals, setConsolidateProposals] = useState<ConsolidateProposal[]>([]);
  const [consolidateNote, setConsolidateNote] = useState<string | null>(null);
  const [consolidateNoteTone, setConsolidateNoteTone] = useState<'error' | 'info'>('info');

  // 하나로 합친 스킬 라이브러리 패널. 필터와 목록을 분리된 두 열로 두면
  // 작은 화면에서 상세 작업공간을 지나치게 압박하므로 한 패널 안에서 탐색한다.
  const listPanel = useResizable({
    initial: 282,
    min: 184,
    max: 360,
    storageKey: 'rice.panel.myskills',
  });

  const FILTER_GROUPS: { labelKey: 'mySkills.filterGroupDefault' | 'mySkills.filterGroupOrigin' | 'mySkills.filterGroupStatus' | 'mySkills.filterGroupUsability'; items: Filter[] }[] = [
    { labelKey: 'mySkills.filterGroupDefault', items: ['all'] },
    { labelKey: 'mySkills.filterGroupStatus', items: ['active', 'attention', 'stale', 'archived', 'inactive'] },
    { labelKey: 'mySkills.filterGroupOrigin', items: ['builtin', 'authored', 'installed', 'learned'] },
    { labelKey: 'mySkills.filterGroupUsability', items: ['frequent', 'recent', 'draft', 'shared'] },
  ];

  const FILTER_LABEL: Record<Filter, string> = {
    all: t('mySkills.filterAll'),
    active: t('mySkills.filterActive'),
    attention: t('mySkills.filterNeedsAttention'),
    builtin: t('mySkills.filterBuiltin'),
    authored: t('mySkills.filterAuthored'),
    installed: t('mySkills.filterInstalled'),
    learned: t('mySkills.filterLearned'),
    stale: t('mySkills.filterStale'),
    archived: t('mySkills.filterArchived'),
    frequent: t('mySkills.filterFrequent'),
    recent: t('mySkills.filterRecent'),
    draft: t('mySkills.filterDraft'),
    shared: t('mySkills.filterShared'),
    inactive: t('mySkills.filterInactive'),
  };

  const list = useMemo(
    () => skills.filter((s) => {
        const detail = details[s.id];
        const analysisNeedsAttention = detail?.analysis?.status === 'stale' || detail?.analysis?.status === 'failed';
        const needsAttention = skillStatus(s) === 'stale'
          || (s.friction ?? 0) > 0
          || (s.routingMiss ?? 0) > 0
          || hasUpstreamUpdate(s.upstream)
          || analysisNeedsAttention;
        const matchesFilter = filter === 'all' ||
          (filter === 'active' && skillStatus(s) === 'active') ||
          (filter === 'attention' && needsAttention) ||
          (filter === 'builtin' && skillOrigin(s) === 'builtin') ||
          (filter === 'authored' && skillOrigin(s) === 'authored') ||
          (filter === 'installed' && skillOrigin(s) === 'installed') ||
          (filter === 'learned' && skillOrigin(s) === 'learned') ||
          (filter === 'frequent' && s.uses >= 10) ||
          (filter === 'recent' && (s.hot === true || s.ver === 'v1')) ||
          (filter === 'draft' && isDraft(s)) ||
          (filter === 'shared' && !!s.shared) ||
          (filter === 'inactive' && skillStatus(s) === 'inactive') ||
          (filter === 'stale' && skillStatus(s) === 'stale') ||
          (filter === 'archived' && skillStatus(s) === 'archived');
        return matchesFilter;
      }),
    [details, filter, skills],
  );
  const active = skills.filter((s) => skillStatus(s) === 'active').length;
  const shared = skills.filter((s) => s.shared).length;

  /** 단일 상태 설정 — 통합 status를 state(생명주기)+enabled(주입)로 매핑한다.
   *  활성=주입 켜고 freshness 리셋 · 비활성=수동 주입 끔(state는 active) · 보관=주입 중단.
   *  enabled는 ChatScreen·패널이 쓰는 "주입 여부"의 진실이라 여기서 함께 갱신한다. */
  const setSkillStatus = (id: string, next: SkillStatus) => {
    setSkills((arr) =>
      arr.map((s) => {
        if (s.id !== id) return s;
        switch (next) {
          case 'active':   return { ...s, state: 'active' as const, enabled: true, idleDays: 0 };
          case 'inactive': return { ...s, state: 'active' as const, enabled: false };
          case 'archived': return { ...s, state: 'archived' as const, enabled: false };
          case 'stale':    return { ...s, state: 'stale' as const, enabled: true };
        }
      }),
    );
    void api.setSkillState(id, next);
  };

  /** 수동 "스킬 정리" (LEARNING_LOOP_DESIGN 5.1 데모 경로 · POST /skills/curate).
   *  큐레이터의 시간 규칙을 지금 즉시 재적용한다: 각 스킬의 idleDays(미사용 일수)로
   *  있어야 할 상태(3일↑ stale, 7일↑ archived)를 계산해, 현재 상태와 어긋난 스킬을 전이.
   *  복귀(→active)는 실제 사용 시 idleDays 가 리셋되며 일어나므로, 정리는 "내려가는"
   *  전이(active→stale→archived)만 적용한다. 각 전이는 "N일 미사용" 사유와 함께 보고. */
  const curateSkills = () => {
    if (curating) return;
    setCurating(true);
    setConsolidateNote(null);
    void api.curateSkills();

    const moves: string[] = [];
    skills.forEach((s) => {
      const next = ruleState(s.idleDays ?? 0);
      if (next !== skillState(s) && next !== 'active') {
        moves.push(t('mySkills.curateTransitionReason', {
          name: s.name,
          transition: next === 'stale' ? t('mySkills.curateTransitionStale') : t('mySkills.curateTransitionArchived'),
          idle: s.idleDays ?? 0,
        }));
      }
    });

    setSkills((arr) =>
      arr.map((s) => {
        const next = ruleState(s.idleDays ?? 0);
        if (next === skillState(s) || next === 'active') return s;
        return { ...s, state: next, enabled: next === 'archived' ? false : s.enabled };
      }),
    );

    setCurateNote(moves.length ? moves.join(' · ') : t('mySkills.curateNoteNone'));
    setTimeout(() => setCurating(false), 600);
  };

  /** Phase B: curator LLM 통합 분석 실행 (POST /skills/curate?mode=consolidate).
   *  스킬 전체 본문을 프롬프트에 태우는 일반 REST 호출이라 규칙 정리보다 훨씬
   *  오래 걸릴 수 있다 — 진짜 로딩 상태가 필요하다(curateSkills의 600ms 흉내와 다름).
   *  결과 proposals는 서버 응답을 그대로 신뢰한다(클라이언트에서 예측 불가능 — LLM
   *  판단이므로 curateSkills의 규칙 기반 클라이언트 예측 패턴을 따르지 않는다). */
  const runConsolidate = async () => {
    if (consolidating) return;
    setConsolidating(true);
    setCurateNote(null);
    setConsolidateNote(null);
    const res = await api.consolidateSkills();
    setConsolidating(false);
    if (!res.success || !res.data) {
      setConsolidateNote(t('mySkills.consolidateFailNote', { error: res.error ? apiErrorMessage(res) : t('mySkills.consolidateFailNoteUnknown') }));
      setConsolidateNoteTone('error');
      return;
    }
    const { proposals } = res.data;
    setConsolidateProposals(proposals);
    setConsolidateNote(
      proposals.length
        ? t('mySkills.consolidateFoundNote', { count: proposals.length })
        : t('mySkills.consolidateNoneNote'),
    );
    setConsolidateNoteTone('info');
  };

  /** 통합 제안 1건 승인/거부. 승인 시 실제로 적용된 결과(서버 응답)로 로컬 skills
   *  목록을 갱신한다 — merge 제안은 새 버전/신규 스킬로, archive 제안은 보관 상태로. */
  const decideProposal = async (p: ConsolidateProposal, approved: boolean) => {
    if (p.kind === 'patch') {
      const res = await api.answerConsolidateDecision(p.skill_id, approved, {
        proposedBody: p.proposed_body,
        description: p.description,
        tags: p.tags,
        fromVersion: p.from_version,
      });
      if (approved && res.success) {
        const newVersion = res.data?.version ?? p.to_version;
        setSkills((arr) => {
          if (arr.some((s) => s.id === p.skill_id)) {
            return arr.map((s) =>
              s.id === p.skill_id ? { ...s, ver: `v${newVersion}`, patches: (s.patches ?? 0) + 1, trend: 'trend_consolidated' } : s,
            );
          }
          return [
            ...arr,
            {
              id: p.skill_id, name: p.skill_id, ver: `v${newVersion}`, uses: 0,
              trend: 'trend_consolidated_new', enabled: true, patches: 0, command: p.skill_id, state: 'active' as const,
            },
          ];
        });
      }
    } else {
      const res = await api.answerArchiveDecision(p.skill_id, approved, p.absorbed_into);
      if (approved && res.success) {
        setSkills((arr) =>
          arr.map((s) => (s.id === p.skill_id ? { ...s, state: 'archived' as const, enabled: false } : s)),
        );
      }
    }
    setConsolidateProposals((arr) => arr.filter((x) => x !== p));
  };

  // keep selection valid as skills change
  const selectedExists = sel && list.some((s) => s.id === sel);
  const hasListConstraint = filter !== 'all';
  const effectiveSel = selectedExists ? sel : list[0]?.id ?? (hasListConstraint ? null : skills[0]?.id) ?? null;

  return (
    <div className="flex flex-1 min-w-0 bg-background overflow-hidden">
      {/* 스킬 라이브러리 — 현황·필터·목록을 하나의 탐색 맥락으로 합친다. */}
      <div
        className="flex-shrink-0 bg-layer-01 border-r border-border-subtle-01 flex flex-col min-w-0 overflow-hidden"
        style={{ width: listPanel.width }}
      >
        <div className="flex-shrink-0 border-b border-border-subtle-01 px-4 pt-5 pb-4">
          <div className="domain-summary domain-summary--skill rounded-xl p-4 text-white">
            <div className="mb-1 text-caption-01 text-white/70">{t('mySkills.libraryTitle')}</div>
            <div className="text-heading-03 font-semibold leading-none text-white tabular-nums">
              {skills.length}
              {t('mySkills.libraryUnit') && (
                <span className="ml-1 text-body-short-01 font-normal text-white/70">{t('mySkills.libraryUnit')}</span>
              )}
            </div>
            <div className="mt-1.5 flex gap-3 text-caption-01 text-white/80">
              <span>{t('mySkills.libraryActiveLabel', { count: active })}</span>
              <span>{t('mySkills.librarySharedLabel', { count: shared })}</span>
            </div>
          </div>

          <div className="mt-3 flex items-center gap-2">
            <label className="relative min-w-0 flex-1 rounded-lg transition-colors duration-150 hover:bg-layer-hover-01">
              <span className="sr-only">{t('mySkills.listFilterLabel')}</span>
              <select
                value={filter}
                onChange={(event) => {
                  setFilter(event.target.value as Filter);
                  setShowImpact(false);
                }}
                className="h-8 w-full appearance-none border-0 bg-transparent pl-2 pr-7 text-caption-01 font-semibold text-text-secondary"
              >
                {FILTER_GROUPS.map((group) => (
                  <optgroup key={group.labelKey} label={t(group.labelKey)}>
                    {group.items.map((item) => <option key={item} value={item}>{FILTER_LABEL[item]}</option>)}
                  </optgroup>
                ))}
              </select>
              <Icon name="chevron-down" size={12} className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-icon-secondary" />
            </label>

            <LibraryManageMenu
              impactActive={showImpact}
              busy={curating || consolidating}
              proposalCount={consolidateProposals.length}
              onImpact={() => setShowImpact(true)}
              onCurate={curateSkills}
              onConsolidate={() => void runConsolidate()}
            />
          </div>
        </div>

        {(curateNote || consolidateNote) && consolidateProposals.length === 0 && (
          <div
            className={`flex-shrink-0 mx-4 my-2 flex items-start gap-1.5 rounded-lg border border-border-subtle-01 bg-background px-2.5 py-2 text-caption-01 ${
              !curateNote && consolidateNoteTone === 'error' ? 'text-support-warning' : 'text-text-helper'
            }`}
            role="status"
            title={curateNote ?? consolidateNote ?? undefined}
          >
            <Icon name={!curateNote && consolidateNoteTone === 'error' ? 'warning' : 'check-circle'} size={12} className="mt-0.5 flex-shrink-0" />
            <span className="line-clamp-2 leading-4">{curateNote ?? consolidateNote}</span>
          </div>
        )}

        <div className="flex-1 min-h-0 overflow-y-auto">
          {/* 통합 제안 검토 목록 — 실행 버튼과 같은 목록 영역에서만 노출해
              AI 기능의 결과가 필터 탐색을 밀어내지 않도록 한다. */}
          {consolidateProposals.length > 0 && (
            <div className="border-b border-border-subtle-01 bg-background px-3 py-3">
              <div className="flex items-center gap-2 px-1 pb-2">
                <span className="text-caption-01 font-semibold text-text-primary">{t('mySkills.consolidateProposalsTitle')}</span>
                <span className="text-caption-01 text-text-helper">{consolidateProposals.length}</span>
              </div>
              <div className="space-y-2">
                {consolidateProposals.map((p, i) => (
                  <div
                    key={`${p.kind}-${p.skill_id}-${i}`}
                    className="rounded-lg border border-border-subtle-01 bg-background p-2.5"
                  >
                    <div className="flex items-center gap-1.5 mb-1">
                      <Icon name={p.kind === 'patch' ? 'merge' : 'trash'} size={12} className="text-icon-secondary flex-shrink-0" />
                      <span className="text-caption-01 font-semibold text-text-primary truncate">{p.skill_id}</span>
                      <Tag type={p.kind === 'patch' ? 'blue' : 'gray'} size="sm">
                        {p.kind === 'patch' ? (p.from_version === 0 ? t('mySkills.consolidateTagNew') : t('mySkills.consolidateTagMerge')) : t('mySkills.consolidateTagArchive')}
                      </Tag>
                    </div>
                    <p className="text-caption-01 text-text-secondary mb-2 line-clamp-2">
                      {p.kind === 'patch' ? p.changelog : p.reason}
                    </p>
                    <div className="flex items-center justify-end gap-1.5">
                      <Button kind="ghost" size="sm" onClick={() => void decideProposal(p, false)}>
                        {t('mySkills.consolidateReject')}
                      </Button>
                      <Button kind="primary" size="sm" onClick={() => void decideProposal(p, true)}>
                        {t('mySkills.consolidateApprove')}
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {list.map((s) => {
            const on = s.id === effectiveSel;
            return (
              <button
                key={s.id}
                onClick={() => {
                  setSel(s.id);
                  setShowImpact(false);
                }}
                className={`group flex w-full items-start gap-2.5 border-b border-l-2 border-border-subtle-01 px-4 py-2.5 text-left transition-colors duration-150 ${
                  on && !showImpact ? 'border-l-interactive bg-layer-selected-01' : 'border-l-transparent bg-transparent hover:bg-layer-hover-01'
                }`}
              >
                <div
                  className={`mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border ${
                    isInjected(skillStatus(s))
                      ? 'border-blue-50/25 bg-blue-60/10 text-blue-40'
                      : 'border-border-subtle-02 bg-layer-03 text-text-helper'
                  }`}
                >
                  <Icon name="skill" size={15} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-body-short-01 font-semibold text-text-primary truncate">{s.name}</span>
                    <span className="text-caption-01 font-mono text-text-helper flex-shrink-0">{s.ver}</span>
                    <StatusBadge s={s} />
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-label-micro text-text-helper">
                    <span>{t(ORIGIN_LABEL_KEYS[skillOrigin(s)])}</span>
                    <span aria-hidden>·</span>
                    <span>{renderTrend(s.trend, tChat)}</span>
                  </div>
                </div>
                {s.shared && <Icon name="share" size={13} className="text-icon-secondary mt-0.5 opacity-70 flex-shrink-0" />}
              </button>
            );
          })}
          {list.length === 0 && (
            <div className="p-10 text-center text-body-short-01 text-text-helper">
              {hydrating ? (
                t('mySkills.listLoading')
              ) : skills.length === 0 ? (
                <div className="flex flex-col items-center gap-3">
                  <span>{t('mySkills.listEmpty')}</span>
                  <button
                    onClick={() => navigate('/skills')}
                    className="inline-flex items-center gap-1.5 text-link-primary hover:underline text-body-short-01"
                  >
                    <Icon name="skill" size={14} />
                    {t('mySkills.listMarketLink')}
                    <Icon name="arrow-up-right" size={13} />
                  </button>
                </div>
              ) : (
                <div className="flex flex-col items-center gap-2">
                  <Icon name="search" size={20} className="text-icon-secondary" />
                  <span>{t('mySkills.listFilterEmpty')}</span>
                  {filter !== 'all' && (
                    <button
                      onClick={() => setFilter('all')}
                      className="text-link-primary hover:underline"
                    >
                      {t('mySkills.listFilterReset')}
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* List resize handle */}
      <ResizeHandle
        onPointerDown={listPanel.startResize}
        onDoubleClick={listPanel.resetWidth}
        isResizing={listPanel.isResizing}
        value={listPanel.width} min={listPanel.min} max={listPanel.max} onResizeBy={listPanel.resizeBy}
        aria-label={t('mySkills.listResizeAriaLabel')}
      />

      {/* detail — skill detail, or the Learning Impact panel (LE-1) */}
      {showImpact ? (
        <LearningImpactPanel report={impact ?? IMPACT_REPORT} demo={!impact} />
      ) : (
        <SkillDetailView skillId={effectiveSel} onSetStatus={setSkillStatus} />
      )}
    </div>
  );
}

export default MySkillsScreen;

// Made with Bob
