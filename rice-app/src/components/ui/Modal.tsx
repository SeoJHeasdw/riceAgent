import React, { useEffect } from 'react';
import { dialogBackdropCls, dialogSurfaceCls } from './surfaces';

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
  size?: 'sm' | 'md' | 'lg';
  /** Footer content (typically buttons) */
  footer?: React.ReactNode;
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
  className = '',
}) => {
  // Close on Escape key
  useEffect(() => {
    if (!open) return;
    
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [open, onClose]);

  // Prevent body scroll when modal is open
  useEffect(() => {
    if (open) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [open]);

  if (!open) return null;

  const sizeStyles = {
    sm: 'max-w-md',
    md: 'max-w-2xl',
    lg: 'max-w-4xl',
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className={dialogBackdropCls} onClick={onClose} aria-hidden="true" />

      {/* Modal */}
      <div
        className={`relative ${dialogSurfaceCls} w-full mx-4 flex flex-col max-h-[90vh] ${sizeStyles[size]} ${className}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? 'modal-title' : undefined}
      >
        {/* Header */}
        {title && (
          <div className="flex items-center justify-between px-4 py-4 border-b border-border-subtle-01">
            <h2 id="modal-title" className="text-heading-03 text-text-primary font-sans font-semibold">
              {title}
            </h2>
            <button
              onClick={onClose}
              className="flex items-center justify-center w-8 h-8 rounded-md text-icon-primary hover:bg-layer-hover-01 transition-colors"
              aria-label="Close modal"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                <path d="M12 4.7L11.3 4 8 7.3 4.7 4 4 4.7 7.3 8 4 11.3l.7.7L8 8.7l3.3 3.3.7-.7L8.7 8z" />
              </svg>
            </button>
          </div>
        )}

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-4 py-5">
          {children}
        </div>

        {/* Footer */}
        {footer && (
          <div className="flex items-center justify-end gap-2 px-4 py-4 border-t border-border-subtle-01">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
};

// Made with Bob
