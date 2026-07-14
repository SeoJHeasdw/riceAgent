import React, { useId } from 'react';

export interface CompanionCloudIconProps {
  className?: string;
  /** Includes the decorative dots used by the interactive starter companion. */
  withBurst?: boolean;
}

/**
 * RICE's cloud companion mark. The cropped static viewBox keeps the cloud
 * legible at avatar and titlebar sizes without needing an enclosing badge.
 */
export const CompanionCloudIcon: React.FC<CompanionCloudIconProps> = ({
  className = '',
  withBurst = false,
}) => {
  const gradientId = `companion-cloud-${useId().replace(/:/g, '')}`;

  return (
    <svg
      viewBox={withBurst ? '0 0 64 56' : '5 9 52 38'}
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id={gradientId} x1="10" y1="8" x2="52" y2="46" gradientUnits="userSpaceOnUse">
          <stop stopColor="#78a9ff" />
          <stop offset="0.55" stopColor="#a56eff" />
          <stop offset="1" stopColor="#3ddbd9" />
        </linearGradient>
      </defs>
      {withBurst && (
        <g className="starter-companion__burst" fill={`url(#${gradientId})`}>
          <circle cx="7" cy="27" r="2" />
          <circle cx="14" cy="8" r="1.7" />
          <circle cx="52" cy="11" r="1.8" />
          <circle cx="58" cy="32" r="1.6" />
          <circle cx="48" cy="49" r="1.5" />
          <circle cx="15" cy="47" r="1.4" />
        </g>
      )}
      <g className={withBurst ? 'starter-companion__puff' : undefined}>
        <path
          d="M17.5 43.5c-6 0-10.8-4.5-10.8-10.1 0-4.9 3.6-9 8.5-9.9C17 16.2 23.4 11 31 11c8.2 0 14.9 6 15.6 13.8 5.2.7 9.2 5 9.2 10.1 0 5.8-5 10.5-11.2 10.5H17.5Z"
          fill={`url(#${gradientId})`}
          fillOpacity="0.11"
          stroke={`url(#${gradientId})`}
          strokeWidth="2.2"
          strokeLinejoin="round"
        />
        <circle cx="26" cy="31" r="1.5" fill="currentColor" />
        <circle cx="37.5" cy="31" r="1.5" fill="currentColor" />
        <path
          d="M27.5 36c2.6 2 5.8 2 8.4 0"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
      </g>
    </svg>
  );
};

