import React, { useEffect } from 'react';
import { Button } from './Button';
import { Icon } from './Icon';
import type { IconName } from './Icon';
import { dialogBackdropCls, dialogSurfaceCls } from './surfaces';

/**
 * ConfirmDialog — 재사용 확인 팝업.
 * "정말 진행할까요?"류의 되돌리기 어려운 작업(복원·삭제 등) 앞에 한 번 더 묻는다.
 * 컴팩트한 높이(헤더/푸터 padding·버튼 h-7)로 Modal 보다 가볍고, 위험 작업은
 * tone="danger" 로 빨간 강조 + 경고 아이콘을 띄운다.
 */
export interface ConfirmDialogProps {
  /** 열림 상태 */
  open: boolean;
  /** 제목 */
  title: string;
  /** 본문 — 설명 텍스트나 JSX */
  description?: React.ReactNode;
  /** 확인 버튼 라벨 */
  confirmLabel?: string;
  /** 취소 버튼 라벨 */
  cancelLabel?: string;
  /** 확인 버튼 톤 — 되돌리기 어려운 작업은 'danger' */
  tone?: 'danger' | 'primary';
  /** 확인 버튼 아이콘 */
  confirmIcon?: IconName;
  /** 확인 콜백 */
  onConfirm: () => void;
  /** 닫기/취소 콜백 */
  onClose: () => void;
}

export const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  open,
  title,
  description,
  confirmLabel = '확인',
  cancelLabel = '취소',
  tone = 'primary',
  confirmIcon,
  onConfirm,
  onClose,
}) => {
  // Esc 로 닫기
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', h);
    return () => document.removeEventListener('keydown', h);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center">
      {/* backdrop */}
      <div className={dialogBackdropCls} onClick={onClose} aria-hidden="true" />

      {/* dialog */}
      <div
        role="alertdialog"
        aria-modal="true"
        className={`relative w-full max-w-sm mx-4 overflow-hidden ${dialogSurfaceCls}`}
      >
        <div className="px-5 pt-5 pb-4">
          <div className="flex items-start gap-3">
            {tone === 'danger' && (
              <span className="flex-shrink-0 w-8 h-8 rounded-full bg-support-error/15 flex items-center justify-center mt-0.5">
                <Icon name="warning" size={16} className="text-support-error" />
              </span>
            )}
            <div className="flex-1 min-w-0">
              <h2 className="text-body-short-02 font-semibold text-text-primary">{title}</h2>
              {description && <div className="mt-1.5 text-body-short-01 text-text-secondary leading-5">{description}</div>}
            </div>
          </div>
        </div>

        {/* footer — 컴팩트 높이. 취소는 배경(bg-background)과 대비되도록 layer-01 버튼으로 표시 */}
        <div className="flex items-center justify-end gap-2 px-5 py-2.5 border-t border-border-subtle-01 bg-background">
          <button
            onClick={onClose}
            className="h-7 px-3 rounded-lg text-body-short-01 font-semibold text-text-primary bg-layer-02 border border-border-strong-01 hover:bg-layer-hover-01 transition-colors"
          >
            {cancelLabel}
          </button>
          <Button
            kind={tone}
            size="sm"
            className="!h-7"
            icon={confirmIcon ? <Icon name={confirmIcon} size={14} /> : undefined}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
};

// Made with Bob
