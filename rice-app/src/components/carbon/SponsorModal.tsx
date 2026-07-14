import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '../ui';
import { dialogBackdropCls } from '../ui/surfaces';
import { useI18n } from '../../lib/i18n/I18nContext';
import { formatNumber } from '../../lib/i18n/formatters';

/**
 * SponsorModal - Modal for sponsoring skill authors with Blue Points
 */

export interface SponsorModalProps {
  isOpen: boolean;
  onClose: () => void;
  skillName: string;
  authorName: string;
  onSponsor: (amount: number) => void;
  balance?: number;
}

export const SponsorModal: React.FC<SponsorModalProps> = ({
  isOpen,
  onClose,
  skillName,
  authorName,
  onSponsor,
  balance = 12480,
}) => {
  const { t } = useTranslation('skills');
  const { language } = useI18n();
  const [amount, setAmount] = useState(500);
  const presetAmounts = [200, 500, 1000, 2500];

  const handleSponsor = () => {
    onSponsor(amount);
    onClose();
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
      {/* Overlay */}
      <div onClick={onClose} className={dialogBackdropCls} />

      {/* Modal */}
      <div className="relative w-[440px] max-w-full bg-layer-01 border border-border-subtle-01 rounded-xl z-[61] shadow-elevation-3 animate-scale-in overflow-hidden">
        {/* Header */}
        <div className="flex items-center px-5 py-4 border-b border-border-subtle-01">
          <div className="flex-1">
            <div className="text-heading-02 font-semibold text-text-primary">
              {t('sponsor.modalTitle')}
            </div>
            <div className="text-caption-01 text-text-helper">{skillName}</div>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-icon-secondary hover:text-icon-primary hover:bg-layer-hover-01 transition-colors"
            aria-label="Close"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="p-5">
          <div className="text-body-short-01 text-text-secondary mb-1.5">
            {t('sponsor.descriptionPrefix')}{' '}
            <strong className="text-text-primary">{authorName}</strong>
            {t('sponsor.descriptionSuffix') ? <>{' '}{t('sponsor.descriptionSuffix')}</> : null}
          </div>

          {/* Balance */}
          <div className="flex items-center gap-1.5 text-caption-01 text-text-helper mb-4">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <rect x="2" y="5" width="20" height="14" rx="2" />
              <path d="M2 10h20" />
            </svg>
            {t('sponsor.balanceLabel', { balance: formatNumber(balance, language) })}
          </div>

          {/* Amount selection — 세그먼트 그리드 (앱 공통 segmented control 문법) */}
          <div className="grid grid-cols-4 gap-0.5 p-0.5 bg-field-01 border border-border-subtle-01 rounded-lg mb-4">
            {presetAmounts.map((preset) => (
              <button
                key={preset}
                onClick={() => setAmount(preset)}
                aria-pressed={amount === preset}
                className={`h-7 px-2 text-center text-caption-01 font-medium font-mono rounded-md transition-colors duration-150 ${
                  amount === preset
                    ? 'bg-interactive text-text-on-color'
                    : 'bg-transparent text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary'
                }`}
              >
                {formatNumber(preset, language)}
              </button>
            ))}
          </div>

          {/* Sponsor button */}
          <Button
            kind="primary"
            size="md"
            onClick={handleSponsor}
            className="w-full"
            icon={
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" />
              </svg>
            }
          >
            {t('sponsor.sponsorBtn', { amount: formatNumber(amount, language) })}
          </Button>
        </div>
      </div>
    </div>
  );
};

// Made with Bob
