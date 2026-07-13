import i18n, { type Language } from '../../../../lib/i18n';
import type { CapabilitySpec } from './types';

/**
 * 시네마틱 온보딩 카피의 계약(구조).
 *
 * 실제 문구는 onboarding 카탈로그(`src/locales/<lang>/onboarding.json`)의 `cinematic`
 * 아래에 있다. getCinematicCopy 가 현재 언어로 통째로 읽어 이 형태로 돌려준다.
 * accent(색상)·capability id·eyebrow 코드처럼 언어 불변인 구조값도 카탈로그에 함께 보관하며
 * 번역 대상이 아니다(docs/i18n.md의 "구조화 콘텐츠" 절 참고).
 */
export interface CinematicCopy {
  skip: string;
  privacy: string;
  awakening: {
    eyebrow: string;
    assembling: string;
    awake: string;
    title: string;
    description: string;
  };
  loop: {
    eyebrow: string;
    title: string;
    description: string;
    nodes: Array<{ label: string; caption: string }>;
    dragHint: string;
    next: string;
  };
  ecosystem: {
    eyebrow: string;
    title: string;
    description: string;
    capabilities: CapabilitySpec[];
    add: string;
    continue: string;
    assembled: string;
  };
  model: {
    eyebrow: string;
    title: string;
    description: string;
    local: string;
    blocked: string;
    connect: string;
    back: string;
  };
  handoff: {
    eyebrow: string;
    title: string;
    description: string;
  };
}

/**
 * 현재 언어의 시네마틱 카피 묶음.
 *
 * `onboarding:cinematic` 서브트리를 returnObjects 로 통째로 읽는다. 카탈로그 구조가
 * CinematicCopy 와 1:1 이므로 캐스팅으로 계약을 복원한다(런타임 형태는 동일).
 */
export const getCinematicCopy = (language: Language): CinematicCopy =>
  i18n.getFixedT(language, 'onboarding')('cinematic', {
    returnObjects: true,
  }) as unknown as CinematicCopy;
