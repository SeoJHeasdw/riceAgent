import React from 'react';

/**
 * Toggle - iOS-style toggle switch.
 * With `label`, renders a full row (text + switch). Without `label`,
 * renders just the bare switch — handy inside custom rows.
 */

export interface ToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  label?: string;
  description?: string;
  /** Accent color when checked. 'blue' (default) or 'green'. */
  tone?: 'blue' | 'green';
}

const toneStyles = {
  blue: { track: 'bg-interactive', check: 'text-interactive' },
  green: { track: 'bg-support-success', check: 'text-support-success' },
} as const;

export const Toggle: React.FC<ToggleProps> = ({
  checked,
  onChange,
  disabled = false,
  label,
  description,
  tone = 'blue',
}) => {
  const accent = toneStyles[tone];
  const switchEl = (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`
        relative inline-flex items-center flex-shrink-0 h-[22px] w-[40px] rounded-full
        transition-colors duration-300 ease-out
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-layer-01 focus-visible:ring-focus
        ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}
        ${checked
          ? `${accent.track} shadow-[inset_0_1px_2px_rgba(0,0,0,0.25)]`
          : 'bg-layer-03 ring-1 ring-inset ring-border-strong-01'}
      `}
    >
      <span
        className={`
          absolute top-1/2 -translate-y-1/2 h-[18px] w-[18px] rounded-full bg-white shadow-elevation-1
          flex items-center justify-center transition-all duration-300 ease-out
          ${checked ? 'left-[20px]' : 'left-0.5'}
        `}
      >
        {checked ? (
          <svg width="10" height="10" viewBox="0 0 32 32" fill="none" className={accent.check}>
            <path d="M13 21.5l-6-6 1.8-1.8 4.2 4.2 9.4-9.4L24.2 10z" fill="currentColor" />
          </svg>
        ) : (
          <span className="w-[7px] h-[7px] rounded-full border-[1.5px] border-text-helper" />
        )}
      </span>
    </button>
  );

  if (!label) return switchEl;

  return (
    <label
      className={`flex items-center gap-3 ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
    >
      <div className="flex-1">
        <div className="text-body-short-02 text-text-primary">{label}</div>
        {description && (
          <div className="text-caption-01 text-text-helper">{description}</div>
        )}
      </div>
      {switchEl}
    </label>
  );
};

// Made with Bob
