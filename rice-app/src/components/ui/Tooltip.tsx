import React from 'react';

/**
 * Tooltip — 아이콘 전용 버튼의 hover 라벨.
 *
 * 네이티브 `title` 속성은 브라우저 기본 지연·스타일이라 반응이 느리고 다크 UI와
 * 이질적이다(IconRail이 이미 자체 버전을 손으로 구현해 둔 것과 같은 이유).
 * 포지셔닝 라이브러리 없이 CSS group-hover만으로 앵커 기준 4방향에 배치한다.
 */

const SIDE_CLASS: Record<'top' | 'bottom' | 'left' | 'right', string> = {
  top: 'bottom-full left-1/2 -translate-x-1/2 mb-1.5',
  bottom: 'top-full left-1/2 -translate-x-1/2 mt-1.5',
  left: 'right-full top-1/2 -translate-y-1/2 mr-1.5',
  right: 'left-full top-1/2 -translate-y-1/2 ml-1.5',
};

export interface TooltipProps {
  label: string;
  side?: 'top' | 'bottom' | 'left' | 'right';
  /** true거나 label이 빈 문자열이면 children만 그대로 렌더 */
  disabled?: boolean;
  children: React.ReactElement;
}

export const Tooltip: React.FC<TooltipProps> = ({ label, side = 'top', disabled, children }) => {
  if (disabled || !label) return children;
  return (
    <span className="relative inline-flex group/tooltip">
      {children}
      <span
        role="tooltip"
        className={`absolute z-50 px-2 py-1 bg-layer-02 text-text-primary text-caption-01 font-medium
          whitespace-nowrap rounded-md border border-border-subtle-01 shadow-elevation-2
          opacity-0 scale-95 pointer-events-none
          group-hover/tooltip:opacity-100 group-hover/tooltip:scale-100
          group-focus-within/tooltip:opacity-100 group-focus-within/tooltip:scale-100
          transition-all duration-150 delay-100
          ${SIDE_CLASS[side]}`}
      >
        {label}
      </span>
    </span>
  );
};

// Made with Bob
