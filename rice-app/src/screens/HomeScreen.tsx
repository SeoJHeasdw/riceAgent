import React from 'react';
import { useNavigate } from 'react-router-dom';
import { navDestinations, type NavDestination, type NavGroup } from '../lib/navigation';
import { NavIcon } from '../lib/navigation/NavIcon';

/**
 * HomeScreen ("홈") — 네비게이션 허브.
 *
 * 앱의 모든 destination을 아이콘 + 설명 카드로 보여주고 클릭 시 이동한다.
 * destination 목록은 좌측 레일과 동일한 매니페스트(navDestinations)를 공유하므로
 * 화면이 추가되면 여기에도 자동 반영된다.
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

const NavCard: React.FC<{ dest: NavDestination; onClick: () => void; featured?: boolean }> = ({
  dest,
  onClick,
  featured = false,
}) => (
  <button
    onClick={onClick}
    className={`group flex items-start gap-4 text-left rounded-xl border border-border-subtle-01 bg-layer-01 shadow-elevation-1 transition-all duration-150 hover:border-border-strong-01 hover:shadow-elevation-2 hover:-translate-y-0.5 active:translate-y-0 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus ${
      featured ? 'p-5 col-span-full sm:col-span-2' : 'p-4'
    }`}
  >
    <span
      className={`flex items-center justify-center rounded-xl flex-shrink-0 ${
        GROUP_ACCENT[dest.group ?? 'workspace']
      } ${featured ? 'w-12 h-12' : 'w-11 h-11'}`}
    >
      <NavIcon size={featured ? 24 : 22}>{dest.iconPaths}</NavIcon>
    </span>
    <div className="min-w-0 flex-1">
      <div className="flex items-center gap-1.5">
        <span className={`font-semibold text-text-primary ${featured ? 'text-heading-02' : 'text-body-short-01'}`}>
          {dest.title ?? dest.label}
        </span>
        <NavIcon
          size={15}
          className="text-text-helper opacity-0 -translate-x-1 transition-all duration-150 group-hover:opacity-100 group-hover:translate-x-0"
        >
          <path d="M5 12h14M13 6l6 6-6 6" />
        </NavIcon>
      </div>
      <p className={`text-text-secondary leading-relaxed mt-1 ${featured ? 'text-body-short-01' : 'text-caption-01'}`}>
        {dest.description}
      </p>
    </div>
  </button>
);

const HomeScreen: React.FC = () => {
  const navigate = useNavigate();
  const visible = navDestinations.filter((d) => !d.hideFromHome);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-[860px] mx-auto px-8 py-10">
        {/* 헤더 */}
        <header className="mb-9">
          <p className="text-body-short-01 text-text-helper">{greeting()}</p>
          <h1 className="text-heading-04 font-semibold text-text-primary mt-1">무엇을 해볼까요?</h1>
          <p className="text-body-short-01 text-text-secondary mt-2 max-w-[520px]">
            RICE의 모든 기능으로 이동할 수 있어요. 자주 쓰는 작업은 대화 중 스킬로 저장돼 점점 더 빨라집니다.
          </p>
        </header>

        {/* 섹션별 카드 그리드 */}
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
                    <NavCard
                      key={dest.id}
                      dest={dest}
                      featured={dest.id === 'chat'}
                      onClick={() => navigate(dest.path)}
                    />
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
