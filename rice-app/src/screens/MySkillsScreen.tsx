import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Icon, Tag, ResizeHandle, Toggle, ConfirmDialog, OverflowMenu } from '../components/ui';
import { useResizable } from '../hooks/useResizable';
import { useLearning } from '../lib/learning/LearningContext';
import { useMarketplace } from '../hooks/useMarketplace';
import { LearningImpactPanel } from '../components/carbon/LearningImpactPanel';
import { SkillSourceWorkspace } from '../components/carbon/SkillSourceWorkspace';
import { SkillPublishDialog } from '../components/carbon/SkillPublishDialog';
import { SkillEditDrawer, type EditPatch } from '../components/carbon/SkillEditDrawer';
import { IMPACT_REPORT } from '../lib/learningData';
import { ownedSkillFiles, ownedToMarketSkill, publishedMarketId } from '../lib/skillBridge';
import { languageFromPath } from '../lib/skillFiles';
import { api } from '../lib/api';
import type { Skill, SkillDetail, SkillRule, SkillVersion, SkillHealth, SkillActivity, SkillOrigin, SkillUpstream } from '../lib/learningData';
import type { IconName } from '../components/ui';
import type { ConsolidateProposal } from '../lib/types';

/**
 * MySkillsScreen ("내 스킬") — full management screen for the user's own learned
 * skills. Master-detail: edit the rules a skill learned, see version history,
 * where it's used, which data it touches, and share to the Skill Market.
 */

// 필터를 3축으로 그룹핑 — 출처(내가 만듦/설치/학습) · 상태(생명주기) · 사용성.
// 출처가 새 직교 축이라, 한 줄로 평평히 두지 않고 섹션 라벨로 묶는다.
type Filter =
  | '전체'
  | '직접 제작'
  | '설치됨'
  | '학습됨'
  | 'stale'
  | '보관됨'
  | '자주 사용'
  | '최근 학습'
  | '초안'
  | '공유됨'
  | '비활성';

const FILTER_GROUPS: { label: string | null; items: Filter[] }[] = [
  { label: null, items: ['전체'] },
  { label: '출처', items: ['직접 제작', '설치됨', '학습됨'] },
  { label: '상태', items: ['stale', '보관됨'] },
  { label: '사용성', items: ['자주 사용', '최근 학습', '초안', '공유됨', '비활성'] },
];

// 생명주기 상태 배지 메타 (LEARNING_LOOP_DESIGN 5.1)
// ── 통합 상태축 — 생명주기(state)와 주입(enabled)을 하나의 사용자 표현으로 합친다.
// active·stale = 주입됨, archived·inactive = 주입 중단. enabled는 ChatScreen·패널이
// 쓰는 "주입 여부"의 진실로 유지하고, UI에서는 여기 단일 status로만 투영한다.
type SkillStatus = 'active' | 'stale' | 'archived' | 'inactive';

const STATUS_META: Record<SkillStatus, { label: string; tag: 'gray' | 'yellow'; dot: string; text: string; desc: string }> = {
  active:   { label: '활성',   tag: 'gray',   dot: 'bg-support-success', text: 'text-support-success', desc: '대화에 주입 중' },
  stale:    { label: 'Stale',  tag: 'yellow', dot: 'bg-support-warning', text: 'text-support-warning', desc: '사용이 줄어 재확인 권장 · 주입 중' },
  archived: { label: '보관',   tag: 'gray',   dot: 'bg-icon-secondary',  text: 'text-text-helper',     desc: '오래 미사용 · 주입 중단' },
  inactive: { label: '비활성', tag: 'gray',   dot: 'bg-icon-secondary',  text: 'text-text-helper',     desc: '수동으로 주입 끔' },
};

/** 생명주기 상태 (큐레이터 내부용, 미설정 = active) */
const skillState = (s: Skill): NonNullable<Skill['state']> => s.state ?? 'active';

/** 통합 표현 상태 — state(생명주기) + enabled(주입)을 하나로 투영.
 *  archived(자동 중단) > inactive(수동 중단) > stale/active(주입 중) 순으로 결정. */
const skillStatus = (s: Skill): SkillStatus => {
  const st = skillState(s);
  if (st === 'archived') return 'archived';
  if (!s.enabled) return 'inactive';
  return st; // active | stale (둘 다 주입 중)
};
/** 이 상태가 "주입 중"을 의미하는가 */
const isInjected = (st: SkillStatus) => st === 'active' || st === 'stale';

// ── 출처(provenance) — 내가 만든 것 vs 다운받은 것 (git fork 모델) ──────
const ORIGIN_META: Record<SkillOrigin, { label: string; icon: IconName; cls: string }> = {
  learned: { label: '학습됨', icon: 'idea', cls: 'text-icon-primary' },
  installed: { label: '설치됨', icon: 'download', cls: 'text-link-primary' },
  authored: { label: '직접 제작', icon: 'user', cls: 'text-purple-60' },
};
/** 현재 출처 반환 (미설정 = learned) */
const skillOrigin = (s: Skill): SkillOrigin => s.origin ?? 'learned';

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

/** 배지·버튼 툴팁에 붙는 "왜 이 상태인지" 사유 */
const statusReason = (s: Skill): string => {
  const idle = s.idleDays ?? 0;
  switch (skillStatus(s)) {
    case 'stale':    return `${idle}일 미사용 — 정리를 고려하세요 · 주입 중`;
    case 'archived': return `${idle}일 미사용 · 주입 중단 — 필요하면 다시 활성화하세요`;
    case 'inactive': return '수동으로 주입을 껐어요 — 대화에 주입되지 않아요';
    default:         return idle > 0 ? `${idle}일 전 사용 · 주입 중` : '주입 중 · 정상';
  }
};

// 초안 = 갓 생성돼 아직 안 다듬어진 스킬 (v1 · 사용 적음)
const isDraft = (s: Skill) => s.ver === 'v1' && s.uses < 5;

// 상태 배지 — active 만 생략(기본값), stale·보관·비활성은 표시
const StatusBadge: React.FC<{ s: Skill }> = ({ s }) => {
  const st = skillStatus(s);
  if (st === 'active') return null;
  const m = STATUS_META[st];
  return (
    <span title={statusReason(s)}>
      <Tag type={m.tag} size="sm">
        {m.label}
      </Tag>
    </span>
  );
};

