import { createContext, useContext } from 'react';
import type { ProgressTone } from '../../components/carbon/ProgressToast';
import type { NotificationVariant } from '../../components/carbon/NotificationToast';

/**
 * 전역 토스트 시스템의 컨텍스트 + 소비 훅(useToast).
 *
 * Provider 컴포넌트는 ./ToastProvider 로 분리되어 있다(fast-refresh 규칙: 한 파일은
 * 컴포넌트만 export). 사용처는 이 파일에서 useToast 만 가져다 쓴다.
 *
 *   const toast = useToast();
 *   toast.notify({ message: '저장했어요', variant: 'success' });
 *
 *   const job = toast.progress({ label: '연결 확인 중', sublabel: endpoint }); // 진척률 없으면 indeterminate
 *   job.update({ progress: 40 });        // 진행률 갱신
 *   job.done('success', '연결 성공');     // 완료/실패로 전환 → 잠깐 머문 뒤 자동 퇴장
 */

/** progress() 가 돌려주는 핸들 — 작업 진행에 따라 토스트를 구동한다. */
export interface ProgressHandle {
  /** label/sublabel/tone/progress 부분 갱신. */
  update: (patch: { label?: string; sublabel?: string; tone?: ProgressTone; progress?: number }) => void;
  /** 작업 종료 — tone 을 성공/실패로 바꾸면 토스트가 머문 뒤 자동 퇴장한다. */
  done: (tone: Exclude<ProgressTone, 'progress'>, label?: string) => void;
  /** 즉시 퇴장 애니메이션을 시작해 닫는다. */
  dismiss: () => void;
}

export interface ToastApi {
  notify: (opts: { message: string; variant?: NotificationVariant; duration?: number }) => void;
  progress: (opts: {
    label: string;
    sublabel?: string;
    tone?: ProgressTone;
    /** 생략하면 indeterminate(진척률 없음) 바로 표시. */
    progress?: number;
  }) => ProgressHandle;
}

export const ToastContext = createContext<ToastApi | null>(null);

export const useToast = (): ToastApi => {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within <ToastProvider>');
  return ctx;
};

// Made with Bob
