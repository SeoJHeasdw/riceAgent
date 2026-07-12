/**
 * i18next-parser 설정 — 코드의 t('...') 호출을 스캔해 로케일 JSON을 동기화한다.
 *
 * 실행: npm run i18n:extract
 *   - 코드에서 새로 등장한 키를 각 로케일 JSON에 추가한다.
 *   - ko(원본)엔 키 이름을 임시값으로, en(번역본)엔 빈 문자열로 넣어 "미번역"을 드러낸다.
 *   - keepRemoved: true 라 기존 키를 함부로 지우지 않는다(수동 정리 권장).
 *
 * 주의: errors 네임스페이스는 백엔드 에러코드를 그대로 반영하는 수동 카탈로그다.
 *       apiError.ts 가 `errors:${code}` 로 동적 조회하므로 파서가 추출하지 못한다.
 *       백엔드에 에러코드를 추가하면 errors.json 도 손으로 맞춰준다.
 */
export default {
  locales: ['ko', 'en'],
  defaultNamespace: 'common',

  // t('ns:key') 의 ns 구분자 ':' + 키 계층 구분자 '.'
  namespaceSeparator: ':',
  keySeparator: '.',

  input: ['src/**/*.{ts,tsx}'],
  output: 'src/locales/$LOCALE/$NAMESPACE.json',

  sort: true,
  keepRemoved: true,        // 코드에서 안 보이는 키(동적/시드)를 자동 삭제하지 않음
  createOldCatalogs: false,
  indentation: 2,

  // 새 키 기본값: 원본(ko)엔 키를 임시로 노출, 번역본은 비워 미번역을 드러냄
  defaultValue: (locale, _namespace, key) => (locale === 'ko' ? key : ''),
};
