import { createContext, useCallback, useContext, useEffect, useMemo, useState, ReactNode } from 'react';
import i18n, { LANGUAGE_STORAGE_KEY, SUPPORTED_LANGUAGES, type Language } from './index';

/**
 * 언어 상태 컨텍스트 (얇은 래퍼).
 *
 * 문자열 번역은 이 훅이 아니라 react-i18next 의 useTranslation() 을 쓴다:
 *     const { t } = useTranslation('settings');
 *     t('appearance.title')
 *
 * 이 훅은 "현재 언어 읽기 / 언어 바꾸기"만 담당한다(설정 화면, 언어 선택 다이얼로그 등).
 */
interface I18nContextType {
  language: Language;
  setLanguage: (lang: Language) => void;
}

const I18nContext = createContext<I18nContextType | undefined>(undefined);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>(() => i18n.language as Language);

  // i18next 인스턴스가 진실의 원천. languageChanged 이벤트로 React 상태를 동기화한다.
  useEffect(() => {
    const onChange = (lng: string) => {
      setLanguageState(lng as Language);
      document.documentElement.lang = lng;
    };
    document.documentElement.lang = i18n.language;
    i18n.on('languageChanged', onChange);
    return () => {
      i18n.off('languageChanged', onChange);
    };
  }, []);

  const setLanguage = useCallback((lang: Language) => {
    if (!SUPPORTED_LANGUAGES.includes(lang)) return;
    try {
      localStorage.setItem(LANGUAGE_STORAGE_KEY, lang);
    } catch {
      // 저장 실패는 무시 — 세션 동안은 정상 동작
    }
    void i18n.changeLanguage(lang);
  }, []);

  const value = useMemo(() => ({ language, setLanguage }), [language, setLanguage]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components -- context hook lives with its provider
export function useI18n() {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error('useI18n must be used within an I18nProvider');
  }
  return context;
}
