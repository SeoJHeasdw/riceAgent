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
  'aria-label'?: string;
}

export const ResizeHandle: React.FC<ResizeHandleProps> = ({
  onPointerDown,
  onDoubleClick,
  isResizing = false,
  'aria-label': ariaLabel = 'Resize panel',
}) => (
  <div
    role="separator"
    aria-orientation="vertical"
    aria-label={ariaLabel}
    onPointerDown={onPointerDown}
    onDoubleClick={onDoubleClick}
    className="relative w-2 -mx-1 z-20 flex-shrink-0 cursor-col-resize group touch-none"
  >
    <div
      className={`
        absolute inset-y-0 left-1/2 -translate-x-1/2 w-[3px] rounded-full
        transition-colors duration-100
        ${isResizing ? 'bg-[#0f62fe]' : 'bg-transparent group-hover:bg-[#0f62fe]/60'}
      `}
    />
  </div>
);

// Made with Bob
