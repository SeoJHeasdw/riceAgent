import React, { useId, useRef } from 'react';
import { createPortal } from 'react-dom';
import { dialogBackdropCls, dialogSurfaceCls } from './surfaces';
import { useDialogA11y } from '../../hooks/useDialogA11y';

/**
 * Modal component — Carbon color tokens with modernized shape
 * - Modal dialog with blurred backdrop overlay
 * - Rounded corners with deep overlay shadow
 *
 * backdrop·표면 스타일은 surfaces.ts 의 공용 토큰을 사용해 ConfirmDialog 등
 * 다른 팝업과 한 출처에서 통제된다.
 */

export interface ModalProps {
  /** Modal open state */
  open: boolean;
  /** Close handler */
  onClose: () => void;
  /** Modal title */
  title?: string;
  /** Modal content */
  children: React.ReactNode;
  /** Modal size */
  size?: 'sm' | 'md' | 'lg' | 'xl';
  /** Footer content (typically buttons) */
  footer?: React.ReactNode;
  /** 컴팩트 푸터가 기본 — 두꺼운 py-4 푸터가 필요한 예외 표면만 false로 */
  compactFooter?: boolean;
  /** Additional CSS classes */
  className?: string;
}

export const Modal: React.FC<ModalProps> = ({
  open,
  onClose,
  title,
  children,
  size = 'md',
  footer,
  compactFooter = true,
  className = '',
}) => {
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useDialogA11y({ open, dialogRef, onClose });

  if (!open) return null;

  const sizeStyles = {
    sm: 'max-w-md',
    md: 'max-w-2xl',
    lg: 'max-w-4xl',
    xl: 'max-w-6xl',
  };

  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-center justify-center">
      {/* Backdrop */}
      <div className={dialogBackdropCls} onClick={onClose} aria-hidden="true" />

      {/* Modal */}
      <div
        ref={dialogRef}
        className={`relative ${dialogSurfaceCls} w-full mx-4 flex flex-col max-h-[90vh] ${sizeStyles[size]} ${className}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        aria-label={title ? undefined : '대화상자'}
        tabIndex={-1}
      >
        {/* Header */}
        {title && (
          <div className="flex items-center justify-between px-5 py-3 border-b border-border-subtle-01">
            <h2 id={titleId} className="text-heading-01 text-text-primary font-sans font-semibold">
              {title}
            </h2>
            <button
              onClick={onClose}
              className="flex items-center justify-center w-7 h-7 rounded-lg text-icon-secondary hover:text-icon-primary hover:bg-layer-hover-01 transition-colors"
              aria-label="대화상자 닫기"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M18 6 6 18M6 6l12 12" />
              </svg>
            </button>
          </div>
        )}

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {children}
        </div>

        {/* Footer */}
        {footer && (
          <div
            className={`flex items-center justify-end gap-2 px-5 border-t border-border-subtle-01 ${
              compactFooter ? 'py-2.5' : 'py-4'
            }`}
          >
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
};

// Made with Bob
