import React, { useEffect, useState } from 'react';
import { useI18n } from '../../lib/i18n/I18nContext';
import type { Language } from '../../lib/i18n';
import { BrandMark } from './onboarding/primitives';

export interface LanguageSelectDialogProps {
  open: boolean;
  onConfirm: () => void;
}

const LANGUAGES: Array<{ value: Language; code: string; label: string; caption: string }> = [
  { value: 'ko', code: 'KO', label: '한국어', caption: 'Korean' },
  { value: 'en', code: 'EN', label: 'English', caption: '영어' },
];

const ArrowIcon = () => (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </svg>
);

export const LanguageSelectDialog: React.FC<LanguageSelectDialogProps> = ({ open, onConfirm }) => {
  const { language, setLanguage } = useI18n();
  const [selected, setSelected] = useState<Language>(language);

  useEffect(() => {
    if (open) setSelected(language);
  }, [language, open]);

  if (!open) return null;

  const handleConfirm = () => {
    setLanguage(selected);
    onConfirm();
  };

  return (
    <div className="cinematic-language" role="dialog" aria-modal="true" aria-labelledby="language-title">
      <div className="cinematic-language__field" aria-hidden="true" />
      <div className="cinematic-language__orbit" aria-hidden="true"><i /><i /><i /></div>
      <section className="language-dock">
        <div className="language-dock__brand">
          <span><BrandMark size={19} /></span>
          <strong>RICE</strong>
          <small>FIRST CONTACT</small>
        </div>
        <div className="language-dock__intro">
          <span>WELCOME / 환영합니다</span>
          <h1 id="language-title">Choose your language</h1>
          <p>RICE와 대화할 언어를 선택하세요.<br />Select the language you want to use with RICE.</p>
        </div>
        <div className="language-dock__options">
          {LANGUAGES.map((item) => {
            const active = selected === item.value;
            return (
              <button
                key={item.value}
                type="button"
                className={active ? 'is-active' : ''}
                onClick={() => setSelected(item.value)}
                aria-pressed={active}
              >
                <span className="language-dock__code">{item.code}</span>
                <span className="language-dock__label"><strong>{item.label}</strong><small>{item.caption}</small></span>
                <span className="language-dock__radio"><i /></span>
              </button>
            );
          })}
        </div>
        <button type="button" className="language-dock__continue" onClick={handleConfirm}>
          <span>{selected === 'ko' ? '한국어로 시작하기' : 'Continue in English'}</span>
          <ArrowIcon />
        </button>
        <p className="language-dock__foot">언어는 설정에서 언제든 변경할 수 있습니다 · You can change this anytime</p>
      </section>
    </div>
  );
};
