import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import koCommon from '../../locales/ko/common.json';
import koSettings from '../../locales/ko/settings.json';
import koChat from '../../locales/ko/chat.json';
import koSkills from '../../locales/ko/skills.json';
import koPlugins from '../../locales/ko/plugins.json';
import koErrors from '../../locales/ko/errors.json';
import koConnectors from '../../locales/ko/connectors.json';
import koHome from '../../locales/ko/home.json';

import enCommon from '../../locales/en/common.json';
import enSettings from '../../locales/en/settings.json';
import enChat from '../../locales/en/chat.json';
import enSkills from '../../locales/en/skills.json';
import enPlugins from '../../locales/en/plugins.json';
import enErrors from '../../locales/en/errors.json';
import enConnectors from '../../locales/en/connectors.json';
import enHome from '../../locales/en/home.json';

/**
 * i18n 초기화 (단일 진입점).
 *
 * 언어 추가 방법: SUPPORTED_LANGUAGES 에 코드 추가 + src/locales/<코드>/ 폴더에
 * 같은 네임스페이스 JSON을 채우고, 아래 resources 에 한 줄 등록하면 끝.
 * 절차서: rice-app/docs/i18n.md
 */

/** 지원 언어 목록. ko = 원본(source of truth). */
export const SUPPORTED_LANGUAGES = ['ko', 'en'] as const;
export type Language = (typeof SUPPORTED_LANGUAGES)[number];

/** 네임스페이스 = 기능 단위 카탈로그. 새 화면은 새 네임스페이스를 권장. */
export const NAMESPACES = ['common', 'settings', 'chat', 'skills', 'plugins', 'errors', 'connectors', 'home'] as const;
export type Namespace = (typeof NAMESPACES)[number];

export const DEFAULT_NS = 'common';

/** ko를 fallback 으로 둬서, en 등 번역이 비어 있는 키는 자동으로 한국어로 대체된다. */
export const FALLBACK_LNG: Language = 'ko';

export const LANGUAGE_STORAGE_KEY = 'rice.language';

const resources = {
  ko: {
    common: koCommon,
    settings: koSettings,
    chat: koChat,
    skills: koSkills,
    plugins: koPlugins,
    errors: koErrors,
    connectors: koConnectors,
    home: koHome,
  },
  en: {
    common: enCommon,
    settings: enSettings,
    chat: enChat,
    skills: enSkills,
    plugins: enPlugins,
    errors: enErrors,
    connectors: enConnectors,
    home: enHome,
  },
} as const;

function detectInitialLanguage(): Language {
  try {
    const stored = localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (stored === 'ko' || stored === 'en') return stored;
  } catch {
    // localStorage 접근 불가 시 브라우저 언어로 폴백
  }
  return typeof navigator !== 'undefined' && navigator.language.toLowerCase().startsWith('ko')
    ? 'ko'
    : 'en';
}

if (!i18n.isInitialized) {
  // resources 를 인라인으로 주입하므로 init 은 동기 완료 → 첫 렌더 전에 사용 가능.
  void i18n.use(initReactI18next).init({
    resources,
    lng: detectInitialLanguage(),
    fallbackLng: FALLBACK_LNG,
    ns: [...NAMESPACES],
    defaultNS: DEFAULT_NS,
    supportedLngs: [...SUPPORTED_LANGUAGES],
    interpolation: { escapeValue: false }, // React 가 XSS 이스케이프를 담당하므로 끈다
    returnNull: false,
  });
}

export default i18n;
