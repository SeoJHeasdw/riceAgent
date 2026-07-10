import React from 'react';

/**
 * Tag component — Carbon g100 색상 + rounded-full pill 형태
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

  // Carbon g100 (dark) tag palette: dark backgrounds, light text
  const typeStyles = {
    blue: 'bg-interactive-active text-blue-30',
    green: 'bg-green-80 text-green-30',
    purple: 'bg-purple-80 text-purple-30',
    gray: 'bg-layer-02 text-text-secondary border border-border-subtle-02',
    teal: 'bg-teal-80 text-teal-30',
    red: 'bg-danger-active text-red-30',
    yellow: 'bg-yellow-60 text-yellow-20',
    outline: 'bg-transparent text-text-primary border border-border-strong-01',
  };

  const sizeStyles = {
    sm: 'h-5 px-2 text-caption-01',
    md: 'h-6 px-2 text-label-01',
  };

  return (
    <span className={`${baseStyles} ${typeStyles[type]} ${sizeStyles[size]} ${className}`} {...props}>
      {dot && (
        <span
          className="w-1.5 h-1.5 rounded-full"
          style={{
            backgroundColor: type === 'green' ? '#42be65' : type === 'blue' ? '#4589ff' : 'currentColor',
          }}
        />
      )}
      {children}
    </span>
  );
};

// Made with Bob
