import React from 'react';
import { OnboardingFlow } from './onboarding';
import type { ModelSettings } from './ModelPicker';

/**
 * ModelOnboarding — 최초 실행 온보딩의 공개 진입점(하위 호환 래퍼).
 *
 * 실제 화면은 모듈화된 다단계 플로우(./onboarding/OnboardingFlow)가 담당한다.
 * 기존 호출부(ChatScreen)의 계약(currentModel/modelMode/settings/onComplete)을
 * 그대로 유지하기 위해 얇은 래퍼만 남긴다.
 */

export interface ModelOnboardingProps {
  currentModel: string;
  modelMode: 'saas' | 'local';
  settings: ModelSettings;
  onComplete: (payload: { modelId: string; settings: ModelSettings; mode: 'saas' | 'local' }) => void;
}

export const ModelOnboarding: React.FC<ModelOnboardingProps> = (props) => <OnboardingFlow {...props} />;

// Made with Bob