// 목록 행의 출처 글리프 — 내가 만든 것/설치한 것을 한눈에 구분 (학습=💡 설치=⬇ 제작=👤)
const OriginGlyph: React.FC<{ s: Skill }> = ({ s }) => {
  const m = ORIGIN_META[skillOrigin(s)];
  return (
    <span title={`출처: ${m.label}`} className="flex-shrink-0">
      <Icon name={m.icon} size={11} className={`${m.cls} opacity-80`} />
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
  const [open, setOpen] = useState(false);
  const ref = React.useRef<HTMLDivElement>(null);
  const st = skillStatus(skill);
  const m = STATUS_META[st];

  // 외부 클릭 닫기
  React.useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, [open]);

  const targets = SETTABLE_STATUSES.filter((t) => t !== st);

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
        <span className={`w-1.5 h-1.5 rounded-full ${m.dot}`} />
        {m.label}
        <Icon name="chevron-down" size={11} className="opacity-60" />
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 z-50 min-w-[208px] bg-background border border-border-subtle-01 rounded-lg shadow-elevation-2 overflow-hidden">
          <div className="px-3 py-2 text-caption-01 text-text-helper border-b border-border-subtle-01">
            상태 변경 · 주입 여부가 함께 정해져요
          </div>
          {targets.map((t) => {
            const tm = STATUS_META[t];
            return (
              <button
                key={t}
                onClick={() => { onSet(t); setOpen(false); }}
                className="flex items-start gap-2.5 w-full text-left px-3 py-2 hover:bg-layer-hover-01 transition-colors"
              >
                <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 mt-1.5 ${tm.dot}`} />
                <span className="min-w-0">
                  <span className="block text-body-short-01 text-text-primary">{tm.label}</span>
                  <span className="block text-caption-01 text-text-helper">{tm.desc}</span>
                </span>
              </button>
            );
          })}
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

// 규칙 학습 신뢰도 — 근거(교정·예시) 양에 따른 확신 정도
const CONFIDENCE_META: Record<NonNullable<SkillRule['confidence']>, { label: string; cls: string }> = {
  high: { label: '신뢰 높음', cls: 'text-support-success' },
  medium: { label: '신뢰 보통', cls: 'text-text-helper' },
  low: { label: '신뢰 낮음', cls: 'text-support-warning' },
};

// 최근 활동 종류별 아이콘·색
const ACTIVITY_META: Record<SkillActivity['kind'], { icon: IconName; cls: string }> = {
  use: { icon: 'check-circle', cls: 'text-support-success' },
  edit: { icon: 'edit', cls: 'text-link-primary' },
  learn: { icon: 'idea', cls: 'text-icon-primary' },
  share: { icon: 'share', cls: 'text-purple-60' },
  restore: { icon: 'renew', cls: 'text-support-warning' },
};

// 처리 턴 추세 미니 스파크라인 — 내려갈수록(턴 감소) 개선이라 초록, 악화면 노랑
const Sparkline: React.FC<{ points: number[] }> = ({ points }) => {
  if (!points || points.length < 2) return null;
  const w = 60, h = 20, pad = 2;
  const min = Math.min(...points), max = Math.max(...points);
  const range = max - min || 1;
  const stepX = (w - pad * 2) / (points.length - 1);
  const yy = (v: number) => pad + ((max - v) / range) * (h - pad * 2);
  const d = points.map((v, i) => `${i === 0 ? 'M' : 'L'}${(pad + i * stepX).toFixed(1)},${yy(v).toFixed(1)}`).join(' ');
  const improving = points[points.length - 1] <= points[0];
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} fill="none" className={improving ? 'text-support-success' : 'text-support-warning'}>
      <path d={d} stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={pad + (points.length - 1) * stepX} cy={yy(points[points.length - 1])} r="1.9" fill="currentColor" />
    </svg>
  );
};

// pp 증감 배지 (+8pp / -5pp)
const DeltaBadge: React.FC<{ delta: number }> = ({ delta }) => {
  if (!delta) return null;
  const up = delta > 0;
  return (
    <span className={`inline-flex items-center text-caption-01 font-semibold ${up ? 'text-support-success' : 'text-support-error'}`}>
      <Icon name={up ? 'chevron-up' : 'chevron-down'} size={11} />
      {Math.abs(Math.round(delta * 100))}pp
    </span>
  );
};

// 헤더의 "성과" 카드 — 성공률·효율·절감을 한눈에 (실서비스 질감)
const HealthCard: React.FC<{ health: SkillHealth; uses: number }> = ({ health, uses }) => {
  const ratingTotal = health.rating ? health.rating.up + health.rating.down : 0;
  return (
    <div className="rounded-xl border border-border-subtle-01 bg-layer-01 p-4">
      <div className="flex items-center mb-3">
        <span className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">성과</span>
        {health.spark && (
          <span className="ml-auto flex items-center gap-1.5">
            <span className="text-caption-01 text-text-helper">처리 추세</span>
            <Sparkline points={health.spark} />
          </span>
        )}
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div>
          <div className="text-caption-01 text-text-helper">성공률</div>
          <div className="flex items-baseline gap-1.5">
            <span className="text-heading-03 font-light text-text-primary">{pctStr(health.successRate)}</span>
            {health.successDelta != null && <DeltaBadge delta={health.successDelta} />}
          </div>
        </div>
        <div>
          <div className="text-caption-01 text-text-helper">평균 처리</div>
          <div className="flex items-baseline gap-1">
            <span className="text-heading-03 font-light text-text-primary">{health.avgTurns}</span>
            <span className="text-caption-01 text-text-helper">턴</span>
            {health.coldTurns != null && <span className="text-caption-01 text-support-success ml-0.5" title="스킬 없이 직접 했을 때">vs {health.coldTurns}턴</span>}
          </div>
        </div>
        <div>
          <div className="text-caption-01 text-text-helper">회당 절감</div>
          <div className="flex items-baseline gap-1">
            <span className="text-heading-03 font-light text-text-primary">~{health.timeSavedMin}</span>
            <span className="text-caption-01 text-text-helper">분</span>
          </div>
        </div>
      </div>
      {ratingTotal > 0 && (
        <div className="mt-3 pt-3 border-t border-border-subtle-01 flex items-center gap-3 text-caption-01">
          <span className="inline-flex items-center gap-1 text-support-success"><Icon name="thumbs-up" size={13} /> {health.rating!.up}</span>
          <span className="inline-flex items-center gap-1 text-text-helper"><Icon name="thumbs-down" size={13} /> {health.rating!.down}</span>
          <span className="text-text-helper ml-auto">최근 {uses}회 사용 기준</span>
        </div>
      )}
    </div>
  );
};

// one editable rule — 규칙 텍스트 + 출처 + (적용 횟수·신뢰도·최근 적용) 텍스처
const RuleRow: React.FC<{ r: SkillRule; onToggle: () => void; onRemove: () => void }> = ({ r, onToggle, onRemove }) => {
  const conf = r.confidence ? CONFIDENCE_META[r.confidence] : null;
  return (
    <div className={`flex items-start gap-2.5 bg-background px-3.5 py-2.5 ${r.on ? '' : 'opacity-50'}`}>
      <div className="flex-1 min-w-0">
        <div className={`text-body-short-01 text-text-primary leading-[18px] ${r.on ? '' : 'line-through'}`}>{r.text}</div>
        <div className="flex items-center gap-x-2.5 gap-y-1 mt-1 flex-wrap text-caption-01 text-text-helper">
          <span className="inline-flex items-center gap-1">
            <Icon name={r.source.includes('교정') ? 'edit' : r.source.includes('예시') ? 'idea' : 'view'} size={11} />
            {r.source}
          </span>
          {typeof r.applied === 'number' && (
            <span className="inline-flex items-center gap-1">
              <span className="w-px h-2.5 bg-border-strong-01" />
              {r.applied}회 적용
            </span>
          )}
          {conf && (
            <span className={`inline-flex items-center gap-1 ${conf.cls}`}>
              <span className="w-1 h-1 rounded-full bg-current" />
              {conf.label}
            </span>
          )}
          {r.lastApplied && <span className="text-text-helper/80">· {r.lastApplied}</span>}
        </div>
      </div>
      <div className="flex-shrink-0 mt-0.5">
        <Toggle checked={r.on} onChange={onToggle} />
      </div>
      <button
        onClick={onRemove}
        title="규칙 삭제"
        aria-label="규칙 삭제"
        className="w-6 h-6 flex items-center justify-center text-icon-secondary hover:text-text-primary opacity-60 hover:opacity-100"
      >
        <Icon name="close" size={14} />
      </button>
    </div>
  );
};

// next version label, e.g. v3 → v4
const bumpVer = (v: string) => {
  const m = v.match(/\d+/);
  return m ? v.replace(/\d+/, String(Number(m[0]) + 1)) : `${v}.1`;
};

// 헤더의 버전 칩 — 클릭하면 버전 목록이 펼쳐져, 이전 버전 내용을 보거나
// 그 버전으로 바로 전환(복원)할 수 있다. 전환은 상위에서 확인 모달로 한 번 더 묻는다.
const VersionChipMenu: React.FC<{
  versions: SkillVersion[];
  currentVer: string;
  onView: (ver: string) => void;
  onSwitch: (v: SkillVersion) => void;
}> = ({ versions, currentVer, onView, onSwitch }) => {
  const [open, setOpen] = useState(false);
  const ref = React.useRef<HTMLDivElement>(null);

  // 외부 클릭 닫기
  React.useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        title="버전 전환 · 내용 보기"
        aria-haspopup="listbox"
        aria-expanded={open}
        className="inline-flex items-center gap-1 text-caption-01 font-mono text-text-secondary bg-layer-02 border border-border-subtle-01 rounded px-1.5 py-px hover:bg-layer-hover-01"
      >
        {currentVer}
        <Icon name="chevron-down" size={12} className={`opacity-70 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 z-50 w-[268px] bg-background border border-border-subtle-01 rounded-lg shadow-elevation-2 overflow-hidden">
          <div className="px-3 py-2 text-caption-01 text-text-helper border-b border-border-subtle-01 normal-case">
            버전을 선택해 전환하거나 내용을 봅니다
          </div>
          <div className="max-h-[280px] overflow-y-auto">
            {versions.map((v) => {
              const isCurrent = v.current ?? v.ver === currentVer;
              return (
                <div
                  key={v.ver}
                  className={`flex items-center gap-2 px-3 py-2 ${isCurrent ? 'bg-layer-selected-01' : 'hover:bg-layer-hover-01'} transition-colors`}
                >
                  <button
                    onClick={() => { onView(v.ver); setOpen(false); }}
                    title="이 버전 내용 보기"
                    className="flex-1 min-w-0 text-left"
                  >
                    <div className="flex items-center gap-1.5">
                      <span className="text-body-short-01 font-mono font-semibold text-text-primary">{v.ver}</span>
                      {isCurrent && <Tag type="blue" size="sm">현재</Tag>}
                      <span className="text-caption-01 text-text-helper">{v.when}</span>
                      {v.metrics && (
                        <span className="ml-auto text-caption-01 font-mono text-text-helper flex-shrink-0">성공률 {pctStr(v.metrics.successRate)}</span>
                      )}
                    </div>
                    <div className="text-caption-01 text-text-helper truncate mt-0.5">{v.note}</div>
                  </button>
                  {isCurrent ? (
                    <Icon name="check" size={14} className="text-link-primary flex-shrink-0" />
                  ) : (
                    <button
                      onClick={() => { onSwitch(v); setOpen(false); }}
                      title={`${v.ver}(으)로 전환`}
                      className="group/sw flex-shrink-0 inline-flex items-center gap-1 text-caption-01 font-semibold text-support-error border border-support-error/50 rounded px-2 py-1 hover:bg-support-error hover:text-white hover:border-support-error transition-colors"
                    >
                      <Icon name="renew" size={12} className="transition-transform group-hover/sw:rotate-180" /> 전환
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
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
const CompareRow: React.FC<{ label: string; view: string; cur: string; tone: 'good' | 'bad' | 'neutral' }> = ({ label, view, cur, tone }) => (
  <div className="flex items-center gap-2 py-1.5">
    <span className="text-caption-01 text-text-helper w-20 flex-shrink-0">{label}</span>
    <span className={`text-body-short-01 font-mono font-semibold ${TONE_CLS[tone]}`}>{view}</span>
    <Icon name="arrow-right" size={12} className="text-icon-secondary opacity-40" />
    <span className="text-body-short-01 font-mono text-text-helper">{cur} <span className="text-caption-01">현재</span></span>
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
  const rules = version.rules ?? [];
  const added = rules.filter((r) => r.added).length;
  const m = version.metrics;

  // ── 복원 판단 근거 (현재 버전이 아닐 때만) ──
  const decision = (() => {
    if (version.current || !currentVersion) return null;
    const curTexts = new Set((currentVersion.rules ?? []).map((r) => normRule(r.text)));
    const viewTexts = new Set(rules.map((r) => normRule(r.text)));
    const gain = rules.filter((r) => !curTexts.has(normRule(r.text))); // 복원하면 다시 생기는 규칙
    const lose = (currentVersion.rules ?? []).filter((r) => !viewTexts.has(normRule(r.text))); // 복원하면 사라지는 규칙
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
      title: `이 버전이 현재(${currentVersion?.ver})보다 성공률이 높았어요`,
      body: '복원을 고려해볼 만해요. 아래 규칙 차이를 확인하세요.',
    },
    caution: {
      cls: 'border-support-warning/40 bg-support-warning/10',
      icon: 'warning' as IconName,
      iconCls: 'text-support-warning',
      title: `현재(${currentVersion?.ver}) 버전이 더 좋은 성과를 내고 있어요`,
      body: '복원하면 성과가 낮았던 설정으로 되돌아갑니다. 신중히 결정하세요.',
    },
    neutral: {
      cls: 'border-border-subtle-01 bg-layer-01',
      icon: 'idea' as IconName,
      iconCls: 'text-icon-primary',
      title: '현재 버전과 성과 차이가 뚜렷하지 않아요',
      body: '아래 규칙 차이를 보고 결정하세요.',
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
          {version.current ? <Tag type="blue" size="sm">현재 버전</Tag> : <Tag type="gray" size="sm">이전 버전</Tag>}
          <span className="text-caption-01 text-text-helper">{version.when}</span>
          {version.author && (
            <span className="inline-flex items-center gap-1 text-caption-01 text-text-helper">
              <Icon name="user" size={11} /> {version.author}
            </span>
          )}
        </div>
        <p className="mt-2 text-body-short-01 leading-5 text-text-secondary">{version.note}</p>

        {/* 이 버전의 성과 지표 칩 */}
        {m && (
          <div className="flex gap-4 mt-4 flex-wrap">
            {([
              ['성공률', pctStr(m.successRate)],
              ['사용', `${m.uses}회`],
              ...(m.avgTurns != null ? [['평균 처리', `${m.avgTurns}턴`] as const] : []),
              ...(m.editRate != null ? [['사후 수정', pctStr(m.editRate)] as const] : []),
            ] as const).map(([k, v]) => (
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
                <CompareRow label="성공률" view={pctStr(m.successRate)} cur={pctStr(decision.cM.successRate)} tone={compareTone(m.successRate, decision.cM.successRate)} />
                {m.avgTurns != null && decision.cM.avgTurns != null && (
                  <CompareRow label="평균 처리" view={`${m.avgTurns}턴`} cur={`${decision.cM.avgTurns}턴`} tone={compareTone(m.avgTurns, decision.cM.avgTurns, true)} />
                )}
                {m.editRate != null && decision.cM.editRate != null && (
                  <CompareRow label="사후 수정" view={pctStr(m.editRate)} cur={pctStr(decision.cM.editRate)} tone={compareTone(m.editRate, decision.cM.editRate, true)} />
                )}
              </div>
            )}

            {/* 복원 시 규칙 변화 */}
            <div className="mt-3 pt-3 border-t border-border-subtle-01/60 flex flex-col gap-2">
              {decision.lose.length === 0 && decision.gain.length === 0 ? (
                <div className="text-caption-01 text-text-helper">현재 버전과 규칙이 동일해요.</div>
              ) : (
                <>
                  {decision.lose.map((r, i) => (
                    <div key={`lose-${i}`} className="flex items-center gap-2 text-body-short-01">
                      <Icon name="close" size={13} className="text-support-error flex-shrink-0" />
                      <span className="text-text-secondary line-through decoration-support-error/50">{r.text}</span>
                      <span className="text-caption-01 text-support-error flex-shrink-0 ml-auto">사라짐</span>
                    </div>
                  ))}
                  {decision.gain.map((r, i) => (
                    <div key={`gain-${i}`} className="flex items-center gap-2 text-body-short-01">
                      <Icon name="add" size={13} className="text-support-success flex-shrink-0" />
                      <span className="text-text-primary">{r.text}</span>
                      <span className="text-caption-01 text-support-success flex-shrink-0 ml-auto">다시 추가</span>
                    </div>
                  ))}
                </>
              )}
            </div>
          </div>
        )}

        <div className="mt-6">
          <div className="flex items-center mb-2.5">
            <span className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">
              이 버전의 규칙 · {rules.length}
            </span>
            {added > 0 && <span className="ml-auto text-caption-01 text-link-primary">+{added} 이 버전에서 추가</span>}
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
                {r.added && <Tag type="blue" size="sm">추가됨</Tag>}
              </div>
            ))}
            {rules.length === 0 && (
              <div className="bg-background px-3.5 py-3.5 text-caption-01 text-text-helper">이 버전의 규칙 스냅샷이 없어요.</div>
            )}
          </div>
        </div>

        {version.current ? (
          <div className="mt-6 pt-4 border-t border-border-subtle-01 flex items-center gap-1.5 text-caption-01 text-text-helper">
            <Icon name="check-circle" size={14} className="text-support-success" /> 지금 적용 중인 버전이에요.
          </div>
        ) : (
          <div className="mt-6 pt-4 border-t border-border-subtle-01 flex items-center gap-3 flex-wrap">
            <Button kind="danger" size="sm" icon={<Icon name="renew" size={15} />} onClick={onRestore}>
              이 버전 복원
            </Button>
            <span className="text-caption-01 text-text-helper">복원하면 새 버전으로 저장돼요. 기록은 지워지지 않아요.</span>
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
  const origin = skillOrigin(skill);

  if (origin === 'authored') {
    return (
      <div className="mt-3 inline-flex items-center gap-2 rounded-lg border border-border-subtle-01 bg-layer-01 px-3 py-2 text-caption-01">
        <Icon name="user" size={14} className="text-purple-60 flex-shrink-0" />
        <span className="text-text-secondary">
          내가 직접 제작{skill.shared ? ' · Skill Market에 공유됨' : ''}
        </span>
      </div>
    );
  }

  if (origin === 'installed' && skill.upstream) {
    const u = skill.upstream;
    const diverged = divergenceCount(detail.versions, u.installedVer);
    const update = hasUpstreamUpdate(u);
    return (
      <div className="mt-3 rounded-lg border border-border-subtle-01 bg-layer-01 px-3 py-2.5">
        <div className="flex items-center gap-2 text-caption-01 flex-wrap">
          <Icon name="download" size={14} className="text-link-primary flex-shrink-0" />
          <span className="text-text-secondary">
            <span className="text-text-primary font-semibold">{u.author}</span>
            {u.team ? ` · ${u.team}` : ''} 에서 설치
          </span>
          <span className="w-px h-3 bg-border-strong-01" />
          <span className="font-mono text-text-helper">{u.installedVer} 기준</span>
          {diverged > 0 && (
            <>
              <span className="w-px h-3 bg-border-strong-01" />
              <span className="inline-flex items-center gap-1 text-support-success">
                <Icon name="growth" size={12} /> 내 개선 {diverged}건
              </span>
            </>
          )}
        </div>
        {update && (
          <div className="mt-2 pt-2 border-t border-border-subtle-01 flex items-center gap-2 flex-wrap">
            <Icon name="renew" size={14} className="text-support-warning flex-shrink-0" />
            <span className="text-caption-01 text-text-secondary">
              원작자가 <span className="font-mono font-semibold text-text-primary">{u.latestVer}</span>를 올렸어요 — 당신은{' '}
              <span className="font-mono">{u.installedVer}</span> 기준 + 내 개선 {diverged}건
            </span>
            <button
              onClick={onOpenMarket}
              className="ml-auto inline-flex items-center gap-1 text-caption-01 font-semibold text-link-primary hover:underline flex-shrink-0"
            >
              마켓에서 업데이트 <Icon name="arrow-up-right" size={12} />
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

// 소스 보기 런처 — 풀스크린 IDE 워크스페이스로 진입. 전체화면 전환이라 일반 버튼보다
// 임팩트 있게: 번들 규모(파일·폴더 수)를 보여주는 카드형 + 그라데이션 아이콘 + 확장 화살표.
const SourceLauncher: React.FC<{ skill: Skill; detail: SkillDetail; onOpen: () => void }> = ({ skill, detail, onOpen }) => {
  const { files } = ownedSkillFiles(skill, detail);
  const folders = new Set(files.filter((f) => f.path.includes('/')).map((f) => f.path.slice(0, f.path.indexOf('/'))));
  return (
    <button
      onClick={onOpen}
      title="이 스킬의 파일(SKILL.md·scripts…)을 전체 화면으로 보기"
      className="group relative flex items-center gap-3 rounded-xl border border-border-subtle-01 bg-layer-01 pl-3 pr-3.5 py-2.5 hover:border-border-strong-01 transition-colors flex-shrink-0"
    >
      <div className="w-9 h-9 rounded-lg bg-layer-02 border border-border-subtle-02 flex items-center justify-center flex-shrink-0 text-blue-50">
        <Icon name="terminal" size={18} />
      </div>
      <div className="text-left">
        <div className="flex items-center gap-1 text-body-short-01 font-semibold text-text-primary">
          소스 보기
          <Icon
            name="arrow-up-right"
            size={13}
            className="text-link-primary opacity-70 group-hover:opacity-100 transition-opacity"
          />
        </div>
        <div className="text-caption-01 text-text-helper">
          {files.length} 파일{folders.size > 0 ? ` · ${folders.size} 폴더` : ''} · 전체화면
        </div>
      </div>
    </button>
  );
};

const SkillDetailView: React.FC<{
  skillId: string | null;
  onSetStatus: (id: string, next: SkillStatus) => void;
}> = ({ skillId, onSetStatus }) => {
  const navigate = useNavigate();
  const { skills, setSkills, details, setDetails } = useLearning();
  const { publishOwnedSkill, unpublishSkill } = useMarketplace();
  const [snapVer, setSnapVer] = useState<string | null>(null);
  // 풀스크린 소스 워크스페이스(IDE) 열림 여부
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
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
  // 규칙 편집은 자동 저장 — 변경 직후 잠깐 "자동 저장됨"을 띄워 저장 사실을 알린다.
  const [ruleSaved, setRuleSaved] = useState(false);
  const savedTimer = React.useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const flashSaved = () => {
    setRuleSaved(true);
    clearTimeout(savedTimer.current);
    savedTimer.current = setTimeout(() => setRuleSaved(false), 1800);
  };
  // leaving a skill closes any open version snapshot / 확인 모달 / 저장 표시 / 소스·편집
  useEffect(() => {
    setSnapVer(null);
    setConfirmRestore(null);
    setRuleSaved(false);
    setWorkspaceOpen(false);
    setEditOpen(false);
    setEditTarget(undefined);
    setPublishOpen(false);
    setConfirmUnshare(false);
    setConfirmDelete(false);
  }, [skillId]);
  useEffect(() => () => clearTimeout(savedTimer.current), []);

  const skill = skills.find((s) => s.id === skillId);
  const detail = skillId ? details[skillId] : undefined;

  if (!skill || !detail || !skillId) {
    return <div className="flex-1 flex items-center justify-center text-body-short-02 text-text-helper">스킬을 선택하세요.</div>;
  }

  const patch = (fn: (d: SkillDetail) => SkillDetail) => setDetails((d) => ({ ...d, [skillId]: fn(d[skillId]) }));
  const appliedRules = detail.rules.filter((r) => r.on).length;

  // restore a past version: save it as a new current version (history kept)
  const restore = (version: SkillVersion) => {
    const newVer = bumpVer(skill.ver);
    setSkills((a) => a.map((x) => (x.id === skillId ? { ...x, ver: newVer } : x)));
    patch((d) => ({
      ...d,
      rules: (version.rules ?? []).map((r, i) => ({
        id: `rv${Date.now()}-${i}`,
        text: r.text,
        source: `${version.ver}에서 복원`,
        on: true,
      })),
      versions: [
        {
          ver: newVer,
          when: '방금',
          note: `${version.ver} 복원`,
          current: true,
          rules: (version.rules ?? []).map((r) => ({ text: r.text })),
        },
        ...d.versions.map((v) => ({ ...v, current: false })),
      ],
    }));
    setSnapVer(null);
  };

  // 편집 어시스턴트 적용 — patch 승인 → version++ + 대상 파일 갱신 + 활동 기록.
  // SKILL.md 편집이면 학습 규칙도 등록(p.newRuleText), 코드/문서 파일 편집이면 파일만 갱신.
  // 규칙 토글(ON/OFF)은 무버전 설정이지만, 이렇게 본문·파일을 바꾸는 편집은 버전을 만든다.
  const applyEditPatch = (p: EditPatch, editedBody?: string) => {
    const finalBody = editedBody ?? p.proposedBody;
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

      // SKILL.md 편집일 때만 학습 규칙 등록 (코드 파일 편집은 규칙 없음)
      const newRule: SkillRule | null = p.newRuleText
        ? { id: 'r' + Date.now(), text: p.newRuleText, source: '편집 어시스턴트', on: true, confidence: 'medium', applied: 0, lastApplied: '방금' }
        : null;
      const rules = newRule ? [...d.rules, newRule] : d.rules;

      return {
        ...d,
        files,
        rules,
        versions: [
          {
            ver: p.toVer,
            when: '방금',
            note: p.changelog,
            current: true,
            author: '편집 어시스턴트',
            rules: rules.map((r) => (newRule && r.id === newRule.id ? { text: r.text, added: true } : { text: r.text })),
          },
          ...d.versions.map((v) => ({ ...v, current: false })),
        ],
        activity: [
          { when: '방금', text: `편집 어시스턴트로 ${p.toVer} 발행 — ${p.targetLabel}: ${p.changelog}`, kind: 'edit' as const },
          ...(d.activity ?? []),
        ],
      };
    });
    flashSaved();
  };

  // 복원 확인 팝업 — 칩 메뉴의 "전환"과 스냅샷의 "이 버전 복원" 두 경로가 모두 거친다.
  const confirmModal = confirmRestore && (
    <ConfirmDialog
      open
      tone="danger"
      title="이 버전으로 복원할까요?"
      confirmLabel={`${confirmRestore.ver}(으)로 복원`}
      confirmIcon="renew"
      onClose={() => setConfirmRestore(null)}
      onConfirm={() => {
        restore(confirmRestore);
        setConfirmRestore(null);
      }}
      description={
        <>
          지금 적용 중인 규칙이 <span className="font-mono font-semibold text-text-primary">{confirmRestore.ver}</span> 시점의
          규칙으로 교체돼요. 복원은 새 버전(<span className="font-mono">{bumpVer(skill.ver)}</span>)으로 저장되며, 기존 버전
          기록은 지워지지 않아요.
        </>
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
          <div className="w-11 h-11 flex-shrink-0 flex items-center justify-center rounded-lg bg-layer-02 border border-border-subtle-02">
            <Icon name="skill" size={22} className={isInjected(skillStatus(skill)) ? 'text-icon-primary' : 'text-icon-secondary'} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-heading-03 font-semibold text-text-primary">{skill.name}</h2>
              <VersionChipMenu
                versions={detail.versions}
                currentVer={skill.ver}
                onView={(ver) => setSnapVer(ver)}
                onSwitch={(v) => setConfirmRestore(v)}
              />
              {skill.shared && (
                <Tag type="purple" size="sm">
                  공유됨
                </Tag>
              )}
              {/* 단일 상태 컨트롤 — 활성/Stale/보관/비활성 (주입 여부 포함) */}
              <StatusControl skill={skill} onSet={(next) => onSetStatus(skill.id, next)} />
            </div>
            <p className="mt-1.5 text-body-short-01 leading-5 text-text-secondary">{detail.summary}</p>
            <div className="flex gap-4 mt-3 flex-wrap">
              {([
                ['사용', `${skill.uses}회`],
                ['개선', `${skill.patches ?? 0}회`],
                ['생성', detail.createdFrom],
                ['최근 사용', detail.lastUsed],
              ] as const).map(([k, v]) => (
                <div key={k}>
                  <div className="text-caption-01 text-text-helper">{k}</div>
                  <div className="text-body-short-01 text-text-primary">{v}</div>
                </div>
              ))}
            </div>
            {/* 출처 스트립 — 설치형/제작형일 때 원본·발산·업데이트를 표시 (학습형은 생략) */}
            <ProvenanceStrip skill={skill} detail={detail} onOpenMarket={() => navigate('/skills')} />
          </div>
          {/* 소스 보기 — 풀스크린 IDE 워크스페이스 진입. 전체화면 전환이라 임팩트 있게. */}
          <SourceLauncher skill={skill} detail={detail} onOpen={() => setWorkspaceOpen(true)} />
        </div>
      </div>

      {/* scrollable body — 학습 섹션 (소스는 풀스크린 워크스페이스로 분리) */}
      <div className="flex-1 min-w-0 overflow-y-auto px-7 pt-5 pb-10 flex flex-col gap-7">
        {/* performance card — 성공률·효율·절감 한눈에 */}
        {detail.health && <HealthCard health={detail.health} uses={skill.uses} />}

        {/* 절차 단계 — 이 스킬이 엮는 도구 호출의 순서 (steps 가 있는 스킬만 표시) */}
        {detail.steps && detail.steps.length > 0 && (
          <div>
            <SectionLabel right={<span className="text-caption-01 text-text-helper normal-case">순서대로 실행 · 각 단계가 쓰는 도구 표시</span>}>
              단계 · {detail.steps.length}
            </SectionLabel>
            <div className="flex flex-col">
              {detail.steps.map((st, i) => (
                <div key={i} className="flex gap-3">
                  <div className="flex flex-col items-center flex-shrink-0">
                    <div className="w-7 h-7 rounded-lg bg-layer-02 border border-border-subtle-01 flex items-center justify-center">
                      <Icon name={st.icon} size={15} className="text-icon-secondary" />
                    </div>
                    {i < detail.steps!.length - 1 && <div className="w-px flex-1 min-h-[14px] bg-border-strong-01 my-1" />}
                  </div>
                  <div className="pb-3 min-w-0">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-caption-01 font-mono text-text-helper">{i + 1}</span>
                      <span className="text-body-short-01 font-semibold text-text-primary">{st.label}</span>
                      <span className="text-label-micro font-mono text-text-helper border border-border-subtle-01 rounded px-1 py-px">
                        {st.tool}
                      </span>
                    </div>
                    <div className="text-caption-01 text-text-helper mt-0.5">{st.desc}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* learned rules */}
        <div>
          <SectionLabel
            right={
              <Button
                kind="ghost"
                size="sm"
                icon={<Icon name="add" size={14} />}
                onClick={() => {
                  patch((d) => ({
                    ...d,
                    rules: [...d.rules, { id: 'r' + Date.now(), text: '새 규칙 — 대화에서 교정하면 채워집니다', source: '수동 추가', on: true }],
                  }));
                  flashSaved();
                }}
              >
                규칙 추가
              </Button>
            }
          >
            <span className="inline-flex items-center gap-2">
              학습한 규칙 · {appliedRules}/{detail.rules.length} 적용
              {/* 자동 저장 안내 — 토글·추가·삭제는 즉시 저장되므로 별도 저장 버튼이 없다 */}
              <span
                className={`inline-flex items-center gap-1 normal-case font-normal text-support-success transition-opacity duration-300 ${
                  ruleSaved ? 'opacity-100' : 'opacity-0'
                }`}
              >
                <Icon name="check" size={12} /> 자동 저장됨
              </span>
            </span>
          </SectionLabel>
          <div className="flex flex-col gap-px bg-border-subtle-01 rounded-lg overflow-hidden">
            {detail.rules.map((r) => (
              <RuleRow
                key={r.id}
                r={r}
                onToggle={() => {
                  patch((d) => ({ ...d, rules: d.rules.map((x) => (x.id === r.id ? { ...x, on: !x.on } : x)) }));
                  flashSaved();
                }}
                onRemove={() => {
                  patch((d) => ({ ...d, rules: d.rules.filter((x) => x.id !== r.id) }));
                  flashSaved();
                }}
              />
            ))}
            {detail.rules.length === 0 && (
              <div className="bg-background px-3.5 py-3.5 text-caption-01 text-text-helper">
                아직 학습한 규칙이 없어요. 대화에서 교정하면 여기에 쌓입니다.
              </div>
            )}
          </div>
        </div>

        {/* version history */}
        <div>
          <SectionLabel right={<span className="text-caption-01 text-text-helper normal-case">항목을 눌러 버전 내용 보기 · 복원</span>}>
            버전 히스토리 · {detail.versions.length}
          </SectionLabel>
          <div>
            {detail.versions.map((v, i) => (
              <div key={v.ver} className="flex gap-3">
                <div className="flex flex-col items-center flex-shrink-0">
                  <div
                    className={`w-2.5 h-2.5 rounded-full mt-3.5 ${v.current ? 'bg-blue-50 ring-[3px] ring-blue-100' : 'bg-layer-03'}`}
                  />
                  {i < detail.versions.length - 1 && <div className="w-px flex-1 min-h-[22px] bg-border-strong-01" />}
                </div>
                <button
                  onClick={() => setSnapVer(v.ver)}
                  className="flex items-center gap-2.5 flex-1 min-w-0 text-left bg-layer-01 border border-border-subtle-01 rounded-lg px-3 py-2.5 mb-2.5 hover:bg-layer-hover-01 transition-colors"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-body-short-01 font-semibold text-text-primary font-mono">{v.ver}</span>
                      {v.current && (
                        <Tag type="blue" size="sm">
                          현재
                        </Tag>
                      )}
                      <span className="text-caption-01 text-text-helper">{v.when}</span>
                      {v.author && (
                        <span className="inline-flex items-center gap-1 text-caption-01 text-text-helper">
                          <Icon name="user" size={10} /> {v.author}
                        </span>
                      )}
                    </div>
                    <div className="text-body-short-01 text-text-secondary mt-0.5">{v.note}</div>
                    {v.metrics && (
                      <div className="flex items-center gap-3 mt-1.5 text-caption-01 font-mono text-text-helper">
                        <span>성공률 {pctStr(v.metrics.successRate)}</span>
                        <span>· {v.metrics.uses}회</span>
                        {v.metrics.avgTurns != null && <span>· {v.metrics.avgTurns}턴</span>}
                      </div>
                    )}
                  </div>
                  <Icon name="arrow-right" size={16} className="text-icon-secondary opacity-50 flex-shrink-0" />
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* recent activity — "살아있는" 데이터 질감 */}
        {detail.activity && detail.activity.length > 0 && (
          <div>
            <SectionLabel>최근 활동</SectionLabel>
            <div className="flex flex-col gap-px bg-border-subtle-01 rounded-lg overflow-hidden">
              {detail.activity.map((a, i) => {
                const meta = ACTIVITY_META[a.kind];
                return (
                  <div key={i} className="flex items-center gap-2.5 bg-background px-3.5 py-2.5">
                    <Icon name={meta.icon} size={15} className={`flex-shrink-0 ${meta.cls}`} />
                    <span className="text-body-short-01 text-text-primary flex-1 min-w-0 truncate">{a.text}</span>
                    <span className="text-caption-01 text-text-helper flex-shrink-0">{a.when}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* where used */}
        <div>
          <SectionLabel>사용처 · {detail.usage.length}</SectionLabel>
          <div className="flex flex-col gap-px bg-border-subtle-01 rounded-lg overflow-hidden">
            {detail.usage.map((u, i) => (
              <div key={i} className="flex items-center gap-2.5 bg-background px-3.5 py-2.5">
                <Icon name={u.icon} size={16} className="text-icon-secondary flex-shrink-0" />
                <span className="text-body-short-01 text-text-primary flex-1 min-w-0 truncate">{u.name}</span>
                <Tag type="gray" size="sm">
                  {u.kind}
                </Tag>
              </div>
            ))}
          </div>
        </div>

        {/* data permissions */}
        <div>
          <SectionLabel
            right={
              <button
                onClick={() => navigate('/connectors')}
                className="inline-flex items-center gap-1 text-caption-01 text-link-primary hover:underline"
              >
                접근 권한 관리 <Icon name="arrow-up-right" size={12} />
              </button>
            }
          >
            사용하는 데이터 접근
          </SectionLabel>
          {detail.permissions.length === 0 ? (
            <div className="text-caption-01 text-text-helper">이 스킬은 외부 데이터에 접근하지 않아요.</div>
          ) : (
            <div className="flex gap-2 flex-wrap">
              {detail.permissions.map((p, i) => (
                <span
                  key={i}
                  className="inline-flex items-center gap-1.5 bg-layer-01 border border-border-subtle-01 rounded-md px-2.5 py-1.5 text-caption-01 font-mono text-text-secondary"
                >
                  <Icon name="lock" size={12} className="opacity-70" />
                  {p.name} · {p.mode}
                </span>
              ))}
            </div>
          )}
        </div>

      </div>

      {/* actions — 주 액션(편집)만 또렷이 노출하고, 보조·위험 액션은 ••• 메뉴로 접는다. */}
      <div className="flex-shrink-0 flex gap-2 border-t border-border-subtle-01 bg-background px-7 py-2 items-center">
        {/* 편집 = 이 화면의 주 액션. 채팅 매개 patch로 안전하게 버전을 올린다. */}
        <button
          onClick={() => { setEditTarget(undefined); setEditOpen(true); }}
          className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-lg text-body-short-01 font-semibold bg-interactive text-text-on-color hover:bg-interactive-hover transition-colors"
        >
          <Icon name="edit" size={14} />
          이 스킬 편집
        </button>
        {/* 공유됨이면 마켓 게시 상태를 인라인으로 알려준다 (메뉴 안에 숨지 않게) */}
        {skill.shared && (
          <span className="inline-flex items-center gap-1 text-caption-01 text-purple-60" title="Skill Market에 공개됨">
            <Icon name="share" size={13} /> 마켓에 공개됨
          </span>
        )}
        <div className="flex-1" />
        {/* 공유·내보내기·삭제 — 의도적 단계로 접어둔다. 삭제는 danger 항목(명확한 hover). */}
        <OverflowMenu
          align="right"
          direction="up"
          items={[
            skill.shared
              ? { label: '마켓에서 내리기', icon: 'share', onClick: () => setConfirmUnshare(true) }
              : { label: 'Skill Market에 공유', icon: 'share', onClick: () => setPublishOpen(true) },
            { label: '내보내기', icon: 'export', onClick: () => downloadSkillBundle(skill, detail) },
            { label: '삭제', icon: 'trash', tone: 'danger', onClick: () => setConfirmDelete(true) },
          ]}
        />
      </div>

      <ConfirmDialog
        open={confirmDelete}
        tone="danger"
        title={`'${skill.name}' 스킬을 삭제할까요?`}
        confirmLabel="삭제"
        confirmIcon="trash"
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => {
          setSkills((a) => a.filter((x) => x.id !== skillId));
          setConfirmDelete(false);
        }}
        description="삭제하면 학습한 규칙과 버전 히스토리가 모두 사라지며 되돌릴 수 없어요."
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
          publishOwnedSkill(ownedToMarketSkill(skill, detail));
          setPublishOpen(false);
        }}
      />

      <ConfirmDialog
        open={confirmUnshare}
        tone="danger"
        title={`'${skill.name}'을(를) 마켓에서 내릴까요?`}
        confirmLabel="마켓에서 내리기"
        confirmIcon="close"
        onClose={() => setConfirmUnshare(false)}
        onConfirm={() => {
          setSkills((a) => a.map((x) => (x.id === skillId ? { ...x, shared: false } : x)));
          unpublishSkill(publishedMarketId(skill.id));
          setConfirmUnshare(false);
        }}
        description="다른 사용자는 더 이상 이 스킬을 새로 설치할 수 없어요. 이미 설치한 사용자에겐 영향이 없어요."
      />

      {/* 소스 워크스페이스(풀스크린 IDE) — 편집은 보고 있는 파일을 대상으로 chat-patch 드로어를 띄운다 */}
      <SkillSourceWorkspace
        open={workspaceOpen}
        skill={skill}
        detail={detail}
        onClose={() => setWorkspaceOpen(false)}
        onEdit={(path) => { setEditTarget(path); setEditOpen(true); }}
      />

      <SkillEditDrawer
        open={editOpen}
        skill={skill}
        detail={detail}
        targetPath={editTarget}
        onClose={() => setEditOpen(false)}
        onApply={applyEditPatch}
      />

      {confirmModal}
    </div>
  );
};

function MySkillsScreen() {
  const navigate = useNavigate();
  const { skills, setSkills, hydrating } = useLearning();
  const [filter, setFilter] = useState<Filter>('전체');
  const [sel, setSel] = useState<string | null>('s1');
  // "학습 효과" (Learning Impact) panel swaps into the detail pane (LE-1: keep 7 screens)
  const [showImpact, setShowImpact] = useState(false);
  // 수동 "스킬 정리"(curator) 트리거 상태
  const [curating, setCurating] = useState(false);
  const [curateNote, setCurateNote] = useState<string | null>(null);
  // Phase B: curator LLM 통합 제안 — /skills/curate?mode=consolidate 결과를 들고
  // 있다가 사용자가 각 제안을 개별 승인/거부한다(approval_id 없음, §서버 설계).
  const [consolidating, setConsolidating] = useState(false);
  const [consolidateProposals, setConsolidateProposals] = useState<ConsolidateProposal[]>([]);
  const [consolidateNote, setConsolidateNote] = useState<string | null>(null);

  // Resizable left rail
  const railPanel = useResizable({
    initial: 282,
    min: 184,
    max: 360,
    storageKey: 'rice.panel.myskills',
  });

  // Resizable skill list
  const listPanel = useResizable({
    initial: 312,
    min: 240,
    max: 480,
    storageKey: 'rice.panel.myskills-list',
  });

  const match = (s: (typeof skills)[number]) =>
    filter === '전체' ||
    (filter === '직접 제작' && skillOrigin(s) === 'authored') ||
    (filter === '설치됨' && skillOrigin(s) === 'installed') ||
    (filter === '학습됨' && skillOrigin(s) === 'learned') ||
    (filter === '자주 사용' && s.uses >= 10) ||
    (filter === '최근 학습' && ['+1 방금', '새로 생성'].includes(s.trend)) ||
    (filter === '초안' && isDraft(s)) ||
    (filter === '공유됨' && !!s.shared) ||
    (filter === '비활성' && skillStatus(s) === 'inactive') ||
    (filter === 'stale' && skillStatus(s) === 'stale') ||
    (filter === '보관됨' && skillStatus(s) === 'archived');

  const list = skills.filter(match);
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
    // api는 생명주기 state를 받는다 — inactive는 내부적으로 state=active이므로 active로 보고.
    void api.setSkillState(id, next === 'inactive' ? 'active' : next);
  };

  /** 수동 "스킬 정리" (LEARNING_LOOP_DESIGN 5.1 데모 경로 · POST /skills/curate).
   *  큐레이터의 시간 규칙을 지금 즉시 재적용한다: 각 스킬의 idleDays(미사용 일수)로
   *  있어야 할 상태(3일↑ stale, 7일↑ archived)를 계산해, 현재 상태와 어긋난 스킬을 전이.
   *  복귀(→active)는 실제 사용 시 idleDays 가 리셋되며 일어나므로, 정리는 "내려가는"
   *  전이(active→stale→archived)만 적용한다. 각 전이는 "N일 미사용" 사유와 함께 보고. */
  const curateSkills = () => {
    if (curating) return;
    setCurating(true);
    void api.curateSkills();

    const moves: string[] = [];
    skills.forEach((s) => {
      const next = ruleState(s.idleDays ?? 0);
      if (next !== skillState(s) && next !== 'active') {
        moves.push(`${s.name} → ${next === 'stale' ? 'Stale' : '보관'} (${s.idleDays ?? 0}일 미사용)`);
      }
    });

    setSkills((arr) =>
      arr.map((s) => {
        const next = ruleState(s.idleDays ?? 0);
        if (next === skillState(s) || next === 'active') return s;
        return { ...s, state: next, enabled: next === 'archived' ? false : s.enabled };
      }),
    );

    setCurateNote(moves.length ? moves.join(' · ') : '전이 대상 없음 — 모두 신선합니다');
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
    setConsolidateNote(null);
    const res = await api.consolidateSkills();
    setConsolidating(false);
    if (!res.success || !res.data) {
      setConsolidateNote(`통합 분석 실패: ${res.error ?? '알 수 없는 오류'}`);
      return;
    }
    const { proposals } = res.data;
    setConsolidateProposals(proposals);
    setConsolidateNote(
      proposals.length
        ? `${proposals.length}건의 통합 제안을 찾았습니다 — 아래에서 검토하세요.`
        : '통합할 만한 유사/중복 스킬을 찾지 못했습니다.',
    );
  };

  /** 통합 제안 1건 승인/거부. 승인 시 실제로 적용된 결과(서버 응답)로 로컬 skills
   *  목록을 갱신한다 — merge 제안은 새 버전/신규 스킬로, archive 제안은 보관 상태로. */
  const decideProposal = async (p: ConsolidateProposal, approved: boolean) => {
    if (p.kind === 'patch') {
      const res = await api.answerConsolidateDecision(p.skill_id, approved, {
        proposedBody: p.proposed_body,
        description: p.description,
        tags: p.tags,
      });
      if (approved && res.success) {
        const newVersion = res.data?.version ?? p.to_version;
        setSkills((arr) => {
          if (arr.some((s) => s.id === p.skill_id)) {
            return arr.map((s) =>
              s.id === p.skill_id ? { ...s, ver: `v${newVersion}`, patches: (s.patches ?? 0) + 1, trend: '통합됨' } : s,
            );
          }
          return [
            ...arr,
            {
              id: p.skill_id, name: p.skill_id, ver: `v${newVersion}`, uses: 0,
              trend: '통합 생성', enabled: true, patches: 0, command: p.skill_id, state: 'active' as const,
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
  const selectedExists = sel && skills.some((s) => s.id === sel);
  const effectiveSel = selectedExists ? sel : list[0]?.id ?? skills[0]?.id ?? null;

  return (
    <div className="flex flex-1 min-w-0 bg-background overflow-hidden">
      {/* rail */}
      <div
        className="flex-shrink-0 bg-layer-01 border-r border-border-subtle-01 flex flex-col min-w-0 overflow-hidden"
        style={{ width: railPanel.width }}
      >
        {/* 통계 카드 + "학습 효과" proof CTA — 숫자(현황)에서 정량 증명으로 자연히 이어지는 한 덩어리 */}
        <div className="p-4">
          <div className="overflow-hidden rounded-xl bg-layer-01 border border-border-subtle-01">
            <div className="p-4">
              <div className="flex items-center gap-1.5 mb-1">
                <span className="text-caption-01 text-text-helper">학습한 스킬</span>
                <span className="ai-label">
                  <span className="ai-label__text">AI</span>
                </span>
              </div>
              <div className="text-heading-03 font-semibold text-text-primary leading-none tabular-nums">
                {skills.length}
                <span className="text-body-short-01 font-normal text-text-helper ml-1">개</span>
              </div>
              <div className="text-caption-01 text-text-secondary mt-1.5 flex gap-3">
                <span>{active} 활성</span>
                <span>{shared} 공유됨</span>
              </div>
            </div>
            {/* proof CTA — 카드 하단에 붙여 "현황 → 학습 효과 보기"로 읽히게 한다 (EVAL 6.1) */}
            <button
              onClick={() => setShowImpact(true)}
              aria-pressed={showImpact}
              className={`w-full flex items-center gap-2 px-4 py-2.5 border-t transition-colors ${
                showImpact
                  ? 'bg-layer-selected-01 border-border-subtle-01'
                  : 'bg-layer-02/50 border-border-subtle-01 hover:bg-layer-hover-01'
              }`}
            >
              <Icon name="growth" size={15} className="text-icon-primary flex-shrink-0" />
              <div className="flex-1 min-w-0 text-left">
                <div className="text-body-short-01 font-semibold text-text-primary">학습 효과</div>
                <div className="text-caption-01 text-text-helper">cold→warm 정량 비교</div>
              </div>
              <Icon name="arrow-right" size={14} className="text-icon-secondary flex-shrink-0" />
            </button>
          </div>
        </div>

        {/* 유지보수 액션 "스킬 정리" — 가끔 쓰는 큐레이터 트리거라 카드가 아닌 경량 행으로 위계를 낮춘다. */}
        <button
          onClick={curateSkills}
          disabled={curating}
          title="오래 안 쓴 스킬을 stale·보관으로 정리합니다 (POST /skills/curate)"
          className="group flex items-center gap-2 mx-4 mb-1 px-2 py-1.5 rounded-md text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary transition-colors disabled:opacity-50"
        >
          <Icon name="renew" size={14} className={`flex-shrink-0 ${curating ? 'animate-spin text-icon-primary' : 'text-icon-secondary group-hover:text-icon-primary'}`} />
          <span className="text-body-short-01 font-medium flex-shrink-0">{curating ? '정리 중…' : '스킬 정리'}</span>
          <span className="text-caption-01 text-text-helper truncate ml-1" title={curateNote ?? undefined}>
            {curateNote ?? `${STALE_DAYS}일↑ Stale · ${ARCHIVE_DAYS}일↑ 보관`}
          </span>
        </button>

        {/* Phase B: curator LLM 통합 — 유사/중복 스킬을 하나의 class-level umbrella로
            묶는 제안을 분석한다. 규칙 정리와 달리 LLM 판단이 필요해 훨씬 오래 걸릴 수
            있어(스킬 전체 본문을 프롬프트에 태움) 별도 버튼으로 분리했다. */}
        <button
          onClick={() => void runConsolidate()}
          disabled={consolidating}
          title="유사/중복 스킬을 하나의 통합 스킬로 합치는 제안을 분석합니다 (POST /skills/curate?mode=consolidate)"
          className="group flex items-center gap-2 mx-4 mb-1 px-2 py-1.5 rounded-md text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary transition-colors disabled:opacity-50"
        >
          <Icon name="flow" size={14} className={`flex-shrink-0 ${consolidating ? 'animate-spin text-icon-primary' : 'text-icon-secondary group-hover:text-icon-primary'}`} />
          <span className="text-body-short-01 font-medium flex-shrink-0">{consolidating ? '분석 중…' : 'AI 통합 제안'}</span>
          {consolidateNote && (
            <span className="text-caption-01 text-text-helper truncate ml-1" title={consolidateNote}>
              {consolidateNote}
            </span>
          )}
        </button>

        {/* 통합 제안 검토 목록 — 제안마다 개별 승인/거부(approval_id 없음, 서버가
            일반 REST 응답으로 그대로 반환한 것을 로컬에서 들고 있다가 반송). */}
        {consolidateProposals.length > 0 && (
          <div className="mx-4 mb-2 space-y-1.5">
            {consolidateProposals.map((p, i) => (
              <div
                key={`${p.kind}-${p.skill_id}-${i}`}
                className="rounded-lg border border-border-subtle-01 bg-layer-01 p-2.5"
              >
                <div className="flex items-center gap-1.5 mb-1">
                  <Icon name={p.kind === 'patch' ? 'flow' : 'trash'} size={12} className="text-icon-secondary flex-shrink-0" />
                  <span className="text-caption-01 font-semibold text-text-primary truncate">{p.skill_id}</span>
                  <Tag type={p.kind === 'patch' ? 'blue' : 'gray'} size="sm">
                    {p.kind === 'patch' ? (p.from_version === 0 ? '신규 통합' : '병합') : '정리'}
                  </Tag>
                </div>
                <p className="text-caption-01 text-text-secondary mb-2 line-clamp-2">
                  {p.kind === 'patch' ? p.changelog : p.reason}
                </p>
                <div className="flex items-center gap-1.5">
                  <Button kind="ghost" size="sm" onClick={() => void decideProposal(p, false)}>
                    거부
                  </Button>
                  <Button kind="primary" size="sm" onClick={() => void decideProposal(p, true)}>
                    승인
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* 유지보수 영역과 필터 목록을 시각적으로 분리 */}
        <div className="mx-4 mt-1.5 mb-1 border-t border-border-subtle-01" />

        <div className="flex-1 overflow-y-auto pb-2">
          {FILTER_GROUPS.map((group) => (
            <div key={group.label ?? '_all'}>
              {group.label && (
                <div className="text-caption-01 text-text-helper px-4 pt-3 pb-1 tracking-wider uppercase">{group.label}</div>
              )}
              {group.items.map((c) => {
                const isActive = c === filter && !showImpact;
                return (
                  <button
                    key={c}
                    onClick={() => {
                      setFilter(c);
                      setShowImpact(false);
                    }}
                    className={`block w-full text-left px-4 py-2.5 text-body-short-01 border-l-2 transition-colors ${
                      isActive
                        ? 'bg-layer-selected-01 border-l-blue-50 text-text-primary font-semibold'
                        : 'bg-transparent border-l-transparent text-text-secondary hover:bg-layer-hover-01'
                    }`}
                  >
                    {c}
                  </button>
                );
              })}
            </div>
          ))}
        </div>

        <div className="border-t border-border-subtle-01 px-4 py-3 flex items-start gap-2">
          <Icon name="idea" size={15} className="text-icon-secondary mt-0.5 flex-shrink-0" />
          <span className="text-caption-01 text-text-helper leading-4">
            새 스킬은 대화에서 자동으로 생성돼요. 여기서 다듬고 공유하세요.
          </span>
        </div>
      </div>

      {/* Rail resize handle */}
      <ResizeHandle
        onPointerDown={railPanel.startResize}
        onDoubleClick={railPanel.resetWidth}
        isResizing={railPanel.isResizing}
        aria-label="Resize filters panel"
      />

      {/* skill list */}
      <div
        className="flex-shrink-0 bg-background border-r border-border-subtle-01 overflow-y-auto min-w-0"
        style={{ width: listPanel.width }}
      >
        <div className="px-5 pt-5 pb-3">
          <h1 className="text-heading-03 font-light text-text-primary">내 스킬</h1>
        </div>
        {list.map((s) => {
          const on = s.id === effectiveSel;
          return (
            <button
              key={s.id}
              onClick={() => {
                setSel(s.id);
                setShowImpact(false);
              }}
              className={`flex items-start gap-2.5 w-full text-left border-b border-border-subtle-01 border-l-2 px-[18px] py-3 transition-colors ${
                on && !showImpact ? 'bg-layer-selected-01 border-l-blue-50' : 'bg-transparent border-l-transparent hover:bg-layer-hover-01'
              } ${skillStatus(s) === 'archived' ? 'opacity-60' : !isInjected(skillStatus(s)) ? 'opacity-70' : ''}`}
            >
              <Icon name="skill" size={16} className="text-icon-secondary mt-0.5 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="text-body-short-01 font-semibold text-text-primary truncate">{s.name}</span>
                  <span className="text-caption-01 font-mono text-text-helper flex-shrink-0">{s.ver}</span>
                  <OriginGlyph s={s} />
                  <StatusBadge s={s} />
                </div>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <span className="text-caption-01 text-text-helper truncate">
                    {s.uses}회 사용 · {s.trend}
                  </span>
                </div>
              </div>
              {s.shared && <Icon name="share" size={13} className="text-icon-secondary mt-0.5 opacity-70 flex-shrink-0" />}
            </button>
          );
        })}
        {list.length === 0 && (
          <div className="p-10 text-center text-body-short-01 text-text-helper">
            {hydrating ? (
              '불러오는 중…'
            ) : skills.length === 0 ? (
              <div className="flex flex-col items-center gap-3">
                <span>아직 학습된 스킬이 없어요. 대화를 나누면 자동으로 스킬이 자라납니다.</span>
                <button
                  onClick={() => navigate('/skills')}
                  className="inline-flex items-center gap-1.5 text-link-primary hover:underline text-body-short-01"
                >
                  <Icon name="skill" size={14} />
                  Skill Market 둘러보기
                  <Icon name="arrow-up-right" size={13} />
                </button>
              </div>
            ) : (
              '해당하는 스킬이 없어요.'
            )}
          </div>
        )}
      </div>

      {/* List resize handle */}
      <ResizeHandle
        onPointerDown={listPanel.startResize}
        onDoubleClick={listPanel.resetWidth}
        isResizing={listPanel.isResizing}
        aria-label="Resize skills list panel"
      />

      {/* detail — skill detail, or the Learning Impact panel (LE-1) */}
      {showImpact ? <LearningImpactPanel report={IMPACT_REPORT} /> : <SkillDetailView skillId={effectiveSel} onSetStatus={setSkillStatus} />}
    </div>
  );
}

export default MySkillsScreen;

// Made with Bob
