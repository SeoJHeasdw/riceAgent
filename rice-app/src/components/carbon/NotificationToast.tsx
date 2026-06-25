import React, { useEffect, useRef, useState } from 'react';
import { floatingSurfaceCls } from '../ui/surfaces';

/**
 * NotificationToast - Lightweight status toast that shares the glassmorphism
 * language of ProgressToast (밝은 elevated 글래스 표면, glowing icon badge,
 * accent ring). Owns its own linger + exit animation; the parent only
 * provides `onDismiss` to unmount. 위치/스택은 ToastViewport 가 담당하므로
 * 자체 fixed 포지셔닝은 갖지 않는다.
 */

export type NotificationVariant = 'success' | 'info' | 'error';

export interface NotificationToastProps {
  message: string;
  variant?: NotificationVariant;
  /** How long to linger before auto-dismissing (ms). */
  duration?: number;
  /** Called after the exit animation finishes (or close is pressed). */
  onDismiss?: () => void;
}

const variantConfig: Record<
  NotificationVariant,
  { icon: React.ReactNode; accent: string; ring: string; glow: string }
> = {
  success: {
    icon: (
      <svg width="18" height="18" viewBox="0 0 32 32" fill="currentColor">
        <path d="M16 2C8.3 2 2 8.3 2 16s6.3 14 14 14 14-6.3 14-14S23.7 2 16 2zm-2 19.6l-5-5 1.4-1.4 3.6 3.6 7.6-7.6 1.4 1.4-9 9z" />
      </svg>
    ),
    accent: 'text-support-success',
    ring: 'ring-support-success/30',
    glow: 'bg-support-success/15',
  },
  info: {
    icon: (
      <svg width="18" height="18" viewBox="0 0 32 32" fill="currentColor">
        <path d="M16 2C8.3 2 2 8.3 2 16s6.3 14 14 14 14-6.3 14-14S23.7 2 16 2zm0 5a1.5 1.5 0 11-1.5 1.5A1.5 1.5 0 0116 7zm3 17h-6v-2h2v-6h-2v-2h4v8h2z" />
      </svg>
    ),
    accent: 'text-interactive',
    ring: 'ring-interactive/30',
    glow: 'bg-interactive/15',
  },
  error: {
    icon: (
      <svg width="18" height="18" viewBox="0 0 32 32" fill="currentColor">
        <path d="M16 2C8.3 2 2 8.3 2 16s6.3 14 14 14 14-6.3 14-14S23.7 2 16 2zm-1.1 6h2.2v11h-2.2V8zM16 25c-.8 0-1.5-.7-1.5-1.5S15.2 22 16 22s1.5.7 1.5 1.5S16.8 25 16 25z" />
      </svg>
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
          <svg width="15" height="15" viewBox="0 0 32 32" fill="currentColor">
            <path d="M24 9.4L22.6 8 16 14.6 9.4 8 8 9.4l6.6 6.6L8 22.6 9.4 24l6.6-6.6 6.6 6.6 1.4-1.4-6.6-6.6L24 9.4z" />
          </svg>
        </button>
      </div>
    </div>
  );
};

// Made with Bob
