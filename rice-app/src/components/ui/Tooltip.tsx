import React, { useId } from 'react';

/**
 * Tooltip — 아이콘 전용 버튼의 hover 라벨.
 *
 * 네이티브 `title` 속성은 브라우저 기본 지연·스타일이라 반응이 느리고 다크 UI와
 * 이질적이다(IconRail이 이미 자체 버전을 손으로 구현해 둔 것과 같은 이유).
 * 포지셔닝 라이브러리 없이 CSS group-hover만으로 앵커 기준 4방향에 배치한다.
 */

const SIDE_CLASS: Record<'top' | 'bottom' | 'bottom-start' | 'bottom-end' | 'left' | 'right', string> = {
  top: 'bottom-full left-1/2 -translate-x-1/2 mb-1.5',
  bottom: 'top-full left-1/2 -translate-x-1/2 mt-1.5',
  'bottom-start': 'top-full left-0 mt-1.5',
  'bottom-end': 'top-full right-0 mt-1.5',
  left: 'right-full top-1/2 -translate-y-1/2 mr-1.5',
  right: 'left-full top-1/2 -translate-y-1/2 ml-1.5',
};

export interface TooltipProps {
  label: string;
  side?: 'top' | 'bottom' | 'bottom-start' | 'bottom-end' | 'left' | 'right';
  /**
   * 감싸는 span에 붙일 클래스.
   *
   * 이 span이 flex 줄의 항목이 되면 줄어들 수 있는지를 이것이 정한다. 긴 경로처럼
   * 끊을 곳이 없는 글은 span의 최소 너비가 글 전체라, `min-w-0` 없이는 안쪽 버튼의
   * 말줄임이 걸리지 않고 옆 글자를 밀어낸다.
   */
  className?: string;
  /** true거나 label이 빈 문자열이면 children만 그대로 렌더 */
  disabled?: boolean;
  children: React.ReactElement;
}

export const Tooltip: React.FC<TooltipProps> = ({ label, side = 'top', disabled, className, children }) => {
  const id = useId();
  if (disabled || !label) return children;
  const child = children as React.ReactElement<{ 'aria-describedby'?: string }>;
  const describedBy = [child.props['aria-describedby'], id].filter(Boolean).join(' ');
  return (
    <span className={`relative inline-flex group/tooltip electron-no-drag${className ? ` ${className}` : ''}`}>
      {React.cloneElement(child, { 'aria-describedby': describedBy })}
      <span
        id={id}
        role="tooltip"
        className={`absolute z-50 px-2 py-1 bg-layer-02 text-text-primary text-caption-01 font-medium
          whitespace-nowrap rounded-md border border-border-subtle-01 shadow-elevation-2
          opacity-0 scale-95 pointer-events-none
          group-hover/tooltip:opacity-100 group-hover/tooltip:scale-100
          group-focus-visible/tooltip:opacity-100 group-focus-visible/tooltip:scale-100
          transition-all duration-150 delay-100
          ${SIDE_CLASS[side]}`}
      >
        {label}
      </span>
    </span>
  );
};

// Made with Bob
