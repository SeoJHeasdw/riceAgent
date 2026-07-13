import React, { useId, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from './Button';
import { Icon } from './Icon';
import type { IconName } from './Icon';
import { dialogBackdropCls, dialogSurfaceCls } from './surfaces';
import { useDialogA11y } from '../../hooks/useDialogA11y';

/**
 * ConfirmDialog — 재사용 확인 팝업.
 * "정말 진행할까요?"류의 되돌리기 어려운 작업(복원·삭제 등) 앞에 한 번 더 묻는다.
 * 차가운 단일 표면: danger여도 색은 확인 버튼과 작은 아이콘 틴트에만 남긴다.
 * 취소는 조용한 텍스트 버튼·확인만 채운 버튼으로 위계를 만든다.
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
  /** 다른 오버레이 위에 표시할 때 사용할 stacking class */
  stackingClassName?: string;
}

export const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel,
  tone = 'primary',
  confirmIcon,
  onConfirm,
  onClose,
  stackingClassName = 'z-[60]',
}) => {
  const { t } = useTranslation('common');
  const dialogRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  useDialogA11y({ open, dialogRef, onClose, initialFocusRef: cancelRef });

  if (!open) return null;

  return (
    <div className={`fixed inset-0 ${stackingClassName} flex items-center justify-center`}>
      {/* backdrop */}
      <div className={dialogBackdropCls} onClick={onClose} aria-hidden="true" />

      {/* dialog */}
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className={`relative w-full max-w-[420px] mx-4 overflow-hidden ${dialogSurfaceCls}`}
      >
        <div className="px-5 pt-4 pb-1">
          <div className="flex items-start gap-3">
            {tone === 'danger' && (
              <span className="flex-shrink-0 w-6 h-6 rounded-full bg-support-error/10 flex items-center justify-center">
                <Icon name="warning" size={13} className="text-support-error" />
              </span>
            )}
            <div className="flex-1 min-w-0 pt-0.5">
              <h2 id={titleId} className="text-body-short-01 font-semibold text-text-primary">{title}</h2>
              {description && (
                <div id={descriptionId} className="mt-1.5 text-body-short-01 leading-5 text-text-secondary">{description}</div>
              )}
            </div>
          </div>
        </div>

        {/* actions — 같은 표면 위 우측 정렬. 취소는 텍스트 버튼, 확인만 채워 위계를 만든다 */}
        <div className="flex items-center justify-end gap-1.5 px-4 py-3">
          <button
            ref={cancelRef}
            onClick={onClose}
            className="h-8 px-3 rounded-lg text-body-short-01 font-medium text-text-secondary hover:text-text-primary hover:bg-layer-hover-01 transition-colors"
          >
            {cancelLabel ?? t('cancel')}
          </button>
          <Button
            kind={tone}
            size="xs"
            className="min-w-[72px]"
            icon={confirmIcon ? <Icon name={confirmIcon} size={14} /> : undefined}
            onClick={onConfirm}
          >
            {confirmLabel ?? t('confirm')}
          </Button>
        </div>
      </div>
    </div>
  );
};

// Made with Bob
