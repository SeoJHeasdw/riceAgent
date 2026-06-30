import React from 'react';
import { useNavigate } from 'react-router-dom';
import { navDestinations, type NavDestination, type NavGroup } from '../lib/navigation';
import { NavIcon } from '../lib/navigation/NavIcon';
import { useLearning } from '../lib/learning/LearningContext';

/**
 * HomeScreen ("홈") — 개인화 홈 베이스.
 *
 * 단순 네비게이션 그리드(좌측 레일과 중복)를 넘어, 제품의 핵심 약속인
 * "쓸수록 똑똑해진다"를 한눈에 보여주는 대시보드다. 학습 store(LearningContext)에서
 * 스킬·신호·제안을 읽어 ① 학습 현황 ② 자주 쓰는 스킬 ③ 대기 중 제안을 요약하고,
 * 하단에 모든 destination으로 가는 카드 그리드(공용 navDestinations)를 둔다.
 */

/** 홈 허브 섹션 구성 (group → 라벨/순서) */
const GROUPS: { id: NavGroup; label: string }[] = [
  { id: 'workspace', label: '워크스페이스' },
  { id: 'market', label: '마켓플레이스' },
  { id: 'system', label: '연결 · 설정' },
];

/** group별 아이콘 강조 색 (시각적 구분) */
const GROUP_ACCENT: Record<NavGroup, string> = {
  workspace: 'text-blue-50 bg-blue-60/12',
  market: 'text-purple-60 bg-purple-60/12',
  system: 'text-teal-60 bg-teal-60/12',
};

const greeting = (): string => {
  const h = new Date().getHours();
  if (h < 6) return '늦은 밤이네요';
  if (h < 12) return '좋은 아침이에요';
  if (h < 18) return '좋은 오후예요';
  return '좋은 저녁이에요';
};

/* ── 24px 스트로크 라인 아이콘 (대시보드 타일용) ── */
const LineIcon: React.FC<{ size?: number; children: React.ReactNode }> = ({ size = 20, children }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.7"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden
  >
    {children}
  </svg>
);

const ARROW = <path d="M5 12h14M13 6l6 6-6 6" />;

/* ── 학습 현황 통계 타일 ── */
const StatTile: React.FC<{
  accent: string;
  icon: React.ReactNode;
  value: React.ReactNode;
  label: string;
}> = ({ accent, icon, value, label }) => (
  <div className="flex items-center gap-3 rounded-xl border border-border-subtle-01 bg-layer-01 px-4 py-3.5">
    <span className={`flex items-center justify-center w-9 h-9 rounded-lg flex-shrink-0 ${accent}`}>{icon}</span>
    <div className="min-w-0">
      <div className="text-heading-03 font-semibold text-text-primary tabular-nums leading-none">{value}</div>
      <div className="text-caption-01 text-text-helper mt-1">{label}</div>
    </div>
  </div>
);

/* ── 네비게이션 카드 ── */
const NavCard: React.FC<{ dest: NavDestination; onClick: () => void }> = ({ dest, onClick }) => (
  <button
    onClick={onClick}
    className="group flex items-start gap-4 text-left rounded-xl border border-border-subtle-01 bg-layer-01 shadow-elevation-1 p-4 transition-all duration-150 hover:border-border-strong-01 hover:shadow-elevation-2 hover:-translate-y-0.5 active:translate-y-0 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
  >
    <span
      className={`flex items-center justify-center w-11 h-11 rounded-xl flex-shrink-0 ${
        GROUP_ACCENT[dest.group ?? 'workspace']
      }`}
    >
      <NavIcon size={22}>{dest.iconPaths}</NavIcon>
    </span>
    <div className="min-w-0 flex-1">
      <div className="flex items-center gap-1.5">
        <span className="font-semibold text-text-primary text-body-short-01">{dest.title ?? dest.label}</span>
        <NavIcon
          size={15}
          className="text-text-helper opacity-0 -translate-x-1 transition-all duration-150 group-hover:opacity-100 group-hover:translate-x-0"
        >
          {ARROW}
        </NavIcon>
      </div>
      <p className="text-text-secondary leading-relaxed mt-1 text-caption-01">{dest.description}</p>
    </div>
  </button>
);

