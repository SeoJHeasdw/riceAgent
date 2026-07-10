import React, { useState } from 'react';
import { dialogBackdropCls, dialogSurfaceCls } from '../ui/surfaces';
import { Button } from '../ui';
import { useI18n } from '../../lib/i18n/I18nContext';
import type { Language } from '../../lib/i18n/translations';

/**
 * LanguageSelectDialog — 온보딩 진입 전에 표시하는 언어 선택 소형 팝업.
 *
 * OK를 누르면 선택 언어를 I18nContext에 저장하고 onConfirm을 호출해
 * 온보딩 플로우를 열도록 부모에게 알린다.
 */

export interface LanguageSelectDialogProps {
  open: boolean;
  onConfirm: () => void;
}

const LANGUAGES: { value: Language; label: string; native: string }[] = [
  { value: 'en', label: 'English', native: 'English' },
  { value: 'ko', label: 'Korean', native: '한국어' },
];

export const LanguageSelectDialog: React.FC<LanguageSelectDialogProps> = ({ open, onConfirm }) => {
  const { language, setLanguage } = useI18n();
  const [selected, setSelected] = useState<Language>(language);

  if (!open) return null;

  const handleConfirm = () => {
    setLanguage(selected);
    onConfirm();
  };

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center">
      {/* Backdrop */}
      <div className={dialogBackdropCls} aria-hidden="true" />

      {/* Dialog */}
      <div
        className={`relative ${dialogSurfaceCls} w-full max-w-xs mx-4 flex flex-col`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="lang-dialog-title"
      >
        {/* Header */}
        <div className="px-5 pt-5 pb-3">
          <h2
            id="lang-dialog-title"
            className="text-heading-02 font-semibold text-text-primary text-center"
          >
            Select Language · 언어 선택
          </h2>
          <p className="mt-1 text-caption-01 text-text-helper text-center">
            Choose your preferred language to continue.
            <br />
            계속하려면 언어를 선택하세요.
          </p>
        </div>

        {/* Language options */}
        <div className="px-5 pb-4 flex flex-col gap-2">
          {LANGUAGES.map((lang) => {
            const isSelected = selected === lang.value;
            return (
              <button
                key={lang.value}
                onClick={() => setSelected(lang.value)}
                className={`flex items-center gap-3 w-full px-4 py-3 rounded-lg border transition-colors text-left ${
                  isSelected
                    ? 'border-interactive bg-interactive/10 text-text-primary'
                    : 'border-border-subtle-01 bg-layer-02 text-text-secondary hover:bg-layer-hover-02'
                }`}
              >
                {/* Radio indicator */}
                <span
                  className={`flex-shrink-0 w-4 h-4 rounded-full border-2 flex items-center justify-center transition-colors ${
                    isSelected ? 'border-interactive' : 'border-border-strong-01'
                  }`}
                >
                  {isSelected && (
                    <span className="w-2 h-2 rounded-full bg-interactive" />
                  )}
                </span>
                <span className="text-body-short-01 font-medium">{lang.label}</span>
                <span className="ml-auto text-caption-01 text-text-helper">{lang.native}</span>
              </button>
            );
          })}
        </div>

        {/* Footer */}
        <div className="px-5 pb-5 flex justify-end">
          <Button kind="primary" size="sm" onClick={handleConfirm}>
            OK
          </Button>
        </div>
      </div>
    </div>
  );
};

// Made with Bob
