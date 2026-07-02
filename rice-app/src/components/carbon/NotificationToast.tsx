import React, { useEffect, useRef, useState } from 'react';
import { floatingSurfaceCls } from '../ui/surfaces';

/**
 * NotificationToast - Lightweight status toast that shares the glassmorphism
 * language of ProgressToast (밝은 elevated 글래스 표면, glowing icon badge,
 * accent ring). Owns its own linger + exit animation; the parent only
 * provides `onDismiss` to unmount. 위치/스택은 ToastViewport 가 담당하므로
 * 자체 fixed 포지셔닝은 갖지 않는다.
 */

export type NotificationVariant = 'success' | 'info' | 'warning' | 'error';

export interface NotificationToastProps {
  message: string;
  variant?: NotificationVariant;
  /** How long to linger before auto-dismissing (ms). */
  duration?: number;
  /** Called after the exit animation finishes (or close is pressed). */
  onDismiss?: () => void;
}

/** 18px 스트로크 상태 글리프 — 앱 공통 라인 아이콘 언어(1.8 stroke)와 통일 */
const VariantIcon: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <svg
    width="18"
    height="18"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden
  >
    {children}
  </svg>
);

const variantConfig: Record<
  NotificationVariant,
  { icon: React.ReactNode; accent: string; ring: string; glow: string }
> = {
  success: {
    icon: (
      <VariantIcon>
        <circle cx="12" cy="12" r="9" />
        <path d="m8.5 12.5 2.5 2.5 4.5-5" />
      </VariantIcon>
    ),
    accent: 'text-support-success',
    ring: 'ring-support-success/30',
    glow: 'bg-support-success/15',
  },
  info: {
    icon: (
      <VariantIcon>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8h.01M12 12v4" />
      </VariantIcon>
    ),
    accent: 'text-interactive',
    ring: 'ring-interactive/30',
    glow: 'bg-interactive/15',
  },
  warning: {
    icon: (
      <VariantIcon>
        <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
        <path d="M12 9v4M12 17h.01" />
      </VariantIcon>
    ),
    accent: 'text-support-warning',
    ring: 'ring-support-warning/30',
    glow: 'bg-support-warning/15',
  },
  error: {
    icon: (
      <VariantIcon>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8v5M12 16.5h.01" />
      </VariantIcon>
    ),
    accent: 'text-support-error',
    ring: 'ring-support-error/30',
    glow: 'bg-support-error/15',
  },
};

export const NotificationToast: React.FC<NotificationToastProps> = ({
  message,
  variant = 'success',
  duration = 2600,
  onDismiss,
}) => {
  const [leaving, setLeaving] = useState(false);
  const config = variantConfig[variant];

  // Linger, then play the exit animation.
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => {
    timer.current = setTimeout(() => setLeaving(true), duration);
    return () => clearTimeout(timer.current);
  }, [duration]);

  const handleAnimationEnd = () => {
    if (leaving) onDismiss?.();
  };

  return (
    <div
      onAnimationEnd={handleAnimationEnd}
      className={leaving ? 'animate-toast-out' : 'animate-toast-in'}
    >
      <div
        className={`
          relative flex items-center gap-3.5
          min-w-[300px] max-w-[460px]
          rounded-2xl px-4 py-3
          ring-1 ${config.ring}
          ${floatingSurfaceCls}
        `}
        role="alert"
      >
        {/* Icon badge */}
        <div className="relative flex-shrink-0">
          <div className={`absolute inset-0 rounded-xl blur-md ${config.glow}`} aria-hidden="true" />
          <div
            className={`relative w-9 h-9 flex items-center justify-center rounded-xl bg-layer-03 ${config.accent}`}
          >
            {config.icon}
          </div>
        </div>

        {/* Message */}
        <p className="flex-1 min-w-0 text-body-short-01 text-text-primary">{message}</p>

        {/* Dismiss */}
        <button
          onClick={() => setLeaving(true)}
          className="flex-shrink-0 w-7 h-7 flex items-center justify-center rounded-lg text-icon-secondary hover:bg-layer-hover-01 hover:text-text-primary transition-colors"
          aria-label="닫기"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
        </button>
      </div>
    </div>
  );
};

// Made with Bob
