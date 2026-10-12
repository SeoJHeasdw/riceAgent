/**
 * 인앱 투어 스텝 정의 — **구조만** 담는다(순서·타겟·위치·이동).
 *
 * 각 스텝의 표시 문구(title/body)는 tour 카탈로그의 `steps.<id>.{title,body}` 에 있고,
 * TourOverlay 가 스텝 id 로 조회한다. 스텝을 추가하려면: 아래 배열에 한 항목 +
 * `src/locales/<lang>/tour.json` 의 steps 에 같은 id 로 문구를 채우면 된다.
 *
 * target: data-tour 속성값 → DOM 조회 키
 * placement: 말풍선이 타겟의 어느 방향으로 뜨는지 (TourOverlay가 위치를 계산)
 * navigateTo: 이 스텝을 보여주기 전에 앱을 이 경로로 이동시켜야 할 때 (선택)
 */
export interface TourStep {
  id: string;
  target: string;
  placement: 'right' | 'left' | 'top' | 'bottom';
  navigateTo?: string;
}

export const TOUR_STEPS: TourStep[] = [
  { id: 'learning-loop', target: 'learning-loop', placement: 'right', navigateTo: '/chat' },
  { id: 'chat-input', target: 'chat-input', placement: 'top', navigateTo: '/chat' },
  { id: 'model-settings', target: 'model-settings', placement: 'right', navigateTo: '/chat' },
  { id: 'my-skills', target: 'nav-my-skills', placement: 'right' },
  { id: 'myplugins', target: 'nav-myplugins', placement: 'right' },
  { id: 'connectors', target: 'nav-connectors', placement: 'right' },
  { id: 'skills-market', target: 'nav-skills', placement: 'right' },
  { id: 'plugins-market', target: 'nav-plugins', placement: 'right' },
  { id: 'settings', target: 'nav-settings', placement: 'right' },
];
