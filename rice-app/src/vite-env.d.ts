/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 렌더러 단독 dev에서 직결할 로컬 런타임 URL (예: http://localhost:8080). */
  readonly VITE_RICE_RUNTIME_URL?: string;
  /** 런타임이 bearer 토큰을 요구할 때만 설정 (보통 dev는 비움). */
  readonly VITE_RICE_RUNTIME_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
