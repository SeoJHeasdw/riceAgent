import React from 'react';

/** Compact RICE shield shared by language selection and the cinematic shell. */
export const BrandMark: React.FC<{ size?: number; className?: string }> = ({ size = 28, className = '' }) => (
  <svg width={size} height={size} viewBox="0 0 32 32" fill="currentColor" className={className} aria-hidden>
    <path d="M16 2 4 8v9c0 7 5.2 11.4 12 13 6.8-1.6 12-6 12-13V8L16 2Zm0 2.2 10 5V17c0 5.7-4.1 9.4-10 10.9C10.1 26.4 6 22.7 6 17V9.2l10-5Z" />
    <path d="m14.2 19.6-3.4-3.4 1.4-1.4 2 2 5-5 1.4 1.4-6.4 6.4Z" />
  </svg>
);

