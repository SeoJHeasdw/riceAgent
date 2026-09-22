/**
 * 데모 촬영 모드 (렌더러). 창 주소의 `?demo=1`로 켜진다 — `electron/demo.ts`가 붙인다.
 *
 * 하는 일은 둘이다. 문서에 `data-demo` 표식을 남겨 지금 찍히는 화면이 데모임을
 * 밝히고, 애니메이션의 시간을 늦춘다. 꺼져 있으면 아무것도 하지 않는다.
 */
import { useEffect, type RefObject } from 'react';

const params = typeof window === 'undefined'
  ? new URLSearchParams()
  : new URLSearchParams(window.location.search);

/** 이번 실행이 데모 촬영인가. */
export const demoEnabled = params.get('demo') === '1';

/** 애니메이션을 실제의 몇 배 속도로 돌릴지. `electron/demo.ts`와 같은 기본값이다. */
export const demoTimeScale = (() => {
  const value = Number(params.get('timeScale'));
  return Number.isFinite(value) && value > 0 && value <= 1 ? value : 0.25;
})();

/** 데모에서 늘어난 시간. 장면 전환처럼 타이머로 재는 길이에 쓴다. */
export const demoStretch = (ms: number): number =>
  demoEnabled ? Math.round(ms / demoTimeScale) : ms;

/** 화면에 데모 표식을 남긴다. 촬영이 이 표식으로 데모 모드를 확인한다. */
export const markDemoDocument = (): void => {
  if (!demoEnabled) return;
  document.documentElement.dataset.demo = String(demoTimeScale);
};

interface DemoClock {
  getDelta(): number;
  elapsedTime: number;
}

/** 촬영이 배율을 확인할 수 있게 내놓는 창구. 데모에서만 생긴다. */
interface DemoWindow extends Window {
  __riceDemo?: { timeScale: number; clock: DemoClock };
}

/**
 * three의 시계를 늦춘다.
 *
 * R3F의 렌더 루프는 매 프레임 `clock.getDelta()` 하나로 `delta`와 `elapsedTime`을
 * 모두 만든다(`@react-three/fiber`의 `update`). 그래서 이 하나만 감싸면 경과 시간으로
 * 그리든 증분으로 그리든 장면 전체가 같은 배율로 느려진다. `getDelta`는 이미
 * `elapsedTime`을 실제만큼 늘린 뒤이므로 그만큼 되돌린다.
 */
export const slowClockForDemo = (clock: DemoClock): void => {
  if (!demoEnabled) return;
  const realDelta = clock.getDelta.bind(clock);
  clock.getDelta = () => {
    const step = realDelta();
    clock.elapsedTime -= step * (1 - demoTimeScale);
    return step * demoTimeScale;
  };
  // 배율이 걸렸다고 믿지 말고 재 볼 수 있게 시계를 내놓는다. 화면에는 영향이 없다.
  (window as DemoWindow).__riceDemo = { timeScale: demoTimeScale, clock };
};

/**
 * 이 가지 안의 CSS 애니메이션·전환을 같은 배율로 늦춘다.
 *
 * 3D만 늦추면 그 위의 글자·빛이 제 속도로 움직여 장면이 어긋난다. 재생 속도는
 * 애니메이션마다 붙는 값이라, 단계가 바뀌며 새로 생기는 것들을 위해 되풀이해서 건다.
 *
 * 매 프레임 건다. 뒤늦게 걸면 `updatePlaybackRate`가 이미 지나온 자리를 새 배율로
 * 다시 재어 애니메이션이 **뒤로 되감긴다** — 온보딩 마지막 장면이 사라졌다가 다시
 * 나타나고 그 사이 뒤의 화면이 비쳤다(2026-09-21, 4K 촬영에서 120ms 간격으로 걸었을 때).
 * 한 프레임 안에 걸면 되감기는 양이 그 프레임만큼이라 보이지 않는다.
 */
export const useDemoAnimationSpeed = (ref: RefObject<HTMLElement | null>): void => {
  useEffect(() => {
    if (!demoEnabled) return;
    let frame = 0;
    const apply = () => {
      const root = ref.current;
      if (root?.getAnimations) {
        for (const animation of root.getAnimations({ subtree: true })) {
          if (animation.playbackRate !== demoTimeScale) animation.updatePlaybackRate(demoTimeScale);
        }
      }
      frame = requestAnimationFrame(apply);
    };
    apply();
    return () => cancelAnimationFrame(frame);
  }, [ref]);
};

// Made with Bob
