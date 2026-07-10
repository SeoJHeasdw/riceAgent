import React, { useId } from 'react';

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
  /** Accent color when checked. */
  tone?: 'blue' | 'green' | 'purple';
  /** Required for icon-only/bare switches. */
  'aria-label'?: string;
}

const toneStyles = {
  blue: { track: 'bg-interactive', check: 'text-interactive' },
  green: { track: 'bg-support-success', check: 'text-support-success' },
  purple: { track: 'bg-purple-60', check: 'text-purple-60' },
} as const;

export const Toggle: React.FC<ToggleProps> = ({
  checked,
  onChange,
  disabled = false,
  label,
  description,
  tone = 'blue',
  'aria-label': ariaLabel,
}) => {
  const accent = toneStyles[tone];
  const id = useId();
  const labelId = label ? `${id}-label` : undefined;
  const descriptionId = description ? `${id}-description` : undefined;
  const switchEl = (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label ? undefined : ariaLabel ?? '상태 전환'}
      aria-labelledby={labelId}
      aria-describedby={descriptionId}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`
        relative inline-flex items-center flex-shrink-0 h-6 w-10 rounded-full
        transition-colors duration-150
        ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}
        ${checked
          ? accent.track
          : 'bg-layer-03 ring-1 ring-inset ring-border-strong-01'}
      `}
    >
      <span
        className={`
          absolute top-1/2 -translate-y-1/2 h-5 w-5 rounded-full bg-white shadow-elevation-1
          flex items-center justify-center transition-[left] duration-150
          ${checked ? 'left-[18px]' : 'left-0.5'}
        `}
      >
        {checked ? (
          <svg width="10" height="10" viewBox="0 0 32 32" fill="none" className={accent.check}>
            <path d="M13 21.5l-6-6 1.8-1.8 4.2 4.2 9.4-9.4L24.2 10z" fill="currentColor" />
          </svg>
        ) : (
          <span className="w-2 h-2 rounded-full border border-text-helper" />
        )}
      </span>
    </button>
  );

  if (!label) return switchEl;

  return (
    <div className={`flex items-center gap-3 ${disabled ? 'opacity-50' : ''}`}>
      <div className="flex-1 min-w-0">
        <div id={labelId} className="text-body-short-02 text-text-primary">{label}</div>
        {description && (
          <div id={descriptionId} className="text-caption-01 text-text-helper">{description}</div>
        )}
      </div>
      {switchEl}
    </div>
  );
};

// Made with Bob
