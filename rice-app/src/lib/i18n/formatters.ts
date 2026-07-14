/**
 * 앱 언어(Language)를 BCP-47 로케일로 변환하는 매핑과 포맷 헬퍼.
 *
 * 사용 예:
 *   const { language } = useI18n();
 *   formatNumber(1234, language)  // ko/en/ja → "1,234"
 *   formatDate(iso, language)     // ko → "2025년 1월 6일", en → "Jan 6, 2025", ja → "2025年1月6日"
 *
 * 중요: 브라우저/OS 로케일이 아니라 현재 앱 언어 기준으로 표시한다.
 */

import type { Language } from './index';

/** 앱 언어 코드 → BCP-47 로케일 태그 */
export const LOCALE_MAP: Record<Language, string> = {
  ko: 'ko-KR',
  en: 'en-US',
  ja: 'ja-JP',
};

/** 숫자를 앱 언어에 맞게 포맷한다. */
export function formatNumber(value: number, language: Language): string {
  return value.toLocaleString(LOCALE_MAP[language]);
}

/** ISO 날짜 문자열(또는 Date)을 앱 언어에 맞는 단축 날짜로 포맷한다. */
export function formatDate(
  value: string | Date | null | undefined,
  language: Language,
  options?: Intl.DateTimeFormatOptions,
): string {
  if (!value) return '';
  const date = typeof value === 'string' ? new Date(value) : value;
  if (isNaN(date.getTime())) return '';
  return date.toLocaleDateString(LOCALE_MAP[language], options ?? { year: 'numeric', month: 'short', day: 'numeric' });
}

/** ISO 날짜 문자열을 앱 언어에 맞는 날짜+시간으로 포맷한다. */
export function formatDateTime(
  value: string | Date | null | undefined,
  language: Language,
  options?: Intl.DateTimeFormatOptions,
): string {
  if (!value) return '';
  const date = typeof value === 'string' ? new Date(value) : value;
  if (isNaN(date.getTime())) return '';
  return date.toLocaleString(LOCALE_MAP[language], options ?? { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}
