import type React from 'react';
import type { IconRailItem } from '../../components/workspace';
import { NavIcon } from './NavIcon';

/**
 * 네비게이션 매니페스트 — 앱의 모든 destination을 한 곳에서 정의한다.
 *
 * 좌측 IconRail(App.tsx)과 홈 허브(HomeScreen)가 같은 출처를 공유하므로,
 * 화면을 추가/변경할 때 이 배열만 고치면 양쪽에 반영된다(중복 제거 + 확장성).
 * 아이콘은 path(children)만 들고, 렌더는 NavIcon이 크기별로 담당한다.
 */

export type NavGroup = 'workspace' | 'market' | 'system';

export interface NavDestination {
  id: string;
  /** 레일 tooltip / aria 라벨 (기존 영문 유지) */
  label: string;
  /** 홈 허브 카드 제목 (미지정 시 label 사용) */
  title?: string;
  path: string;
  /** 홈 허브 카드 설명 */
  description: string;
  /** NavIcon children (24 viewBox stroke path) */
  iconPaths: React.ReactNode;
  /** 홈 허브에서 묶이는 섹션 */
  group?: NavGroup;
  /** 레일 하단 고정 (예: Settings) */
  pinBottom?: boolean;
  /** 레일에서 이 항목 뒤에 구분선 */
  dividerAfter?: boolean;
  /** 홈 허브 카드 그리드에서 제외 (예: home 자신) */
  hideFromHome?: boolean;
}

export const navDestinations: NavDestination[] = [
  {
    id: 'home',
    label: 'Home',
    title: '홈',
    path: '/home',
    description: '모든 기능을 한눈에 보고 이동해요',
    dividerAfter: true,
    hideFromHome: true,
    iconPaths: (
      <>
        <path d="M3 10.5 12 3l9 7.5" />
        <path d="M5 9.5V21h14V9.5" />
        <path d="M9.5 21v-6h5v6" />
      </>
    ),
  },
  {
    id: 'chat',
    label: 'Chat',
    title: '채팅',
    path: '/chat',
    description: '에이전트와 대화하며 일을 처리해요',
    group: 'workspace',
    iconPaths: (
      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
    ),
  },
  {
    id: 'my-skills',
    label: 'My skills',
    title: '나의 스킬',
    path: '/my-skills',
    description: '내가 만들고 저장한 스킬을 관리해요',
    group: 'workspace',
    iconPaths: (
      <>
        <circle cx="6" cy="9" r="2.4" />
        <circle cx="18" cy="15" r="2.4" />
        <path d="M8.3 9h5.4M10.3 15h5.4" />
        <path d="M13.7 9 16 12M10.3 15 8 12" />
      </>
    ),
  },
  {
    id: 'myplugins',
    label: 'My plugins',
    title: '나의 플러그인',
    path: '/my-plugins',
    description: '설치한 플러그인을 관리해요',
    group: 'workspace',
    dividerAfter: true,
    iconPaths: (
      <>
        <path d="M9 3v5M15 3v5" />
        <path d="M6 8h12v3a6 6 0 0 1-12 0V8z" />
        <path d="M12 17v4" />
      </>
    ),
  },
  {
    id: 'skills',
    label: 'Skill market',
    title: '스킬 마켓',
    path: '/skills',
    description: '커뮤니티 스킬을 찾아 추가해요',
    group: 'market',
    iconPaths: (
      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
    ),
  },
  {
    id: 'plugins',
    label: 'Plugin market',
    title: '플러그인 마켓',
    path: '/plugins',
    description: '기능을 확장하는 플러그인을 둘러봐요',
    group: 'market',
    dividerAfter: true,
    iconPaths: (
      <path d="M19.439 7.85c-.049.322.059.648.289.878l1.568 1.568c.47.47.706 1.087.706 1.704s-.235 1.233-.706 1.704l-1.611 1.611a.98.98 0 0 1-.837.276c-.47-.07-.802-.48-.968-.925a2.501 2.501 0 1 0-3.214 3.214c.446.166.855.497.925.968a.979.979 0 0 1-.276.837l-1.61 1.61a2.404 2.404 0 0 1-1.705.707 2.402 2.402 0 0 1-1.704-.706l-1.568-1.568a1.026 1.026 0 0 0-.877-.29c-.493.074-.84.504-1.02.968a2.5 2.5 0 1 1-3.237-3.237c.464-.18.894-.527.967-1.02a1.026 1.026 0 0 0-.289-.877l-1.568-1.568A2.402 2.402 0 0 1 1.998 12c0-.617.236-1.234.706-1.704L4.23 8.77c.24-.24.581-.353.917-.303.515.077.877.528 1.073 1.01a2.5 2.5 0 1 0 3.259-3.259c-.482-.196-.933-.558-1.01-1.073a1.026 1.026 0 0 1 .303-.917l1.525-1.525A2.402 2.402 0 0 1 12 1.998c.617 0 1.234.236 1.704.706l1.568 1.568c.23.23.556.338.877.29.493-.074.84-.504 1.02-.968a2.5 2.5 0 1 1 3.237 3.237c-.464.18-.894.527-.967 1.02Z" />
    ),
  },
  {
    id: 'connectors',
    label: 'Connectors',
    title: '커넥터',
    path: '/connectors',
    description: '외부 계정·데이터 소스를 연결해요',
    group: 'system',
    iconPaths: (
      <>
        <path d="M10 13a5 5 0 0 0 7.07 0l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
        <path d="M14 11a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
      </>
    ),
  },
  {
    id: 'settings',
    label: 'Settings',
    title: '설정',
    path: '/settings',
    description: '외관, 학습과 개인정보를 설정해요',
    group: 'system',
    pinBottom: true,
    iconPaths: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
      </>
    ),
  },
];

/** 레일(IconRail)이 쓰는 항목으로 변환 — 아이콘을 18px NavIcon으로 렌더 */
export const toRailItems = (): IconRailItem[] =>
  navDestinations.map(({ id, label, path, iconPaths, pinBottom, dividerAfter }) => ({
    id,
    label,
    path,
    pinBottom,
    dividerAfter,
    icon: <NavIcon size={18}>{iconPaths}</NavIcon>,
  }));
