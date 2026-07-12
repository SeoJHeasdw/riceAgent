// react-i18next 타입 증강: ko 카탈로그(원본)를 키 스키마의 기준으로 삼는다.
// 이 덕분에 t('settings:appearance.title') 같은 호출이 자동완성 + 오타 시 컴파일 에러.
// ko 를 기준으로 두는 이유: 원본은 항상 완전하고, en 은 번역 중 일부가 비어 있을 수 있어서.
import 'i18next';

import type common from '../../locales/ko/common.json';
import type settings from '../../locales/ko/settings.json';
import type chat from '../../locales/ko/chat.json';
import type skills from '../../locales/ko/skills.json';
import type plugins from '../../locales/ko/plugins.json';
import type errors from '../../locales/ko/errors.json';

declare module 'i18next' {
  interface CustomTypeOptions {
    defaultNS: 'common';
    resources: {
      common: typeof common;
      settings: typeof settings;
      chat: typeof chat;
      skills: typeof skills;
      plugins: typeof plugins;
      errors: typeof errors;
    };
  }
}
