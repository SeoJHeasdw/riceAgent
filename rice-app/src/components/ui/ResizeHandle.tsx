import React from 'react';

/**
 * ResizeHandle - vertical drag handle between resizable panels
 * Invisible 8px hit area; a thin highlight bar appears on hover/drag.
 * Double-click resets the panel to its default width.
 */

export interface ResizeHandleProps {
  onPointerDown: (e: React.PointerEvent) => void;
  onDoubleClick?: () => void;
  isResizing?: boolean;
  value?: number;
  min?: number;
  max?: number;
  onResizeBy?: (delta: number) => void;
  'aria-label'?: string;
}

export const ResizeHandle: React.FC<ResizeHandleProps> = ({
  onPointerDown,
  onDoubleClick,
  isResizing = false,
  value,
  min,
  max,
  onResizeBy,
  'aria-label': ariaLabel = 'Resize panel',
}) => (
  <div
    role="separator"
    aria-orientation="vertical"
    aria-label={ariaLabel}
    aria-valuenow={value}
    aria-valuemin={min}
    aria-valuemax={max}
    tabIndex={onResizeBy ? 0 : undefined}
    onPointerDown={onPointerDown}
    onDoubleClick={onDoubleClick}
    onKeyDown={(event) => {
      if (!onResizeBy) return;
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        onResizeBy(event.shiftKey ? -32 : -8);
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        onResizeBy(event.shiftKey ? 32 : 8);
      } else if (event.key === 'Home') {
        event.preventDefault();
        onDoubleClick?.();
      }
    }}
    className="relative w-2 -mx-1 z-20 flex-shrink-0 cursor-col-resize group touch-none focus-visible:outline-offset-0"
  >
    <div
      className={`
        absolute inset-y-0 left-1/2 -translate-x-1/2 w-[3px] rounded-full
        transition-colors duration-150
        ${isResizing ? 'bg-interactive' : 'bg-transparent group-hover:bg-interactive/60 group-focus-visible:bg-interactive/70'}
      `}
    />
  </div>
);
