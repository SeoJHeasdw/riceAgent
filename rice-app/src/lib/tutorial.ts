/**
 * 온보딩·투어 재실행 브리지.
 *
 * 온보딩/투어의 표시 상태는 App 셸이 관리하는데, 설정 화면(SettingsScreen)은
 * 그 상태에 직접 접근할 수 없다. 그래서 설정에서 window 커스텀 이벤트를 쏘면
 * App이 받아 다시 띄우는 얇은 이벤트 브리지를 둔다(전역 store 없이 결합 최소화).
 */

export const REPLAY_ONBOARDING_EVENT = 'rice:replay-onboarding';
export const REPLAY_TOUR_EVENT = 'rice:replay-tour';

/** 최초 실행 온보딩(전체화면 다단계)을 다시 띄운다. */
export const replayOnboarding = () => window.dispatchEvent(new CustomEvent(REPLAY_ONBOARDING_EVENT));

/** 인앱 둘러보기(말풍선 투어)를 다시 시작한다. */
export const replayTour = () => window.dispatchEvent(new CustomEvent(REPLAY_TOUR_EVENT));

// Made with Bob
