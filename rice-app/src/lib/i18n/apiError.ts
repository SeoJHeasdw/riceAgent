import i18n from './index';

/**
 * 백엔드 에러 봉투 → 지역화된 사용자 메시지.
 *
 * 런타임은 자연어 문장을 만들지 않는다. 안정적인 `code`(+ 보간용 `params`)만 내려주고,
 * 실제 표시 문구는 여기서 `errors` 네임스페이스로 매핑한다.
 *
 * 백엔드 응답 형태(RiceError 핸들러):
 *   { success: false, error: "<dev fallback>", code: "workspace.path_not_absolute", params: { detail: "..." } }
 *
 * 사용 예(토스트/캐치 블록 — 명령형):
 *   const res = await api.saveWorkspace(path);
 *   if (!res.success) toast.error(apiErrorMessage(res));
 */
export interface BackendError {
  /** 안정적 에러 코드. errors 네임스페이스의 키와 1:1 매핑된다. */
  code?: string;
  /** 개발자용 폴백 메시지(표시는 하지 않는 게 원칙, 코드 미매핑 시 최후 폴백). */
  error?: string;
  /** 문구 보간용 파라미터. 예: { detail, baseUrl, name } */
  params?: Record<string, unknown>;
}

// 코드는 런타임에서 오는 동적 문자열이라 정적 키 타입에 없다 → 시그니처를 넓혀서 캐스팅.
const translate = i18n.t.bind(i18n) as unknown as (
  key: string,
  options?: Record<string, unknown>,
) => string;

/**
 * 에러 봉투를 지역화 문자열로. 매핑 우선순위:
 *   1) errors:<code>  (있으면 params 로 보간)
 *   2) err.error      (code가 없거나 카탈로그에 없는 미분류 에러 — 최후 폴백, 미번역 dev 문구)
 *   3) errors:unknown (그마저도 없으면 완전 제네릭 문구)
 *
 * 주의: i18next의 `{ defaultValue }`는 대상 키가 **존재하지 않을 때만** 쓰인다.
 * `errors:unknown`은 항상 존재하는 키라 `t('errors:unknown', { defaultValue: ... })`로는
 * 절대 defaultValue가 나오지 않는다 — 그래서 폴백을 아래처럼 직접 분기한다.
 */
export function apiErrorMessage(err: BackendError | null | undefined): string {
  const code = err?.code;
  const params = err?.params ?? {};

  if (code && i18n.exists(`errors:${code}`)) {
    return translate(`errors:${code}`, params);
  }
  if (err?.error) {
    return err.error;
  }
  return translate('errors:unknown');
}
