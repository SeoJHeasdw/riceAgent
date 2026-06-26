import React, { useState } from 'react';
import { Button } from '../ui';
import { ModelPicker, ModelDraft, ModelSettings } from './ModelPicker';

/**
 * ModelOnboarding — 최초 실행 시 보여주는 전체 화면 튜토리얼.
 *
 * 로컬(Electron) 앱이므로, 모델이 아직 연결되지 않았다면 채팅 전에 이 화면에서
 * 사용할 모델을 먼저 연결하게 한다. 연결을 마치면 onComplete로 설정을 저장하고
 * (provider.configured=true) 이후로는 다시 뜨지 않는다.
 */

export interface ModelOnboardingProps {
  currentModel: string;
  modelMode: 'saas' | 'local';
  settings: ModelSettings;
  onComplete: (payload: { modelId: string; settings: ModelSettings; mode: 'saas' | 'local' }) => void;
}

export const ModelOnboarding: React.FC<ModelOnboardingProps> = ({
  currentModel,
  modelMode,
  settings,
  onComplete,
}) => {
  const [draft, setDraft] = useState<ModelDraft>({
    modelId: currentModel,
    settings,
    mode: modelMode,
    canSave: false,
  });

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-background overflow-y-auto"
      role="dialog"
      aria-modal="true"
      aria-label="모델 연결"
    >
      <div className="w-full max-w-[520px] my-auto px-6 py-10" style={{ animation: 'onbIn 240ms cubic-bezier(0.16, 1, 0.3, 1)' }}>
        {/* 환영 헤더 */}
        <div className="flex flex-col items-center text-center mb-7">
          <span className="flex items-center justify-center w-14 h-14 rounded-2xl bg-interactive text-white shadow-elevation-2 mb-4">
            <svg width="28" height="28" viewBox="0 0 32 32" fill="currentColor">
              <path d="M16 2L4 8v9c0 7 5.2 11.4 12 13 6.8-1.6 12-6 12-13V8L16 2zm0 2.2l10 5v7.8c0 5.7-4.1 9.4-10 10.9C10.1 26.4 6 22.7 6 17V9.2l10-5z" />
              <path d="M14.2 19.6l-3.4-3.4 1.4-1.4 2 2 5-5 1.4 1.4z" />
            </svg>
          </span>
          <h1 className="text-heading-03 font-semibold text-text-primary">RICE에 오신 걸 환영합니다</h1>
          <p className="text-body-short-01 text-text-secondary mt-2 max-w-[400px]">
            시작하려면 사용할 AI 모델을 연결하세요. 클라우드 API(SaaS) 또는 내 기기의
            로컬 모델(Ollama) 중 하나를 선택할 수 있어요. 설정은 이 기기에 저장됩니다.
          </p>
        </div>

        {/* 모델 선택 본문 */}
        <div className="bg-layer-01 border border-border-subtle-01 rounded-xl p-5 shadow-overlay">
          <ModelPicker
            currentModel={currentModel}
            modelMode={modelMode}
            settings={settings}
            onboarding
            onChange={setDraft}
          />
        </div>

        {/* 시작 */}
        <div className="mt-5">
          <Button
            kind="primary"
            size="lg"
            fullWidth
            disabled={!draft.canSave}
            onClick={() => onComplete({ modelId: draft.modelId, settings: draft.settings, mode: draft.mode })}
          >
            <svg width="16" height="16" viewBox="0 0 32 32" fill="currentColor" className="mr-1.5">
              <path d="M13 24l-9-9 1.414-1.414L13 21.171 26.586 7.586 28 9 13 24z" />
            </svg>
            시작하기
          </Button>
          <p className="text-caption-01 text-text-helper text-center mt-2">
            {draft.canSave
              ? '연결 완료 — 시작할 수 있어요'
              : draft.mode === 'local'
              ? '로컬 모델을 확인하고 선택하면 시작할 수 있어요'
              : 'API 모델을 추가하면 시작할 수 있어요'}
          </p>
        </div>
      </div>

      <style>{`
        @keyframes onbIn {
          from { opacity: 0; transform: translateY(8px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
};

// Made with Bob
