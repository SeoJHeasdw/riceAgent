import React from 'react';

/**
 * Badge component following IBM Carbon design principles
 * - Count/status badge component
 * - 0px border radius
 * - Multiple variants
 */

export interface BadgeProps {
  /** Badge content (typically a number) */
  children: React.ReactNode;
  /** Badge variant */
  variant?: 'default' | 'info' | 'success' | 'warning' | 'error';
  /** Badge size */
  size?: 'sm' | 'md';
  /** Additional CSS classes */
  className?: string;
}

export const Badge: React.FC<BadgeProps> = ({
  children,
  variant = 'default',
  size = 'md',
  className = '',
}) => {
  const baseStyles = 'inline-flex items-center justify-center font-mono font-semibold rounded-full min-w-fit';

  const variantStyles = {
    default: 'bg-layer-02 text-text-primary',
    info: 'bg-[#0f62fe] text-white',
    success: 'bg-[#24a148] text-white',
    warning: 'bg-[#f1c21b] text-[#000000]',
    error: 'bg-[#da1e28] text-white',
  };

  const sizeStyles = {
    sm: 'h-4 px-1 text-caption-01 min-w-[16px]',
    md: 'h-5 px-1.5 text-label-01 min-w-[20px]',
  };

  return (
    <span className={`${baseStyles} ${variantStyles[variant]} ${sizeStyles[size]} ${className}`}>
      {children}
    </span>
  );
};

// Made with Bob
