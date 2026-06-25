import React, { useState } from 'react';
import { Button } from '../ui';
import { dialogBackdropCls } from '../ui/surfaces';

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
      <div className="relative w-[440px] max-w-full bg-layer-01 border border-border-subtle-01 rounded-2xl z-[61] shadow-overlay animate-scale-in overflow-hidden">
        {/* Header */}
        <div className="flex items-center px-5 py-4 border-b border-border-subtle-01">
          <div className="flex-1">
            <div className="text-heading-03 font-semibold text-text-primary">
              스킬 후원
            </div>
            <div className="text-caption-01 text-text-helper">{skillName}</div>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 flex items-center justify-center hover:bg-layer-hover-01 transition-colors"
            aria-label="Close"
          >
            <svg width="16" height="16" viewBox="0 0 32 32" fill="currentColor">
              <path d="M24 9.4L22.6 8 16 14.6 9.4 8 8 9.4l6.6 6.6L8 22.6 9.4 24l6.6-6.6 6.6 6.6 1.4-1.4-6.6-6.6L24 9.4z"/>
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="p-5">
          <div className="text-body-short-01 text-text-secondary mb-1.5">
            제작자{' '}
            <strong className="text-text-primary">{authorName}</strong>에게 IBM 블루 포인트를 보냅니다
          </div>

          {/* Balance */}
          <div className="flex items-center gap-1.5 text-caption-01 text-text-helper mb-4">
            <svg width="14" height="14" viewBox="0 0 32 32" fill="currentColor">
              <path d="M26 6H6a2 2 0 00-2 2v16a2 2 0 002 2h20a2 2 0 002-2V8a2 2 0 00-2-2zm0 2v4H6V8zM6 24V14h20v10z"/>
            </svg>
            내 잔액 {balance.toLocaleString()} BP
          </div>

          {/* Amount selection */}
          <div className="grid grid-cols-4 gap-px bg-border-subtle-01 mb-5">
            {presetAmounts.map((preset) => (
              <button
                key={preset}
                onClick={() => setAmount(preset)}
                className={`
                  py-3.5 text-center text-body-short-01 font-semibold font-mono transition-colors
                  ${amount === preset
                    ? 'bg-interactive text-white'
                    : 'bg-background text-text-primary hover:bg-layer-hover-01'
                  }
                `}
              >
                {preset.toLocaleString()}
              </button>
            ))}
          </div>

          {/* Sponsor button */}
          <Button
            kind="primary"
            size="lg"
            onClick={handleSponsor}
            className="w-full"
          >
            <svg width="16" height="16" viewBox="0 0 32 32" fill="currentColor" className="mr-2">
              <path d="M22.45 6h-7.88l-1.94-4.68A1 1 0 0011.7 1H3.53a1 1 0 00-.93.63L.1 8.31a1 1 0 00.93 1.37h4.67l-2.5 6.54a1 1 0 00.55 1.3 1 1 0 001.3-.55l2.5-6.54h4.67l-2.5 6.54a1 1 0 00.55 1.3 1 1 0 001.3-.55l2.5-6.54h4.67l-2.5 6.54a1 1 0 00.55 1.3 1 1 0 001.3-.55l2.5-6.54h4.67a1 1 0 00.93-1.37l-2.5-6.68A1 1 0 0022.45 6z"/>
            </svg>
            {amount.toLocaleString()} BP 후원
          </Button>
        </div>
      </div>
    </div>
  );
};

// Made with Bob