import React, { useCallback, useMemo, useRef, useState } from 'react';
import { ProgressToast, type ProgressTone } from '../../components/carbon/ProgressToast';
import { NotificationToast, type NotificationVariant } from '../../components/carbon/NotificationToast';
import { ToastContext, type ToastApi } from './ToastContext';

/**
 * ToastProvider — 진행률(ProgressToast)과 알림(NotificationToast)을 한 큐로 관리하고
 * 화면 하단 중앙에 스택으로 렌더한다. 명령형 API는 useToast()(./ToastContext)로 제공한다.
 */

let nextId = 0;
const makeId = () => ++nextId;

/** 동시에 화면에 떠 있을 수 있는 토스트 상한 — 초과 시 가장 오래된 것부터 밀어낸다. */
const MAX_VISIBLE_TOASTS = 3;

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

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [items, setItems] = useState<ToastItem[]>([]);

  const remove = useCallback((id: number) => {
    setItems((prev) => prev.filter((it) => it.id !== id));
  }, []);

  const patchProgress = useCallback((id: number, patch: Partial<ProgressItem>) => {
    setItems((prev) =>
      prev.map((it) => (it.id === id && it.kind === 'progress' ? { ...it, ...patch } : it)),
    );
  }, []);

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

// Made with Bob
