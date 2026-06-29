import type React from 'react';
import type { ModelDraft, ModelSettings } from '../ModelPicker';

/**
 * 온보딩 플로우의 공용 타입.
 *
 * 온보딩은 "여러 개의 독립적인 스텝"으로 구성된다. 각 스텝은 자신의 화면을
 * 그릴 뿐 진행 상태(현재 스텝/네비게이션/진행바)는 셸(OnboardingFlow)이 관리한다.
 * 새 튜토리얼을 추가할 때는 OnboardingStep 하나를 더 정의해 steps 배열에 끼우면 된다.
 */

/** 모델 연결 시 셸이 들고 있는 초기값(=ChatScreen에서 내려준 현재 설정) */
export interface OnboardingInitial {
  currentModel: string;
  modelMode: 'saas' | 'local';
  settings: ModelSettings;
}

/** 각 스텝 렌더 함수에 전달되는 컨텍스트 */
export interface OnboardingStepContext {
  /** 모델 선택 draft (모델 스텝이 갱신, 다른 스텝이 요약에 사용) */
  draft: ModelDraft;
  /** draft 갱신 — 모델 스텝의 ModelPicker onChange와 연결된다 */
  setDraft: (draft: ModelDraft) => void;
  /** 셸이 보관 중인 최초 설정값 */
  initial: OnboardingInitial;
  /** 다음 스텝으로 이동(마지막 스텝이면 onComplete) */
  goNext: () => void;
  /** 특정 스텝 id로 점프(예: 소개 건너뛰고 모델 연결로) */
  goTo: (stepId: string) => void;
}

/** 온보딩 스텝 정의 */
export interface OnboardingStep {
  /** 고유 id (goTo 점프 대상) */
  id: string;
  /** 좌측 진행 레일에 표시될 짧은 제목 */
  navLabel: string;
  /** 좌측 진행 레일의 부가 설명(선택) */
  navDescription?: string;
  /** 이 스텝을 통과(다음 이동)하려면 만족해야 하는 조건. 미지정 시 항상 통과 */
  canAdvance?: (ctx: OnboardingStepContext) => boolean;
  /** 통과 불가일 때 하단에 보여줄 힌트 텍스트 */
  blockedHint?: (ctx: OnboardingStepContext) => string;
  /** 다음 버튼 라벨 오버라이드(미지정 시 셸 기본값) */
  primaryLabel?: string;
  /** 본문 렌더 */
  render: (ctx: OnboardingStepContext) => React.ReactNode;
}

// Made with Bob
