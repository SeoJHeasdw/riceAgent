import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Icon, Tag, ResizeHandle, Toggle, ConfirmDialog, OverflowMenu } from '../components/ui';
import { useResizable } from '../hooks/useResizable';
import { useLearning } from '../lib/learning/LearningContext';
import { useMarketplace } from '../hooks/useMarketplace';
import { LearningImpactPanel } from '../components/carbon/LearningImpactPanel';
import { SkillSourceWorkspace } from '../components/carbon/SkillSourceWorkspace';
import { SkillPublishDialog } from '../components/carbon/SkillPublishDialog';
import { SkillEditDrawer, type EditPatch } from '../components/carbon/SkillEditDrawer';
import { Markdown } from '../components/carbon/Markdown';
import { IMPACT_REPORT, runtimeAnalysisToSkillAnalysis } from '../lib/learningData';
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
type Filter =
  | '전체'
  | '기본 내장'
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
  { label: '출처', items: ['기본 내장', '직접 제작', '설치됨', '학습됨'] },
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
const ORIGIN_META: Record<SkillOrigin, { label: string; icon: IconName; cls: string }> = {
  learned: { label: '학습됨', icon: 'idea', cls: 'text-icon-primary' },
  installed: { label: '설치됨', icon: 'download', cls: 'text-link-primary' },
  authored: { label: '직접 제작', icon: 'user', cls: 'text-purple-60' },
  builtin: { label: '기본 내장', icon: 'sparkle', cls: 'text-teal-40' },
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

// 빌트인 스킬의 단일 컨트롤 — 활성/비활성 토글 하나뿐이다(요구사항: 그 외 전이·편집 불가).
// 서버 상태는 active|archived 두 값으로 영속되고, UI 표현은 활성/비활성으로 통일한다.
const BuiltinStatusToggle: React.FC<{
  skill: Skill;
  onSet: (next: SkillStatus) => void;
}> = ({ skill, onSet }) => {
  const on = skillState(skill) === 'active';
  return (
    <span
      className="inline-flex items-center gap-2 border border-border-subtle-01 rounded-md pl-2.5 pr-1.5 py-1"
      title={on ? '대화에 주입 중 — 끄면 주입이 중단돼요' : '주입 꺼짐 — 켜면 다시 대화에 주입돼요'}
    >
      <span className={`text-caption-01 font-semibold ${on ? 'text-support-success' : 'text-text-helper'}`}>
        {on ? '활성' : '비활성'}
      </span>
      <Toggle
        checked={on}
        onChange={() => onSet(on ? 'archived' : 'active')}
        aria-label={`${skill.name} ${on ? '비활성화' : '활성화'}`}
      />
    </span>
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

const CONFIDENCE_META: Record<SkillAnalysisConfidence, { label: string; cls: string }> = {
  high: { label: '신뢰 높음', cls: 'text-support-success' },
  medium: { label: '신뢰 보통', cls: 'text-text-secondary' },
  low: { label: '추정', cls: 'text-support-warning' },
};

const ConfidencePill: React.FC<{ value?: SkillAnalysisConfidence }> = ({ value = 'low' }) => {
  const meta = CONFIDENCE_META[value];
  return (
    <span className={`inline-flex items-center gap-1 text-caption-01 font-semibold ${meta.cls}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {meta.label}
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
  analysis?: RuntimeSkillAnalysis | null;
};

const stripFrontmatter = (content: string): string =>
  content.replace(/^---\s*[\s\S]*?\n---\s*/m, '').trim();

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

const versionWhen = (value: string): string => {
  if (!value) return '저장 시각 없음';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('ko-KR');
};

const runtimeVersionList = (
  current: RuntimeSkillDetailPayload,
  history: RuntimeSkillVersion[],
): SkillVersion[] => [
  {
    ver: `v${current.version}`,
    when: '현재 적용 중',
    note: '현재 런타임에 적용된 SKILL.md',
    current: true,
    body: runtimeSkillSource(current),
  },
  ...history
    .filter((entry) => entry.version !== current.version)
    .sort((a, b) => b.version - a.version)
    .map((entry): SkillVersion => ({
      ver: `v${entry.version}`,
      when: versionWhen(entry.updated_at),
      note: `서버에 보관된 v${entry.version} 원본`,
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

const stepCandidatesFromBody = (body: string): ResolvedAnalysisStep[] => {
  const section = findMarkdownSection(body, /(workflow|how it works|steps|procedure|절차|작동 방식|사용 방법|흐름)/i);
  const source = section.length ? section : body.split('\n');
  return source
    .filter((line) => /^\s*(?:[-*+]\s+|\d+[.)]\s+)/.test(line))
    .map((line): ResolvedAnalysisStep => ({
      title: sentenceForTitle(line),
      description: section.length ? '원문의 절차 섹션에서 감지됨' : '원문 목록 구조에서 감지된 단계 후보',
      confidence: section.length ? 'medium' : 'low',
      evidence: cleanMdLine(line),
    }))
    .filter((s) => s.title.length > 0)
    .slice(0, 6);
};

// 본문 도구 섹션의 항목 — 출처 분리 원칙상 body 에서 온 건 전부 'mentioned'(언급
// 후보)다. declared 는 frontmatter(tools:)/manifest 선언만이 정본이고 그건 서버
// 분석 payload 로만 온다 (learning/skill/analysis.py 계약).
const toolNamesFromSection = (body: string): ResolvedToolSignal[] =>
  findMarkdownSection(body, /(tools|tooling|사용 도구|도구)/i)
    .filter((line) => /^\s*(?:[-*+]\s+|\d+[.)]\s+)/.test(line))
    .map((line): ResolvedToolSignal => ({
      name: sentenceForTitle(line),
      kind: 'mentioned',
      confidence: 'medium',
      basis: 'SKILL.md 도구 섹션',
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

const mentionedToolsFromBody = (body: string): ResolvedToolSignal[] =>
  KNOWN_TOOL_PATTERNS
    .filter((t) => t.pattern.test(body))
    .map((t): ResolvedToolSignal => ({
      name: t.name,
      kind: 'mentioned',
      confidence: 'low',
      basis: '본문 키워드 감지',
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

const resolveUnderstanding = (skill: Skill, detail: SkillDetail): ResolvedUnderstanding => {
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
      : stepCandidatesFromBody(body);

  // 도구 단서: 서버 payload 는 이미 observed(실행 로그)/declared(frontmatter 선언)/
  // mentioned(본문 감지) 세 출처가 합류된 결과라 그대로 신뢰한다. 사이드카가 없을
  // 때만 클라이언트가 본문에서 mentioned 후보를 임시로 긁는다.
  const serverTools = (analysis?.toolMentions ?? []).map((t): ResolvedToolSignal => ({
    ...t,
    confidence: t.confidence ?? (t.kind === 'mentioned' ? 'low' : 'high'),
  }));
  const tools = hasAnalysis
    ? dedupeTools(serverTools)
    : dedupeTools([...toolNamesFromSection(body), ...mentionedToolsFromBody(body)]);

  const sourceLabel = !hasAnalysis
    ? '원문 미리보기'
    : analysis?.source === 'llm'
      ? 'AI 분석'
      : '자동 분석 (규칙 기반)';

  return {
    summary: analysis?.summary?.trim() || detail.summary,
    sourceLabel,
    statusLabel: !hasAnalysis ? '분석 대기' : analysis!.status === 'fresh' ? '최신' : analysis!.status === 'stale' ? '재분석 권장' : '분석 실패',
    confidence: analysis?.confidence ?? (hasAnalysis ? 'medium' : 'low'),
    analyzedAt: analysis?.analyzedAt,
    stale: !analysis || analysis.status !== 'fresh',
    steps,
    tools,
    warnings: analysis?.warnings ?? [],
  };
};

const ToolKindMeta: Record<ResolvedToolSignal['kind'], { label: string; icon: IconName; cls: string }> = {
  observed: { label: '실제 사용', icon: 'check-circle', cls: 'text-support-success' },
  declared: { label: '선언됨', icon: 'rule', cls: 'text-link-primary' },
  mentioned: { label: '언급 후보', icon: 'search', cls: 'text-support-warning' },
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
                      title={`${v.ver}(으)로 복원`}
                      className="group/sw flex-shrink-0 inline-flex h-6 items-center gap-1 rounded border border-border-subtle-02 px-1.5 text-label-micro font-semibold text-text-secondary transition-colors hover:border-border-strong-01 hover:bg-layer-hover-01 hover:text-text-primary"
                    >
                      <Icon name="renew" size={10} className="transition-transform group-hover/sw:rotate-180" /> 복원
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
  const hasRuleComparison = rules.length > 0 || (currentVersion?.rules?.length ?? 0) > 0;

  // ── 복원 판단 근거 (현재 버전이 아닐 때만) ──
  const decision = (() => {
    if (version.current || !currentVersion) return null;
    if (!hasRuleComparison && !(m && currentVersion.metrics)) return null;
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
          {!version.current && (
            <Button
              kind="secondary"
              size="xs"
              className="ml-auto"
              icon={<Icon name="renew" size={12} />}
              onClick={onRestore}
              title="이 원문을 새 버전으로 복원합니다. 기존 기록은 유지됩니다."
            >
              {version.ver} 복원
            </Button>
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

        {version.body ? (
          <div className="mt-6">
            <div className="mb-2.5 flex items-center gap-2">
              <span className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">
                이 버전의 SKILL.md
              </span>
              <span className="text-caption-01 text-text-helper">서버에 보관된 읽기 전용 원문</span>
            </div>
            <div className="max-w-[920px] overflow-x-auto rounded-lg border border-border-subtle-01 bg-background px-4 py-3 [&_h1]:!mb-2 [&_h1]:!text-heading-03 [&_h2]:!mb-1.5 [&_h2]:!mt-4 [&_h2]:!text-body-short-02 [&_h3]:!mt-3 [&_h3]:!text-body-short-01 [&_li]:!text-caption-01 [&_p]:!text-caption-01">
              <Markdown source={version.body} />
            </div>
          </div>
        ) : (
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
        )}

        {version.current && (
          <div className="mt-5 pt-3 border-t border-border-subtle-01 flex items-center gap-1.5 text-caption-01 text-text-helper">
            <Icon name="check-circle" size={14} className="text-support-success" /> 지금 적용 중인 버전이에요.
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

  if (origin === 'builtin') {
    return (
      <div className="mt-3 rounded-lg border border-teal-70/40 bg-teal-70/10 px-3 py-2.5">
        <div className="flex items-start gap-2 text-caption-01">
          <Icon name="sparkle" size={14} className="text-teal-40 flex-shrink-0 mt-px" />
          <span className="text-text-secondary leading-4">
            <span className="text-text-primary font-semibold">RICE 기본 내장 스킬</span> — 앱과 함께
            제공되고 업데이트돼요. 활성/비활성만 전환할 수 있고{' '}
            <span className="text-text-primary">삭제·수정·마켓 공유는 지원하지 않아요.</span>{' '}
            소스 보기로 SKILL.md 작성법을 참고해 나만의 스킬을 만들어 보세요.
          </span>
        </div>
      </div>
    );
  }

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

// 소스 보기 런처 — 아이콘만 남기면 의미 발견성이 떨어진다.
// 스킬의 차별점(소스 열람·편집 가능)을 텍스트로 드러내되, 플러그인 헤더보다 무겁지 않게 유지한다.
const SourceLauncher: React.FC<{ skill: Skill; detail: SkillDetail; onOpen: () => void }> = ({ skill, detail, onOpen }) => {
  const { files } = ownedSkillFiles(skill, detail);
  const folders = new Set(files.filter((f) => f.path.includes('/')).map((f) => f.path.slice(0, f.path.indexOf('/'))));
  const readOnly = isBuiltinSkill(skill);
  return (
    <button
      onClick={onOpen}
      title={
        readOnly
          ? '기본 내장 SKILL.md를 전체 화면으로 보기 (읽기 전용) — 스킬 작성법 참고용'
          : '이 스킬의 파일(SKILL.md·scripts…)을 전체 화면으로 보기'
      }
      className="group relative flex items-center gap-3 rounded-xl border border-border-subtle-01 bg-layer-01 pl-3 pr-3.5 py-2.5 hover:border-blue-50/45 hover:bg-layer-hover-01 transition-colors flex-shrink-0"
    >
      <div className="w-9 h-9 rounded-lg bg-blue-60/14 border border-blue-50/30 flex items-center justify-center flex-shrink-0 text-blue-30">
        <Icon name="terminal" size={18} />
      </div>
      <div className="text-left">
        <div className="flex items-center gap-1.5 text-body-short-01 font-semibold text-text-primary">
          소스 보기
          <Icon name="side-panel-open" size={14} className="text-blue-30 opacity-80 group-hover:opacity-100 transition-opacity" />
        </div>
        <div className="text-caption-01 text-text-helper">
          {files.length} 파일{folders.size > 0 ? ` · ${folders.size} 폴더` : ''} · {readOnly ? '읽기 전용' : '편집 가능'}
        </div>
      </div>
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
        {tools.map((tool) => {
          const meta = ToolKindMeta[tool.kind];
          return (
            <span
              key={`${tool.kind}-${tool.name}`}
              title={tool.evidence || tool.basis || meta.label}
              className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-border-subtle-01 bg-layer-01 px-2 py-1 text-caption-01 text-text-secondary"
            >
              <Icon name={meta.icon} size={12} className={`${meta.cls} flex-shrink-0`} />
              <span className="truncate font-mono">{tool.name}</span>
              {tool.confidence === 'low' && <span className="text-support-warning">추정</span>}
            </span>
          );
        })}
      </div>
    ) : (
      <div className="text-caption-01 text-text-helper leading-4">{empty}</div>
    )}
  </div>
);

const SkillRoutingMetrics: React.FC<{ skill: Skill }> = ({ skill }) => {
  const injected = skill.injected ?? 0;
  const friction = skill.friction ?? 0;
  const routingMiss = skill.routingMiss ?? 0;
  // use에는 명시 호출·수동 skill_view도 포함되므로 100%를 넘을 수 있다. 전환율이
  // 아니라 자동 노출과 전체 관여의 관계를 보는 방향성 지표로 그대로 표시한다.
  const engagementRate = injected > 0 ? Math.round((skill.uses / injected) * 100) : null;
  const metrics = [
    {
      label: '노출 대비 관여',
      value: engagementRate == null ? '—' : `${engagementRate}%`,
      helper: injected > 0 ? `${skill.uses}회 관여 / ${injected}회 노출` : '자동 노출 데이터 없음',
      title: '관여에는 명시 호출과 수동 로딩도 포함되어 100%를 넘을 수 있는 방향성 지표입니다.',
    },
    { label: '자동 노출', value: `${injected}회`, helper: '전문 자동 주입', title: '관련도 라우터가 스킬 전문을 자동 주입한 횟수입니다.' },
    { label: '마찰 신호', value: `${friction}회`, helper: '교정·실패 동시 감지', title: '스킬이 관여한 턴에서 교정 또는 실패 신호가 함께 감지된 횟수입니다.' },
    { label: '라우팅 미스', value: `${routingMiss}회`, helper: '수동 로딩으로 발견', title: '자동 라우터가 찾지 못해 모델이 skill_view로 직접 로딩한 횟수입니다.' },
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
  const understanding = useMemo(() => resolveUnderstanding(skill, detail), [skill, detail]);
  const observed = understanding.tools.filter((t) => t.kind === 'observed');
  const declared = understanding.tools.filter((t) => t.kind === 'declared');
  const mentioned = understanding.tools.filter((t) => t.kind === 'mentioned');
  const feedbackMeta = feedback ? ANALYSIS_FEEDBACK_META[feedback.tone] : null;

  return (
    <div>
      <SectionLabel
        right={
          <button
            onClick={onRefresh}
            disabled={refreshing}
            className="inline-flex items-center gap-1.5 rounded-md border border-border-subtle-01 px-2 py-1 text-caption-01 font-semibold text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary disabled:opacity-50"
            title="SKILL.md를 다시 분석해 설명용 메타데이터를 갱신합니다"
          >
            <Icon name="renew" size={12} className={refreshing ? 'animate-spin text-icon-primary' : 'text-icon-secondary'} />
            {refreshing ? '분석 중…' : 'AI 분석 새로고침'}
          </button>
        }
      >
        스킬 이해
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
              <span className="text-caption-01 text-text-helper">{new Date(understanding.analyzedAt).toLocaleString('ko-KR')}</span>
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
              <span className="text-body-short-01 font-semibold text-text-primary">작동 방식</span>
              <span className="text-caption-01 text-text-helper">원문 해석</span>
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
                아직 구조화된 절차를 보여줄 근거가 없어요. 원문은 소스 보기에서 확인할 수 있습니다.
              </div>
            )}
          </div>

          <div className="bg-background p-4">
            <div className="mb-3 flex items-center gap-2">
              <Icon name="tools" size={15} className="text-icon-secondary" />
              <span className="text-body-short-01 font-semibold text-text-primary">도구 단서</span>
              <button
                onClick={onOpenConnectors}
                className="ml-auto inline-flex items-center gap-1 text-caption-01 text-link-primary hover:underline"
              >
                권한 관리 <Icon name="arrow-up-right" size={11} />
              </button>
            </div>
            <div className="grid gap-4">
              <ToolSignalGroup title="실제로 사용된 도구" tools={observed} empty="아직 실행 로그 기반 도구 기록이 없어요." />
              <ToolSignalGroup title="선언된 도구" tools={declared} empty="manifest나 명시 섹션에서 선언된 도구가 없어요." />
              <ToolSignalGroup title="본문 언급 후보" tools={mentioned} empty="본문에서 도구 후보를 찾지 못했어요." />
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t border-border-subtle-01 px-4 py-2.5 text-caption-01 text-text-helper">
          <Icon name="lock" size={12} className="text-icon-secondary" />
          분석 결과는 설명용입니다. 실제 실행과 권한은 런타임 도구 정책과 승인 흐름이 결정합니다.
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
  const origin = ORIGIN_META[skillOrigin(skill)];
  const metrics = [
    { label: '관여', value: `${skill.uses}회`, helper: skill.trend },
    { label: '본문 개선', value: `${skill.patches ?? 0}회`, helper: '승인된 변경 기준' },
    { label: '최근 사용', value: detail.lastUsed, helper: statusReason(skill) },
    { label: '출처', value: origin.label, helper: detail.createdFrom },
  ];
  return (
    <div>
      <SectionLabel>실사용 근거</SectionLabel>
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

const SkillDetailView: React.FC<{
  skillId: string | null;
  onSetStatus: (id: string, next: SkillStatus) => void;
}> = ({ skillId, onSetStatus }) => {
  const navigate = useNavigate();
  const { skills, setSkills, details, setDetails } = useLearning();
  const { publishOwnedSkill, unpublishSkill } = useMarketplace();
  const toast = useToast();
  const [snapVer, setSnapVer] = useState<string | null>(null);
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
            historyError: detailRes.error || '스킬 상세를 불러오지 못했어요.',
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
      const files = [...(cur.files ?? [])];
      const skillMdIndex = files.findIndex((file) => file.path.toLowerCase().endsWith('skill.md'));
      const skillMd = { path: 'SKILL.md', language: languageFromPath('SKILL.md'), content: source } as const;
      if (skillMdIndex >= 0) files[skillMdIndex] = { ...files[skillMdIndex], content: source };
      else files.unshift(skillMd);

      const currentVersion: SkillVersion = {
        ver: `v${data.version}`,
        when: '현재 적용 중',
        note: '현재 런타임에 적용된 SKILL.md',
        current: true,
        body: source,
      };
      const versions = history
        ? runtimeVersionList(data, history)
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
          historyError: history ? undefined : (historyRes.error || '버전 히스토리를 불러오지 못했어요.'),
        },
      };
    });
    return true;
  }, [setDetails, setSkills]);

  // leaving a skill closes any open version snapshot / 확인 모달 / 분석 상태 / 소스·편집
  useEffect(() => {
    setSnapVer(null);
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
    return <div className="flex-1 flex items-center justify-center text-body-short-02 text-text-helper">스킬을 선택하세요.</div>;
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
      label: '스킬 분석 중',
      sublabel: `${skill.name} · 분석 결과를 생성하고 저장하고 있어요`,
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
        const savedAt = mapped.analyzedAt
          ? new Date(mapped.analyzedAt).toLocaleString('ko-KR')
          : '방금';
        if (mapped.source === 'llm') {
          setAnalysisFeedback({ tone: 'success', message: `AI 분석 저장 완료 · ${savedAt}` });
          progress.done('success', 'AI 분석 저장 완료');
        } else if (mapped.status === 'failed') {
          setAnalysisFeedback({
            tone: 'warning',
            message: `AI 호출 실패 · 규칙 기반 분석 저장 완료 · ${savedAt}`,
          });
          progress.dismiss();
          toast.notify({ message: 'AI 호출에 실패해 규칙 기반 분석 결과를 저장했어요.', variant: 'warning' });
        } else {
          setAnalysisFeedback({
            tone: 'warning',
            message: `규칙 기반 분석 저장 완료 · ${savedAt} · LLM 연결 시 더 풍부해져요`,
          });
          progress.done('success', '원문 기반 분석 저장 완료');
        }
        return;
      }
      const reason = res.error ? ` · ${res.error}` : '';
      setAnalysisFeedback({ tone: 'error', message: `분석을 갱신하지 못했어요${reason}` });
      progress.done('error', '스킬 분석 실패');
    } catch (error) {
      const reason = error instanceof Error ? ` · ${error.message}` : '';
      setAnalysisFeedback({ tone: 'error', message: `분석 결과를 반영하지 못했어요${reason}` });
      progress.done('error', '스킬 분석 실패');
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

  // 런타임 버전 복원은 낙관 갱신하지 않는다. 서버가 현재 원본 보관 → 과거 본문
  // 복원 → 새 단조 증가 버전 발급을 모두 마친 뒤, 상세/히스토리를 다시 읽어 UI를
  // 서버 정본과 맞춘다. 마켓/로컬 fixture만 기존 로컬 스냅샷 경로를 사용한다.
  const restore = async (version: SkillVersion) => {
    if (detail.historyStatus === undefined) {
      restoreLocalVersion(version);
      return;
    }

    const progress = toast.progress({
      label: `${version.ver} 복원 중`,
      sublabel: `${skill.name} · 현재 버전을 보관하고 새 버전으로 복원하고 있어요`,
    });
    try {
      const res = await api.rollbackSkill(skillId, verNum(version.ver));
      if (!res.success || !res.data) {
        const reason = res.error ? ` · ${res.error}` : '';
        progress.done('error', '버전 복원 실패');
        toast.notify({ message: `${version.ver} 복원에 실패했어요${reason}`, variant: 'error' });
        return;
      }

      const refreshed = await loadRuntimeSkillDetail(skillId);
      if (!refreshed) {
        setSkills((prev) => prev.map((item) => (
          item.id === skillId ? { ...item, ver: `v${res.data!.version}` } : item
        )));
        progress.done('success', `${version.ver} 복원 완료`);
        toast.notify({ message: '복원은 완료됐지만 화면 재조회에 실패했어요. 다시 열어 확인하세요.', variant: 'warning' });
      } else {
        progress.done('success', `${version.ver} → v${res.data.version} 복원 완료`);
      }
      setSnapVer(null);
    } catch (error) {
      const reason = error instanceof Error ? ` · ${error.message}` : '';
      progress.done('error', '버전 복원 실패');
      toast.notify({ message: `버전 복원 중 오류가 발생했어요${reason}`, variant: 'error' });
    }
  };

  // 편집 어시스턴트 적용 — patch 승인 → version++ + 대상 파일 갱신 + 활동 기록.
  // 구조화된 절차/도구 표시는 본문에서 다시 분석되는 설명용 sidecar가 담당한다.
  const applyEditPatch = (p: EditPatch, editedBody?: string) => {
    const finalBody = editedBody ?? p.proposedBody;
    // 런타임 영속화(PUT /skills/{id}) — SKILL.md 가 대상일 때. 서버가 히스토리
    // 보관·version+1·재분석 enqueue 를 수행한다(frontmatter 는 서버 소유라 본문만
    // 보낸다). 번들 내 다른 파일(scripts…)은 런타임 스킬 저장소가 단일 SKILL.md
    // 모델이라 로컬 상태로만 유지된다. 오프라인이면 실패 무해(로컬 프로토타입 유지).
    if (p.targetPath.toLowerCase().endsWith('skill.md')) {
      void api.updateSkill(skillId, { body: stripFrontmatter(finalBody) });
    }
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
            when: '방금',
            note: p.changelog,
            current: true,
            author: '편집 어시스턴트',
            rules: d.rules.map((r) => ({ text: r.text })),
          },
          ...d.versions.map((v) => ({ ...v, current: false })),
        ],
        activity: [
          { when: '방금', text: `편집 어시스턴트로 ${p.toVer} 발행 — ${p.targetLabel}: ${p.changelog}`, kind: 'edit' as const },
          ...(d.activity ?? []),
        ],
      };
    });
    setAnalysisFeedback({ tone: 'warning', message: '본문이 바뀌었어요. AI 분석을 새로고침하세요.' });
  };

  // 복원 확인 팝업 — 칩 메뉴의 "전환"과 스냅샷의 "이 버전 복원" 두 경로가 모두 거친다.
  const confirmModal = confirmRestore && (
    <ConfirmDialog
      open
      tone="primary"
      title="이 버전으로 복원할까요?"
      confirmLabel={`${confirmRestore.ver}(으)로 복원`}
      confirmIcon="renew"
      onClose={() => setConfirmRestore(null)}
      onConfirm={() => {
        void restore(confirmRestore);
        setConfirmRestore(null);
      }}
      description={
        <>
          지금 적용 중인 지침이 <span className="font-mono font-semibold text-text-primary">{confirmRestore.ver}</span> 시점의
          지침으로 교체돼요. 복원은 새 버전(<span className="font-mono">{bumpVer(skill.ver)}</span>)으로 저장되며, 기존 버전
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
                // 빌트인은 버전 히스토리/복원이 없다(본문 불변) — 정적 버전 표기
                <span className="text-caption-01 font-mono text-text-secondary bg-layer-02 border border-border-subtle-01 rounded px-1.5 py-px">
                  {skill.ver}
                </span>
              ) : (
                <VersionChipMenu
                  versions={detail.versions}
                  currentVer={skill.ver}
                  onView={(ver) => setSnapVer(ver)}
                  onSwitch={(v) => setConfirmRestore(v)}
                />
              )}
              {builtin && (
                <Tag type="teal" size="sm">
                  기본 내장
                </Tag>
              )}
              {skill.shared && (
                <Tag type="purple" size="sm">
                  공유됨
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
              <p className={`text-body-short-01 leading-5 text-text-secondary ${summaryExpandable && !summaryOpen ? 'line-clamp-3' : ''}`}>
                {detail.summary}
              </p>
              {summaryExpandable && (
                <button
                  onClick={() => setSummaryOpen((open) => !open)}
                  className="mt-1 inline-flex items-center gap-1 text-caption-01 font-semibold text-link-primary hover:underline"
                  aria-expanded={summaryOpen}
                >
                  {summaryOpen ? '접기' : '더 보기'}
                  <Icon name={summaryOpen ? 'chevron-up' : 'chevron-down'} size={12} />
                </button>
              )}
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1.5 mt-3 text-caption-01">
              {([
                ['사용', `${skill.uses}회`],
                ['개선', `${skill.patches ?? 0}회`],
                ['생성', detail.createdFrom],
                ['최근 사용', detail.lastUsed],
              ] as const).map(([k, v]) => (
                <span key={k} className="inline-flex items-baseline gap-1.5 text-text-helper">
                  <span>{k}</span>
                  <span className="text-text-secondary">{v}</span>
                </span>
              ))}
            </div>
            {/* 출처 스트립 — 설치형/제작형일 때 원본·발산·업데이트를 표시 (학습형은 생략) */}
            <ProvenanceStrip skill={skill} detail={detail} onOpenMarket={() => navigate('/skills')} />
          </div>
          {/* 소스 보기 — 풀스크린 IDE 워크스페이스 진입. 전체화면 전환이라 임팩트 있게. */}
          <SourceLauncher skill={skill} detail={detail} onOpen={() => setWorkspaceOpen(true)} />
        </div>
      </div>

      {/* scrollable body — source of truth is SKILL.md; this surface explains it for humans */}
      <div className="flex-1 min-w-0 overflow-y-auto px-7 pt-5 pb-10 flex flex-col gap-7">
        <SkillUnderstandingPanel
          skill={skill}
          detail={detail}
          refreshing={analysisRefreshing}
          feedback={analysisFeedback}
          onRefresh={() => void refreshAnalysis()}
          onOpenConnectors={() => navigate('/connectors')}
        />

        <SkillTelemetryPanel skill={skill} detail={detail} />

        {/* version history — 빌트인은 본문 불변이라 히스토리·복원이 없다 */}
        {!builtin && (
        <div>
          <SectionLabel
            right={detail.historyStatus === 'loading' ? (
              <span className="inline-flex items-center gap-1.5 text-caption-01 text-text-helper normal-case">
                <Icon name="renew" size={11} className="animate-spin" /> 서버 히스토리 불러오는 중
              </span>
            ) : detail.historyStatus === 'error' ? (
              <button
                onClick={() => void loadRuntimeSkillDetail(skillId)}
                className="inline-flex items-center gap-1 text-caption-01 font-semibold text-support-warning normal-case hover:underline"
              >
                <Icon name="renew" size={11} /> 다시 불러오기
              </button>
            ) : (
              <span className="text-caption-01 text-text-helper normal-case">항목을 눌러 원문 보기 · 복원</span>
            )}
          >
            버전 히스토리 · {detail.historyStatus === 'loading' ? '…' : detail.versions.length}
          </SectionLabel>
          {detail.historyStatus === 'error' && (
            <div className="mb-3 flex items-start gap-2 rounded-lg border border-support-warning/35 bg-support-warning/10 px-3 py-2.5 text-caption-01 text-text-secondary">
              <Icon name="warning" size={13} className="mt-0.5 flex-shrink-0 text-support-warning" />
              <span>서버 버전 기록을 불러오지 못했어요. 현재 버전만 표시 중입니다{detail.historyError ? ` · ${detail.historyError}` : ''}</span>
            </div>
          )}
          <div className="max-w-[780px]">
            {detail.versions.map((v, i) => (
              <div key={v.ver} className="flex gap-2">
                <div className="flex flex-col items-center flex-shrink-0">
                  <div
                    className={`mt-2.5 h-2 w-2 rounded-full ${v.current ? 'bg-blue-50 ring-2 ring-blue-100' : 'bg-layer-03'}`}
                  />
                  {i < detail.versions.length - 1 && <div className="min-h-[14px] w-px flex-1 bg-border-strong-01" />}
                </div>
                <button
                  onClick={() => setSnapVer(v.ver)}
                  className="mb-1.5 flex min-w-0 flex-1 items-center gap-2 rounded-md border border-border-subtle-01 bg-layer-01 px-2.5 py-1.5 text-left transition-colors hover:border-border-subtle-02 hover:bg-layer-hover-01"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex min-w-0 items-center gap-1.5">
                      <span className="font-mono text-caption-01 font-semibold text-text-primary">{v.ver}</span>
                      {v.current && (
                        <span className="rounded bg-blue-60/20 px-1.5 py-px text-label-micro font-semibold text-blue-30">
                          현재
                        </span>
                      )}
                      <span className="truncate text-label-micro text-text-helper">{v.when}</span>
                      {v.author && (
                        <span className="inline-flex items-center gap-1 text-label-micro text-text-helper">
                          <Icon name="user" size={10} /> {v.author}
                        </span>
                      )}
                    </div>
                    <div className="mt-px truncate text-caption-01 text-text-secondary">{v.note}</div>
                    {v.metrics && (
                      <div className="mt-1 flex items-center gap-2 text-label-micro font-mono text-text-helper">
                        <span>성공률 {pctStr(v.metrics.successRate)}</span>
                        <span>· {v.metrics.uses}회</span>
                        {v.metrics.avgTurns != null && <span>· {v.metrics.avgTurns}턴</span>}
                      </div>
                    )}
                  </div>
                  <Icon name="arrow-right" size={13} className="flex-shrink-0 text-icon-secondary opacity-45" />
                </button>
              </div>
            ))}
            {detail.historyStatus === 'loaded' && detail.versions.length === 1 && (
              <div className="rounded-lg border border-border-subtle-01 bg-layer-01 px-3.5 py-3 text-caption-01 text-text-helper">
                아직 보관된 과거 버전이 없어요. 다음 본문 변경부터 이전 버전이 여기에 저장됩니다.
              </div>
            )}
          </div>
        </div>
        )}

      </div>

      {/* actions — 주 액션(편집)만 또렷이 노출하고, 보조·위험 액션은 ••• 메뉴로 접는다.
          빌트인은 편집·공유·삭제가 원천 차단(백엔드 422)이라 액션 자체를 노출하지 않고,
          허용되는 내보내기(사본 다운로드)만 남긴다 — 스킬 작성 교보재로 쓰라는 의도. */}
      <div className="flex-shrink-0 flex gap-2 border-t border-border-subtle-01 bg-background px-7 py-2 items-center">
        {builtin ? (
          <>
            <span className="inline-flex items-center gap-1.5 text-caption-01 text-text-helper">
              <Icon name="sparkle" size={13} className="text-teal-40" />
              기본 내장 스킬 — 활성/비활성만 전환할 수 있어요
            </span>
            <div className="flex-1" />
            <Button
              kind="ghost"
              size="sm"
              icon={<Icon name="export" size={14} />}
              onClick={() => downloadSkillBundle(skill, detail)}
            >
              내보내기
            </Button>
          </>
        ) : (
          <>
            {/* 편집 = 이 화면의 주 액션. 채팅 매개 patch로 안전하게 버전을 올린다. */}
            <button
              onClick={() => { setEditTarget(undefined); setEditOpen(true); }}
              title="대화로 변경 요청을 작성하고 새 버전으로 저장합니다"
              className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-lg text-body-short-01 font-semibold bg-interactive text-text-on-color hover:bg-interactive-hover transition-colors"
            >
              <Icon name="edit" size={14} />
              AI로 편집
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
          </>
        )}
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
          // 런타임에도 반영 — 로컬만 지우면 새로고침 때 부활한다. 런타임이 모르는
          // 스킬(목/로컬 전용)이면 404가 돌아올 뿐이라 무해하다.
          void api.deleteSkill(skillId);
          setConfirmDelete(false);
        }}
        description="삭제하면 스킬 본문, 분석 메타데이터, 버전 히스토리가 모두 사라지며 되돌릴 수 없어요."
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
          // 공개의 실체 — 런타임이 워크스페이스 skill-exports/ 아래에 유통 가능한
          // artifact(디렉토리 + tar.gz + checksum)를 만든다. 유통본과 원본의
          // 계보는 없다(관계 단절). 실패해도 마켓 카드(로컬)는 유지 — 재시도 가능.
          void api.exportSkill(skillId).then((res) => {
            if (res.success && res.data) {
              toast.notify({
                message: `유통 패키지가 준비됐어요 — 워크스페이스 skill-exports/${skill.name}`,
                variant: 'success',
              });
            } else {
              toast.notify({
                message: `유통 패키지 생성에 실패했어요${res.error ? ` — ${res.error}` : ''}`,
                variant: 'warning',
              });
            }
          });
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
  const navigate = useNavigate();
  const { skills, setSkills, hydrating } = useLearning();
  const [filter, setFilter] = useState<Filter>('전체');
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
    (filter === '기본 내장' && skillOrigin(s) === 'builtin') ||
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
    setCurateNote(null);
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
        fromVersion: p.from_version,
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
        <div className="px-4 pt-4 pb-2">
          <div className="domain-summary domain-summary--skill overflow-hidden rounded-xl text-white">
            <div className="p-4">
              <div className="flex items-center gap-1.5 mb-1">
                <span className="text-caption-01 text-white/70">나의 스킬</span>
              </div>
              <div className="text-heading-03 font-semibold text-white leading-none tabular-nums">
                {skills.length}
                <span className="text-body-short-01 font-normal text-white/70 ml-1">개</span>
              </div>
              <div className="text-caption-01 text-white/80 mt-1.5 flex gap-3">
                <span>{active} 활성</span>
                <span>{shared} 공유됨</span>
              </div>
            </div>
            {/* proof CTA — 카드 하단에 붙여 "현황 → 학습 효과 보기"로 읽히게 한다 (EVAL 6.1) */}
            <button
              onClick={() => setShowImpact(true)}
              aria-pressed={showImpact}
              className="domain-summary__action w-full flex items-center gap-2 px-4 py-2.5 border-t transition-colors"
            >
              <Icon name="growth" size={15} className="text-white flex-shrink-0" />
              <div className="flex-1 min-w-0 text-left">
                <div className="text-body-short-01 font-semibold text-white">학습 효과</div>
                <div className="text-caption-01 text-white/70">cold→warm 정량 비교</div>
              </div>
              <Icon name="arrow-right" size={14} className="text-white/70 flex-shrink-0" />
            </button>
          </div>
        </div>

        {/* 상태 기반 유지보수는 현황 카드와 필터 사이에 둔다. 목록 생성 액션인
            AI 통합과 같은 위계로 묶지 않고, 기존의 조용한 관리 행 패턴을 유지한다. */}
        <div className="px-4 pb-1.5">
          <button
            onClick={curateSkills}
            disabled={curating || consolidating}
            title="오래 안 쓴 스킬을 stale·보관으로 정리합니다"
            className="group flex h-7 w-full items-center gap-1.5 rounded-md px-1.5 text-text-secondary transition-colors hover:bg-layer-hover-01 hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Icon
              name="renew"
              size={13}
              className={`flex-shrink-0 ${curating ? 'animate-spin text-icon-primary' : 'text-icon-secondary group-hover:text-icon-primary'}`}
            />
            <span className="text-caption-01 font-semibold flex-shrink-0">{curating ? '정리 중…' : '스킬 정리'}</span>
            <span className="ml-auto truncate text-label-micro text-text-helper" title={curateNote ?? undefined}>
              {curateNote ?? `${STALE_DAYS}일↑ Stale · ${ARCHIVE_DAYS}일↑ 보관`}
            </span>
          </button>
        </div>

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
        value={railPanel.width} min={railPanel.min} max={railPanel.max} onResizeBy={railPanel.resizeBy}
        aria-label="Resize filters panel"
      />

      {/* skill list */}
      <div
        className="flex-shrink-0 bg-background border-r border-border-subtle-01 overflow-y-auto min-w-0"
        style={{ width: listPanel.width }}
      >
        {/* 다른 관리 화면과 같은 조용한 목록 헤더. AI 통합만 아이콘 액션으로
            극압축하고, 정확한 의미는 접근성 라벨과 툴팁으로 보완한다. */}
        <div className="flex items-center gap-2 px-5 pt-5 pb-3">
          <h1 className="min-w-0 flex-1 text-heading-03 font-semibold text-text-primary">스킬 목록</h1>
          <div className="relative flex-shrink-0">
            <button
              onClick={() => void runConsolidate()}
              disabled={consolidating || curating}
              title="AI가 유사하거나 중복된 스킬을 찾아 통합을 제안합니다"
              aria-label={consolidating ? 'AI 통합 제안 분석 중' : 'AI 통합 제안'}
              className={`flex h-7 w-7 items-center justify-center rounded-lg transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                consolidating || consolidateProposals.length > 0
                  ? 'bg-blue-60/15 text-link-primary'
                  : 'text-icon-secondary hover:bg-layer-hover-01 hover:text-text-primary'
              }`}
            >
              <Icon name={consolidating ? 'renew' : 'merge'} size={16} className={consolidating ? 'animate-spin' : ''} />
            </button>
            {consolidateProposals.length > 0 && (
              <span className="pointer-events-none absolute -right-1.5 -top-1.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-blue-60 px-1 text-[10px] font-semibold leading-none text-white tabular-nums">
                {consolidateProposals.length}
              </span>
            )}
          </div>
        </div>

        {consolidateNote && consolidateProposals.length === 0 && (
          <div
            className={`mx-5 mb-2 flex items-start gap-1.5 text-caption-01 ${
              consolidateNote.startsWith('통합 분석 실패') ? 'text-support-warning' : 'text-text-helper'
            }`}
            role="status"
            title={consolidateNote}
          >
            <Icon name={consolidateNote.startsWith('통합 분석 실패') ? 'warning' : 'check-circle'} size={12} className="mt-0.5 flex-shrink-0" />
            <span className="line-clamp-2 leading-4">{consolidateNote}</span>
          </div>
        )}

        <div>
          {/* 통합 제안 검토 목록 — 실행 버튼과 같은 목록 영역에서만 노출해
              AI 기능의 결과가 필터 탐색을 밀어내지 않도록 한다. */}
          {consolidateProposals.length > 0 && (
            <div className="border-b border-border-subtle-01 bg-layer-01 px-3 py-3">
              <div className="flex items-center gap-2 px-1 pb-2">
                <span className="text-caption-01 font-semibold text-text-primary">통합 제안 검토</span>
                <span className="text-caption-01 text-text-helper">{consolidateProposals.length}건</span>
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
                        {p.kind === 'patch' ? (p.from_version === 0 ? '신규 통합' : '병합') : '정리'}
                      </Tag>
                    </div>
                    <p className="text-caption-01 text-text-secondary mb-2 line-clamp-2">
                      {p.kind === 'patch' ? p.changelog : p.reason}
                    </p>
                    <div className="flex items-center justify-end gap-1.5">
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
                className={`flex items-start gap-2.5 w-full text-left border-b border-border-subtle-01 border-l-2 px-[18px] py-3 transition-colors ${
                  on && !showImpact ? 'bg-layer-selected-01 border-l-blue-50' : 'bg-transparent border-l-transparent hover:bg-layer-hover-01'
                } ${skillStatus(s) === 'archived' ? 'opacity-60' : !isInjected(skillStatus(s)) ? 'opacity-70' : ''}`}
              >
                <div
                  className={`mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg border ${
                    on && !showImpact
                      ? 'border-blue-50/35 bg-blue-60/16 text-blue-30'
                      : isInjected(skillStatus(s))
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
      </div>

      {/* List resize handle */}
      <ResizeHandle
        onPointerDown={listPanel.startResize}
        onDoubleClick={listPanel.resetWidth}
        isResizing={listPanel.isResizing}
        value={listPanel.width} min={listPanel.min} max={listPanel.max} onResizeBy={listPanel.resizeBy}
        aria-label="Resize skills list panel"
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
