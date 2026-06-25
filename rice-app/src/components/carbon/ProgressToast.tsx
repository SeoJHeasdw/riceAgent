import React, { useEffect, useRef, useState } from 'react';
import { floatingSurfaceCls } from '../ui/surfaces';

/**
 * ProgressToast — 진행 중인 작업의 상태를 하단에 띄우는 토스트.
 *
 * 설치(다운로드/설치 진행률)뿐 아니라 연결 테스트처럼 진척률이 없는 작업까지
 * 아우르도록 일반화한 프리미티브다. `progress`가 없으면 indeterminate 바를 흘려
 * "확인 중" 상태를 자연스럽게 표현한다.
 *
 * 등장(slide-up) → linger → 퇴장(slide-down) 모션과 그 타이밍을 스스로 소유한다.
 * 부모는 작업이 끝나면 tone 을 'success'/'error' 로 바꾸기만 하면 되고, 토스트가
 * 잠깐 머문 뒤 알아서 퇴장 애니메이션을 재생하고 `onDismiss`로 언마운트를 알린다.
 */

export type ProgressTone = 'progress' | 'success' | 'error';

export interface ProgressToastProps {
  /** 굵게 표시되는 주 라벨. 예: "설치 중", "연결 확인 중", "설치 완료" */
  label: string;
  /** 보조 설명. 예: 아티팩트 이름, 엔드포인트 URL */
  sublabel?: string;
  /** 색/아이콘을 결정. 'progress' 동안엔 자동 퇴장하지 않는다. */
  tone: ProgressTone;
  /** 0–100. 주어지면 결정형 바, 없으면 indeterminate 바. */
  progress?: number;
  /** 값이 바뀌면(초기값 제외) 즉시 퇴장 애니메이션을 시작 — 외부에서 닫을 때. */
  dismissSignal?: number;
  /** 퇴장 애니메이션이 끝난 뒤(또는 닫기 누른 뒤) 호출. */
  onDismiss?: () => void;
}

const Spinner = () => (
  <svg width="18" height="18" viewBox="0 0 32 32" fill="none" className="animate-spin">
    <circle cx="16" cy="16" r="13" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
    <path d="M16 3a13 13 0 0 1 13 13" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
  </svg>
);

const CheckIcon = () => (
  <svg width="18" height="18" viewBox="0 0 32 32" fill="currentColor">
    <path d="M16 2C8.3 2 2 8.3 2 16s6.3 14 14 14 14-6.3 14-14S23.7 2 16 2zm-2 19.6l-5-5 1.4-1.4 3.6 3.6 7.6-7.6 1.4 1.4-9 9z" />
  </svg>
);

const ErrorIcon = () => (
  <svg width="18" height="18" viewBox="0 0 32 32" fill="currentColor">
    <path d="M16 2C8.3 2 2 8.3 2 16s6.3 14 14 14 14-6.3 14-14S23.7 2 16 2zm-1.1 6h2.2v11h-2.2V8zM16 25c-.8 0-1.5-.7-1.5-1.5S15.2 22 16 22s1.5.7 1.5 1.5S16.8 25 16 25z" />
  </svg>
);

const toneConfig: Record<
  ProgressTone,
  { icon: React.ReactNode; accent: string; ring: string; glow: string; bar: string }
> = {
  progress: {
    icon: <Spinner />,
    accent: 'text-interactive',
    ring: 'ring-interactive/30',
    glow: 'bg-interactive/15',
    bar: 'bg-interactive',
  },
  success: {
    icon: <CheckIcon />,
    accent: 'text-support-success',
    ring: 'ring-support-success/30',
    glow: 'bg-support-success/15',
    bar: 'bg-support-success',
  },
  error: {
    icon: <ErrorIcon />,
    accent: 'text-support-error',
    ring: 'ring-support-error/30',
    glow: 'bg-support-error/15',
    bar: 'bg-support-error',
  },
};

export const ProgressToast: React.FC<ProgressToastProps> = ({
  label,
  sublabel,
  tone,
  progress,
  dismissSignal = 0,
  onDismiss,
}) => {
  const [leaving, setLeaving] = useState(false);
  const inProgress = tone === 'progress';
  const config = toneConfig[tone];

  // 작업이 끝나면(tone !== 'progress') 잠깐 머문 뒤 퇴장 애니메이션을 재생한다.
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => {
    if (inProgress) return;
    timer.current = setTimeout(() => setLeaving(true), 1600);
    return () => clearTimeout(timer.current);
  }, [inProgress]);

  // 외부 닫기 요청(dismissSignal 변화) → 퇴장 애니메이션 시작. 초기값(0)은 무시.
  useEffect(() => {
    if (dismissSignal > 0) setLeaving(true);
  }, [dismissSignal]);

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
          min-w-[340px] max-w-[460px]
          rounded-2xl px-4 py-3.5
          ring-1 ${config.ring}
          ${floatingSurfaceCls}
        `}
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

        {/* Text + progress */}
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2">
            <span className="text-body-short-01 font-semibold text-text-primary truncate">{label}</span>
            {inProgress && progress !== undefined && (
              <span className={`ml-auto text-caption-01 font-mono ${config.accent}`}>{progress}%</span>
            )}
          </div>
          {sublabel && <div className="text-caption-01 text-text-helper truncate mt-0.5">{sublabel}</div>}

          {inProgress &&
            (progress !== undefined ? (
              // 결정형 바
              <div className="mt-2 h-1.5 w-full rounded-full bg-layer-01 overflow-hidden">
                <div
                  className={`h-full rounded-full ${config.bar} transition-[width] duration-300 ease-out`}
                  style={{ width: `${progress}%` }}
                />
              </div>
            ) : (
              // indeterminate 바 — 진척률 없는 작업(연결 테스트 등)
              <div className="mt-2 h-1.5 w-full rounded-full bg-layer-01 overflow-hidden">
                <div className={`h-full w-2/5 rounded-full ${config.bar} animate-progress-indeterminate`} />
              </div>
            ))}
        </div>

        {/* Dismiss / cancel */}
        <button
          onClick={() => setLeaving(true)}
          className="flex-shrink-0 w-7 h-7 flex items-center justify-center rounded-lg text-icon-secondary hover:bg-layer-hover-01 hover:text-text-primary transition-colors"
          aria-label={inProgress ? '취소' : '닫기'}
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
