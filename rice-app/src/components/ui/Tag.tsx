import React from 'react';

/**
 * Tag component — theme-aware semantic colors + rounded-full pill 형태
 * - Status/category tags with color variants
 * - Multiple color variants: blue, green, purple, gray, teal, red, yellow
 */

export interface TagProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** Tag content */
  children: React.ReactNode;
  /** Tag color variant */
  type?: 'blue' | 'green' | 'purple' | 'gray' | 'teal' | 'red' | 'yellow' | 'outline';
  /** Tag size */
  size?: 'sm' | 'md';
  /** Show dot indicator */
  dot?: boolean;
  /** Additional CSS classes */
  className?: string;
}

export const Tag: React.FC<TagProps> = ({
  children,
  type = 'gray',
  size = 'md',
  dot = false,
  className = '',
  ...props
}) => {
  const baseStyles = 'inline-flex items-center gap-1.5 font-sans font-normal rounded-full whitespace-nowrap';

  const typeStyles = {
    blue: 'rice-tag--blue',
    green: 'rice-tag--green',
    purple: 'rice-tag--purple',
    gray: 'bg-layer-02 text-text-secondary border border-border-subtle-02',
    teal: 'rice-tag--teal',
    red: 'rice-tag--red',
    yellow: 'rice-tag--yellow',
    outline: 'bg-transparent text-text-primary border border-border-strong-01',
  };

  const sizeStyles = {
    sm: 'h-5 px-2 text-caption-01',
    md: 'h-6 px-2 text-label-01',
  };

  return (
    <span className={`${baseStyles} ${typeStyles[type]} ${sizeStyles[size]} ${className}`} {...props}>
      {dot && (
        <span className="h-1.5 w-1.5 rounded-full bg-current opacity-80" />
      )}
      {children}
    </span>
  );
};
