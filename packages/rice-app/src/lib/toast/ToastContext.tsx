import React, { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import { ProgressToast, type ProgressTone } from '../../components/carbon/ProgressToast';
import { NotificationToast, type NotificationVariant } from '../../components/carbon/NotificationToast';

/**
 * 전역 토스트 시스템 — 진행률(ProgressToast)과 알림(NotificationToast)을
 * 한 큐로 관리하고 화면 하단 중앙에 스택으로 렌더한다.
 *
 * 그동안 각 화면이 installProgress/toast state 를 직접 들고 컴포넌트를 렌더해야
 * 했다. 이제 useToast() 명령형 API 한 줄로 어디서든 토스트를 띄울 수 있어
 * 연결 테스트처럼 진행 피드백이 필요한 새 사용처를 손쉽게 늘릴 수 있다.
 *
 *   const toast = useToast();
 *   toast.notify({ message: '저장했어요', variant: 'success' });
 *
 *   const job = toast.progress({ label: '연결 확인 중', sublabel: endpoint }); // 진척률 없으면 indeterminate
 *   job.update({ progress: 40 });        // 진행률 갱신
 *   job.done('success', '연결 성공');     // 완료/실패로 전환 → 잠깐 머문 뒤 자동 퇴장
 */

let nextId = 0;
const makeId = () => ++nextId;

/** 동시에 화면에 떠 있을 수 있는 토스트 상한 — 초과 시 가장 오래된 것부터 밀어낸다. */
const MAX_VISIBLE_TOASTS = 3;

/** progress() 가 돌려주는 핸들 — 작업 진행에 따라 토스트를 구동한다. */
export interface ProgressHandle {
  /** label/sublabel/tone/progress 부분 갱신. */
  update: (patch: Partial<Pick<ProgressItem, 'label' | 'sublabel' | 'tone' | 'progress'>>) => void;
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

interface ProgressItem {
  kind: 'progress';
  id: number;
  label: string;
  sublabel?: string;
  tone: ProgressTone;
  progress?: number;
  dismissSignal: number;
}

interface NotificationItem {
  kind: 'notification';
  id: number;
  message: string;
  variant: NotificationVariant;
  duration?: number;
}

type ToastItem = ProgressItem | NotificationItem;

const ToastContext = createContext<ToastApi | null>(null);

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [items, setItems] = useState<ToastItem[]>([]);

  const remove = useCallback((id: number) => {
    setItems((prev) => prev.filter((it) => it.id !== id));
  }, []);

  const patchProgress = useCallback(
    (id: number, patch: Partial<ProgressItem>) => {
      setItems((prev) =>
        prev.map((it) => (it.id === id && it.kind === 'progress' ? { ...it, ...patch } : it)),
      );
    },
    [],
  );

  // api 는 안정적인 참조여야 하므로(useEffect 의존성 등) ref + useMemo 로 고정.
  const removeRef = useRef(remove);
  removeRef.current = remove;
  const patchRef = useRef(patchProgress);
  patchRef.current = patchProgress;

  const api = useMemo<ToastApi>(
    () => ({
      notify: ({ message, variant = 'success', duration }) => {
        const id = makeId();
        setItems((prev) =>
          [...prev, { kind: 'notification', id, message, variant, duration } as ToastItem].slice(
            -MAX_VISIBLE_TOASTS,
          ),
        );
      },
      progress: ({ label, sublabel, tone = 'progress', progress }) => {
        const id = makeId();
        setItems((prev) =>
          [
            ...prev,
            { kind: 'progress', id, label, sublabel, tone, progress, dismissSignal: 0 } as ToastItem,
          ].slice(-MAX_VISIBLE_TOASTS),
        );
        return {
          update: (patch) => patchRef.current(id, patch),
          done: (doneTone, doneLabel) =>
            patchRef.current(id, {
              tone: doneTone,
              ...(doneLabel ? { label: doneLabel } : {}),
              progress: undefined,
            }),
          dismiss: () => patchRef.current(id, { dismissSignal: Date.now() }),
        };
      },
    }),
    [],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <ToastViewport items={items} onDismiss={(id) => removeRef.current(id)} />
    </ToastContext.Provider>
  );
};

/** 화면 하단 중앙에 토스트를 세로로 쌓는 뷰포트. */
const ToastViewport: React.FC<{ items: ToastItem[]; onDismiss: (id: number) => void }> = ({
  items,
  onDismiss,
}) => {
  if (items.length === 0) return null;
  return (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[70] flex flex-col items-center gap-2.5 pointer-events-none">
      {items.map((it) => (
        <div key={it.id} className="pointer-events-auto">
          {it.kind === 'progress' ? (
            <ProgressToast
              label={it.label}
              sublabel={it.sublabel}
              tone={it.tone}
              progress={it.progress}
              dismissSignal={it.dismissSignal}
              onDismiss={() => onDismiss(it.id)}
            />
          ) : (
            <NotificationToast
              message={it.message}
              variant={it.variant}
              duration={it.duration}
              onDismiss={() => onDismiss(it.id)}
            />
          )}
        </div>
      ))}
    </div>
  );
};

export const useToast = (): ToastApi => {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within <ToastProvider>');
  return ctx;
};

// Made with Bob
