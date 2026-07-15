/**
 * 화면별 단발성 투어 정의.
 *
 * 전역 투어(TOUR_STEPS)와 완전히 분리된다 — 이 파일을 수정해도 전역 투어는 변하지 않는다.
 *
 * 각 화면 투어의 표시 문구(title/body)는 tour 카탈로그의
 * `steps.<stepId>.{title,body}` 키로 조회한다.
 */
import type { TourStep } from './tourSteps';

/** 화면별 완료 키 — 버전이 포함돼 있어 재설계 시 자동으로 재실행된다. */
export const SCREEN_TOUR_KEYS = {
  mySkills:   'rice-tour-my-skills-v1',
  myPlugins:  'rice-tour-my-plugins-v1',
  connectors: 'rice-tour-connectors-v1',
} as const;

export type ScreenTourId = keyof typeof SCREEN_TOUR_KEYS;

/** 화면 경로 → 화면 투어 ID 매핑 */
export const PATH_TO_SCREEN_TOUR: Record<string, ScreenTourId> = {
  '/my-skills':   'mySkills',
  '/my-plugins':  'myPlugins',
  '/connectors':  'connectors',
};

/** 상세 라우트(`/connectors/:id`)도 상위 Connectors 투어에 포함한다. */
export const screenTourIdForPath = (pathname: string): ScreenTourId | null =>
  PATH_TO_SCREEN_TOUR[pathname]
  ?? (pathname.startsWith('/connectors/') ? 'connectors' : null);

// ── My Skills: 6단계 ──────────────────────────────────────────────────────

export const MY_SKILLS_TOUR_STEPS: TourStep[] = [
  {
    id: 'screen-myskills-overview',
    target: 'myskills-detail-header',
    placement: 'bottom',
  },
  {
    id: 'screen-myskills-overview-tab',
    target: 'myskills-tab-overview',
    placement: 'bottom',
  },
  {
    id: 'screen-myskills-insights',
    target: 'myskills-tab-insights',
    placement: 'bottom',
  },
  {
    id: 'screen-myskills-manage',
    target: 'myskills-manage-btn',
    placement: 'bottom',
  },
  {
    id: 'screen-myskills-files',
    target: 'myskills-source-launcher',
    placement: 'bottom',
  },
  {
    id: 'screen-myskills-ai-edit',
    target: 'myskills-detail-actions',
    placement: 'bottom',
  },
];

// ── My Plugins: 3단계 ──────────────────────────────────────────────────────

export const MY_PLUGINS_TOUR_STEPS: TourStep[] = [
  {
    id: 'screen-myplugins-skills',
    target: 'myplugins-tab-skills',
    placement: 'bottom',
  },
  {
    id: 'screen-myplugins-knowledge',
    target: 'myplugins-tab-knowledge',
    placement: 'bottom',
  },
  {
    id: 'screen-myplugins-permissions',
    target: 'myplugins-tab-permissions',
    placement: 'bottom',
  },
];

// ── Connectors: 4단계 ──────────────────────────────────────────────────────

export const CONNECTORS_TOUR_STEPS: TourStep[] = [
  {
    id: 'screen-connectors-kinds',
    target: 'connectors-kind-tabs',
    placement: 'right',
  },
  {
    id: 'screen-connectors-add',
    target: 'connectors-list-header',
    placement: 'bottom',
  },
  {
    id: 'screen-connectors-tool-groups',
    target: 'connectors-detail-tools',
    placement: 'left',
  },
  {
    id: 'screen-connectors-permissions',
    target: 'connectors-detail-perm-control',
    placement: 'left',
  },
];

// Made with Bob
