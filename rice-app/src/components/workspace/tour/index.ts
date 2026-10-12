export { TourOverlay } from './TourOverlay';
export type { TourOverlayProps } from './TourOverlay';
export { TOUR_STEPS } from './tourSteps';
export type { TourStep } from './tourSteps';
export {
  SCREEN_TOUR_KEYS,
  PATH_TO_SCREEN_TOUR,
  screenTourIdForPath,
  MY_SKILLS_TOUR_STEPS,
  MY_PLUGINS_TOUR_STEPS,
  CONNECTORS_TOUR_STEPS,
} from './screenTours';
export type { ScreenTourId } from './screenTours';
export { useScreenTour } from './useScreenTour';

/** localStorage 키 — 전역 투어 완료/스킵 여부 저장 */
export const TOUR_DONE_KEY = 'rice-tour-done';
