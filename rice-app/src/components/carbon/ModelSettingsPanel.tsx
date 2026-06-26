import React, { useState } from 'react';
import { Button } from '../ui';
import { ModelPicker, ModelDraft, ModelSettings } from './ModelPicker';

/**
 * ModelSettingsPanel — 모델 설정 슬라이드 패널.
 * 본문(실행 위치/모델 선택/파라미터)은 공용 ModelPicker가 담당하고,
 * 여기서는 헤더 + 저장/취소 프레임만 감싼다.
 */

// 공용 타입 재노출 (기존 import 경로 호환: mockData, carbon/index 등)
export type { Model, ModelSettings, RegisteredModel } from './ModelPicker';

export interface ModelSettingsPanelProps {
  isOpen: boolean;
  onClose: () => void;
  currentModel: string;
  onSave: (payload: { modelId: string; settings: ModelSettings; mode: 'saas' | 'local' }) => void;
  settings: ModelSettings;
  modelMode: 'saas' | 'local';
}

export const ModelSettingsPanel: React.FC<ModelSettingsPanelProps> = ({
  isOpen,
  onClose,
  currentModel,
  onSave,
  settings,
  modelMode,
}) => {
  const [draft, setDraft] = useState<ModelDraft>({
    modelId: currentModel,
    settings,
    mode: modelMode,
    canSave: false,
  });

  if (!isOpen) return null;

  const handleSave = () => {
    onSave({ modelId: draft.modelId, settings: draft.settings, mode: draft.mode });
    onClose();
  };

  return (
    <>
      <div
        onClick={onClose}
        className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40"
        style={{ animation: 'panelFadeIn 160ms ease' }}
      />

      <div
        className="fixed top-0 right-0 bottom-0 w-[380px] bg-layer-01 border-l border-border-subtle-01 z-50 flex flex-col shadow-overlay"
        style={{ animation: 'panelSlideIn 200ms cubic-bezier(0.16, 1, 0.3, 1)' }}
        role="dialog"
        aria-modal="true"
        aria-label="Model settings"
      >
        {/* Header */}
        <div className="flex items-center px-5 py-3.5 border-b border-border-subtle-01">
          <span className="text-heading-02 font-semibold text-text-primary">모델 설정</span>
          <button
            onClick={onClose}
            className="ml-auto p-1.5 hover:bg-layer-hover-01 transition-colors rounded-md"
            title="Close"
          >
            <svg width="20" height="20" viewBox="0 0 32 32" fill="currentColor" className="text-icon-primary">
              <path d="M24 9.4L22.6 8 16 14.6 9.4 8 8 9.4l6.6 6.6L8 22.6 9.4 24l6.6-6.6 6.6 6.6 1.4-1.4-6.6-6.6L24 9.4z" />
            </svg>
          </button>
        </div>

        {/* Content — 열릴 때마다 최신 prop으로 새로 초기화하도록 key로 remount */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          <ModelPicker
            key={`${currentModel}|${modelMode}`}
            currentModel={currentModel}
            modelMode={modelMode}
            settings={settings}
            onChange={setDraft}
          />
        </div>

        {/* Footer */}
        <div className="px-5 py-2.5 border-t border-border-subtle-01 flex gap-2">
          <Button
            kind="secondary"
            size="sm"
            onClick={onClose}
            className="flex-1 !bg-layer-02 hover:!bg-layer-hover-02"
          >
            취소
          </Button>
          <Button
            kind="primary"
            size="sm"
            onClick={handleSave}
            disabled={!draft.canSave}
            className="flex-1"
            title={draft.canSave ? undefined : draft.mode === 'local' ? '설치된 모델을 확인하고 선택하세요' : '먼저 모델을 추가하세요'}
          >
            <svg width="14" height="14" viewBox="0 0 32 32" fill="currentColor" className="mr-1.5">
              <path d="M13 24l-9-9 1.414-1.414L13 21.171 26.586 7.586 28 9 13 24z" />
            </svg>
            저장
          </Button>
        </div>
      </div>

      <style>{`
        @keyframes panelFadeIn { from { opacity: 0; } to { opacity: 1; } }
        @keyframes panelSlideIn { from { transform: translateX(100%); } to { transform: translateX(0); } }
      `}</style>
    </>
  );
};

// Made with Bob
