/**
 * 데모 촬영 모드 (main). `RICE_DEMO=1`일 때만 켜진다.
 *
 * 소개 영상을 자동으로 찍을 때만 쓰는 스위치다. 꺼져 있으면 RICE는 평소와 완전히
 * 같아야 한다 — 그래서 데모를 아는 코드는 이 파일과 렌더러의 `src/lib/demo.ts`
 * 둘뿐이고, 나머지는 여기서 만든 값만 받는다.
 *
 * 촬영은 개발 서버 화면이 아니라 빌드된 화면을 찍는다. DevTools·HMR 배너처럼
 * 제품에 없는 것이 영상에 남으면 안 되기 때문이다.
 */

/** 이번 실행이 데모 촬영인가. */
export const demoEnabled = process.env.RICE_DEMO === '1';

/**
 * 애니메이션을 실제의 몇 배 속도로 돌릴지. 기본은 ¼이다.
 *
 * 4K에서 3D 장면은 초당 9장쯤밖에 그리지 못한다. ¼ 속도로 찍어 편집에서 4배로
 * 감으면 그 9장이 1초 분량의 36장이 되어 부드럽게 보인다. 1은 실제 속도다.
 */
export function demoTimeScale(raw: string | undefined = process.env.RICE_DEMO_TIME_SCALE): number {
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 && value <= 1 ? value : 0.25;
}

/** 창 주소에 붙일 질의. 렌더러는 이것으로만 데모 여부를 안다. */
export function demoSearch(): string {
  return demoEnabled ? `demo=1&timeScale=${demoTimeScale()}` : '';
}

// Made with Bob