const HomeScreen: React.FC = () => {
  const navigate = useNavigate();
  const { skills, signals, proposals, hydrating } = useLearning();

  const visible = navDestinations.filter((d) => !d.hideFromHome);

  // ── 학습 현황 집계 ──
  const activeSkills = skills.filter((s) => s.state !== 'archived');
  const totalUses = skills.reduce((sum, s) => sum + (s.uses || 0), 0);
  const totalPatches = skills.reduce((sum, s) => sum + (s.patches || 0), 0);
  const hasLearning = activeSkills.length > 0 || signals.length > 0;

  // 자주 쓰는 스킬 Top 4 (사용 횟수 내림차순)
  const topSkills = [...activeSkills].sort((a, b) => (b.uses || 0) - (a.uses || 0)).slice(0, 4);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-[920px] mx-auto px-8 py-10">
        {/* ── 히어로 ── */}
        <header className="mb-9">
          <p className="text-body-short-01 text-text-helper">{greeting()}</p>
          <h1 className="text-heading-04 font-semibold text-text-primary mt-1">무엇을 해볼까요?</h1>
          <p className="text-body-short-01 text-text-secondary mt-2 max-w-[560px]">
            RICE는 대화할수록 내 작업 방식을 학습하고 반복 작업을 스킬로 저장해요. 이어서 일을 시작해 보세요.
          </p>

          {/* 주요 행동 — 새 대화 */}
          <div className="mt-5 flex flex-wrap items-center gap-2.5">
            <button
              onClick={() => navigate('/chat')}
              className="group inline-flex items-center gap-2 rounded-xl bg-interactive px-4 py-2.5 text-white shadow-elevation-1 transition-all duration-150 hover:brightness-110 hover:shadow-elevation-2 active:scale-[0.98] outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
            >
              <LineIcon size={18}>
                <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
              </LineIcon>
              <span className="text-body-short-01 font-semibold">새 대화 시작</span>
              <NavIcon size={16} className="-translate-x-0.5 transition-transform duration-150 group-hover:translate-x-0">
                {ARROW}
              </NavIcon>
            </button>
            <button
              onClick={() => navigate('/skills')}
              className="inline-flex items-center gap-2 rounded-xl border border-border-subtle-01 bg-layer-01 px-4 py-2.5 text-text-secondary transition-colors duration-150 hover:border-border-strong-01 hover:text-text-primary outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
            >
              <LineIcon size={18}>
                <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
              </LineIcon>
              <span className="text-body-short-01 font-medium">스킬 마켓 둘러보기</span>
            </button>
          </div>
        </header>

        {/* ── 학습 현황 ── */}
        <section className="mb-8">
          <div className="flex items-center gap-2 mb-3">
            <h2 className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">학습 현황</h2>
            {proposals.length > 0 && (
              <button
                onClick={() => navigate('/chat')}
                className="ml-auto inline-flex items-center gap-1.5 text-caption-01 font-medium text-blue-50 hover:text-blue-60 transition-colors"
              >
                <span className="relative flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full rounded-full bg-blue-50 opacity-60 animate-ping" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-blue-50" />
                </span>
                검토할 제안 {proposals.length}건
                <NavIcon size={13}>{ARROW}</NavIcon>
              </button>
            )}
          </div>

          {hydrating ? (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="h-[68px] rounded-xl border border-border-subtle-01 bg-layer-01 animate-pulse" />
              ))}
            </div>
          ) : hasLearning ? (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <StatTile
                accent="text-blue-50 bg-blue-60/12"
                icon={
                  <LineIcon size={18}>
                    <circle cx="6" cy="9" r="2.4" />
                    <circle cx="18" cy="15" r="2.4" />
                    <path d="M8.3 9h5.4M10.3 15h5.4" />
                    <path d="M13.7 9 16 12M10.3 15 8 12" />
                  </LineIcon>
                }
                value={activeSkills.length}
                label="저장된 스킬"
              />
              <StatTile
                accent="text-teal-60 bg-teal-60/12"
                icon={
                  <LineIcon size={18}>
                    <path d="M3 17l5-5 4 4 8-8" />
                    <path d="M16 8h4v4" />
                  </LineIcon>
                }
                value={totalUses}
                label="누적 사용"
              />
              <StatTile
                accent="text-purple-60 bg-purple-60/12"
                icon={
                  <LineIcon size={18}>
                    <path d="M12 3v3M12 18v3M3 12h3M18 12h3" />
                    <circle cx="12" cy="12" r="4" />
                  </LineIcon>
                }
                value={totalPatches}
                label="자동 개선"
              />
              <StatTile
                accent="text-[#ff832b] bg-[#ff832b]/12"
                icon={
                  <LineIcon size={18}>
                    <path d="M12 5a3 3 0 0 0-3 3 3 3 0 0 0-1.5 5.6A2.5 2.5 0 0 0 9 18a3 3 0 0 0 3 1" />
                    <path d="M12 5a3 3 0 0 1 3 3 3 3 0 0 1 1.5 5.6A2.5 2.5 0 0 1 15 18a3 3 0 0 1-3 1" />
                    <path d="M12 5v14" />
                  </LineIcon>
                }
                value={signals.length}
                label="학습된 신호"
              />
            </div>
          ) : (
            // 아직 학습 데이터가 없을 때 — 첫 대화를 유도
            <div className="flex items-center gap-4 rounded-xl border border-dashed border-border-strong-01 bg-layer-01 px-5 py-4">
              <span className="flex items-center justify-center w-10 h-10 rounded-xl bg-blue-60/12 text-blue-50 flex-shrink-0">
                <LineIcon size={20}>
                  <path d="M12 5a3 3 0 0 0-3 3 3 3 0 0 0-1.5 5.6A2.5 2.5 0 0 0 9 18a3 3 0 0 0 3 1" />
                  <path d="M12 5a3 3 0 0 1 3 3 3 3 0 0 1 1.5 5.6A2.5 2.5 0 0 1 15 18a3 3 0 0 1-3 1" />
                  <path d="M12 5v14" />
                </LineIcon>
              </span>
              <div className="min-w-0">
                <p className="text-body-short-01 font-medium text-text-primary">아직 학습한 내용이 없어요</p>
                <p className="text-caption-01 text-text-helper mt-0.5">
                  대화를 시작하면 작업 패턴과 스킬이 여기에 쌓이고, 쓸수록 더 빨라져요.
                </p>
              </div>
            </div>
          )}
        </section>

        {/* ── 자주 쓰는 스킬 ── */}
        {topSkills.length > 0 && (
          <section className="mb-8">
            <div className="flex items-center mb-3">
              <h2 className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">자주 쓰는 스킬</h2>
              <button
                onClick={() => navigate('/my-skills')}
                className="ml-auto inline-flex items-center gap-1 text-caption-01 font-medium text-text-helper hover:text-text-secondary transition-colors"
              >
                전체 보기
                <NavIcon size={13}>{ARROW}</NavIcon>
              </button>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              {topSkills.map((skill) => (
                <button
                  key={skill.id}
                  onClick={() => navigate('/my-skills')}
                  className="group flex items-center gap-3 text-left rounded-xl border border-border-subtle-01 bg-layer-01 px-3.5 py-3 transition-all duration-150 hover:border-border-strong-01 hover:shadow-elevation-1 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
                >
                  <span className="flex items-center justify-center w-8 h-8 rounded-lg bg-blue-60/12 text-blue-50 flex-shrink-0">
                    <LineIcon size={16}>
                      <rect x="3" y="4" width="18" height="16" rx="2.5" />
                      <path d="M14 9l-4 6" />
                    </LineIcon>
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-body-short-01 text-text-primary truncate">
                        {skill.command ? `/${skill.command}` : skill.name}
                      </span>
                      {skill.hot && (
                        <span className="text-[10px] font-semibold text-[#ff832b] flex-shrink-0">인기</span>
                      )}
                    </div>
                    <div className="text-caption-01 text-text-helper mt-0.5 truncate">
                      {skill.ver} · {skill.uses}회 사용
                      {skill.patches ? ` · ${skill.patches}회 개선` : ''}
                    </div>
                  </div>
                  <NavIcon
                    size={15}
                    className="text-text-helper opacity-0 -translate-x-1 transition-all duration-150 group-hover:opacity-100 group-hover:translate-x-0 flex-shrink-0"
                  >
                    {ARROW}
                  </NavIcon>
                </button>
              ))}
            </div>
          </section>
        )}

        {/* ── 모든 기능 (네비게이션 그리드) ── */}
        <div className="flex flex-col gap-8">
          {GROUPS.map((group) => {
            const items = visible.filter((d) => d.group === group.id);
            if (items.length === 0) return null;
            return (
              <section key={group.id}>
                <h2 className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary mb-3">
                  {group.label}
                </h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {items.map((dest) => (
                    <NavCard key={dest.id} dest={dest} onClick={() => navigate(dest.path)} />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      </div>
    </div>
  );
};

export default HomeScreen;

// Made with Bob
