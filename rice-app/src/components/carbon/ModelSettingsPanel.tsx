import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Icon } from '../ui';
import { ModelPicker, ModelDraft, ModelSettings } from './ModelPicker';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { useDialogA11y } from '../../hooks/useDialogA11y';

/**
 * ModelSettingsPanel — 모델 설정 슬라이드 패널.
 * 본문(실행 위치/모델 선택/파라미터)은 공용 ModelPicker가 담당하고,
 * 여기서는 헤더 + 저장/취소 프레임만 감싼다.
 */

// 공용 타입 재노출 (기존 import 경로 호환: mockData, carbon/index 등)
export type { Model, ModelSettings, RegisteredModel } from './ModelPicker';

const CLOSE_MS = 180;

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
  const { t } = useTranslation('chat');

  const [draft, setDraft] = useState<ModelDraft>({
    modelId: currentModel,
    settings,
    mode: modelMode,
    canSave: false,
  });

  // 저장 의미 있는 변경 여부:
  // mode 전환이나 "로컬 모델 확인" 같은 탐색 행위는 제외하고,
  // 실제로 저장될 값(modelId · registeredModels · temperature · endpoint · Show Thinking)이
  // 패널을 열 때의 초기값과 달라졌을 때만 dirty로 판단한다.
  const savedModelId = useRef(currentModel);
  const savedSettings = useRef(settings);
  const savedMode = useRef(modelMode);

  const computeDirty = useCallback((d: ModelDraft): boolean => {
    // 탭 전환 자체는 탐색 행위 — 저장될 실제 값이 바뀌었는지만 본다.
    // mode가 원래와 달라졌더라도 저장 시 그 mode에 맞는 값이 동일하면 무시.
    const modeChanged = d.mode !== savedMode.current;
    const modelChanged = d.modelId !== savedModelId.current;
    const a = d.settings;
    const b = savedSettings.current;
    const paramsChanged =
      a.temperature !== b.temperature ||
      a.showThinking !== b.showThinking ||
      (a.endpoint ?? '') !== (b.endpoint ?? '');
    const registeredChanged =
      a.registeredModels.length !== b.registeredModels.length ||
      a.registeredModels.some((ra) => !b.registeredModels.find((rb) => rb.modelId === ra.modelId));

    // mode만 바뀌고 나머지가 동일한 경우도 저장하면 달라지므로 dirty로 본다.
    // 단, modeChanged && !modelChanged && !paramsChanged && !registeredChanged 이면
    // 탭만 왔다갔다 한 것 — 경고 불필요.
    if (modeChanged && !modelChanged && !paramsChanged && !registeredChanged) return false;
    return modeChanged || modelChanged || paramsChanged || registeredChanged;
  }, []);

  const [isDirty, setIsDirty] = useState(false);

  const handleDraftChange = useCallback((d: ModelDraft) => {
    setDraft(d);
    setIsDirty(computeDirty(d));
  }, [computeDirty]);

  // 나가기 확인 다이얼로그
  const [confirmOpen, setConfirmOpen] = useState(false);

  // 닫힘 애니메이션: closing 동안 slide-out을 재생한 뒤 실제 onClose
  const [closing, setClosing] = useState(false);
  const closingRef = useRef(false);
  const doClose = useCallback(() => {
    if (closingRef.current) return;
    closingRef.current = true;
    setClosing(true);
    window.setTimeout(() => {
      closingRef.current = false;
      setClosing(false);
      onClose();
    }, CLOSE_MS);
  }, [onClose]);

  // dirty 상태면 확인 다이얼로그를 먼저 띄운다
  const requestClose = useCallback(() => {
    if (isDirty) {
      setConfirmOpen(true);
    } else {
      doClose();
    }
  }, [isDirty, doClose]);

  const panelRef = useRef<HTMLDivElement>(null);
  useDialogA11y({
    open: isOpen && !confirmOpen && !closing,
    dialogRef: panelRef,
    onClose: requestClose,
  });

  // 패널이 다시 열릴 때 저장 기준값 · dirty 상태 초기화
  useEffect(() => {
    if (isOpen) {
      savedModelId.current = currentModel;
      savedSettings.current = settings;
      savedMode.current = modelMode;
      setIsDirty(false);
      setConfirmOpen(false);
    }
  }, [isOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  // 본문이 푸터 아래로 이어질 때만 푸터에 스크롤 그림자
  const scrollRef = useRef<HTMLDivElement>(null);
  const [footerLifted, setFooterLifted] = useState(false);
  const updateFooterShadow = () => {
    const el = scrollRef.current;
    if (!el) return;
    setFooterLifted(el.scrollTop + el.clientHeight < el.scrollHeight - 1);
  };
  useEffect(() => {
    if (!isOpen) return;
    updateFooterShadow();
    const el = scrollRef.current;
    if (!el) return;
    const ro = new ResizeObserver(updateFooterShadow);
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => ro.disconnect();
  }, [isOpen]);

  if (!isOpen) return null;

  const handleSave = () => {
    onSave({ modelId: draft.modelId, settings: draft.settings, mode: draft.mode });
    setIsDirty(false);
    doClose();
  };

  return (
    <>
      <div
        onClick={requestClose}
        aria-hidden="true"
        className="fixed inset-0 z-40 bg-black/35"
        style={{
          animation: closing
            ? `panelFadeOut ${CLOSE_MS}ms ease forwards`
            : 'panelFadeIn 160ms ease',
        }}
      />

      <div
        ref={panelRef}
        className="fixed bottom-0 right-0 top-0 z-50 flex w-[384px] max-w-[calc(100vw-16px)] flex-col border-l border-border-subtle-01 bg-layer-01 shadow-elevation-3"
        style={{
          animation: closing
            ? `panelSlideOut ${CLOSE_MS}ms cubic-bezier(0.4, 0, 1, 1) forwards`
            : 'panelSlideIn 200ms cubic-bezier(0.16, 1, 0.3, 1)',
        }}
        role="dialog"
        aria-modal="true"
        aria-label="Model settings"
        tabIndex={-1}
      >
        <div className="flex h-[64px] flex-shrink-0 items-center gap-3 border-b border-border-subtle-01 px-4">
          <div className="min-w-0">
            <h2 className="text-body-short-02 font-semibold text-text-primary">{t('modelSettings.title')}</h2>
            <p className="mt-0.5 truncate text-caption-01 text-text-helper">{currentModel || t('modelSettings.noModel')}</p>
          </div>
          <button
            onClick={requestClose}
            title={t('modelSettings.closeButton')}
            className="ml-auto flex-shrink-0 p-1.5 text-icon-secondary hover:text-icon-primary hover:bg-layer-hover-01 transition-colors rounded-md"
          >
            <Icon name="close" size={18} />
          </button>
        </div>

        {/* Content — 열릴 때마다 최신 prop으로 새로 초기화하도록 key로 remount */}
        <div ref={scrollRef} onScroll={updateFooterShadow} className="flex-1 overflow-y-auto px-4 py-4">
          <ModelPicker
            key={`${currentModel}|${modelMode}`}
            currentModel={currentModel}
            modelMode={modelMode}
            settings={settings}
            onChange={handleDraftChange}
          />
        </div>

        {/* Footer — 우측 정렬 자연폭. 취소는 조용한 텍스트 버튼, 저장만 primary로 위계를 만든다 */}
        <div
          className={`flex flex-shrink-0 items-center gap-1.5 border-t border-border-subtle-01 bg-layer-01 px-3 py-3 transition-shadow duration-200 ${
            footerLifted ? 'shadow-[0_-10px_18px_-10px_rgba(0,0,0,0.55)]' : ''
          }`}
        >
          <span className="mr-auto hidden items-center gap-1.5 text-caption-01 text-text-helper sm:flex">
            <Icon name="lock" size={12} /> {t('modelSettings.saveOnDevice')}
          </span>
          <Button
            kind="ghost"
            size="sm"
            onClick={requestClose}
          >
            {t('modelSettings.cancel')}
          </Button>
          <Button
            kind="primary"
            size="sm"
            onClick={handleSave}
            disabled={!draft.canSave}
            icon={<Icon name="check" size={14} />}
            title={draft.canSave ? undefined : draft.mode === 'local' ? t('modelSettings.saveDisabledLocal') : t('modelSettings.saveDisabledSaas')}
          >
            {t('modelSettings.save')}
          </Button>
        </div>
      </div>

      {/* 미저장 변경사항 확인 다이얼로그 */}
      <ConfirmDialog
        open={confirmOpen}
        title={t('modelSettings.confirmTitle')}
        description={t('modelSettings.confirmDescription')}
        confirmLabel={t('modelSettings.confirmLeave')}
        cancelLabel={t('modelSettings.confirmEdit')}
        tone="danger"
        onConfirm={() => {
          setConfirmOpen(false);
          doClose();
        }}
        onClose={() => setConfirmOpen(false)}
      />

      <style>{`
        @keyframes panelFadeIn { from { opacity: 0; } to { opacity: 1; } }
        @keyframes panelFadeOut { from { opacity: 1; } to { opacity: 0; } }
        @keyframes panelSlideIn { from { transform: translateX(100%); } to { transform: translateX(0); } }
        @keyframes panelSlideOut { from { transform: translateX(0); } to { transform: translateX(100%); } }
      `}</style>
    </>
  );
};

// Made with Bob
