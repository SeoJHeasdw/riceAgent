import React from 'react';

/**
 * Tag component following IBM Carbon design principles
 * - Status/category tags with color variants
 * - 0px border radius (flat design)
 * - Multiple color variants: blue, green, purple, gray, teal, red, yellow
 */

export interface TagProps {
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
}) => {
  const baseStyles = 'inline-flex items-center gap-1.5 font-sans font-normal rounded-full whitespace-nowrap';

  // Carbon g100 (dark) tag palette: dark backgrounds, light text
  const typeStyles = {
    blue: 'bg-[#002d9c] text-[#a6c8ff]',
    green: 'bg-[#044317] text-[#6fdc8c]',
    purple: 'bg-[#491d8b] text-[#d4bbff]',
    gray: 'bg-layer-02 text-text-secondary border border-border-subtle-01',
    teal: 'bg-[#004144] text-[#3ddbd9]',
    red: 'bg-[#750e13] text-[#ffb3b8]',
    yellow: 'bg-[#684e00] text-[#fddc69]',
    outline: 'bg-transparent text-text-primary border border-border-strong-01',
  };

  const sizeStyles = {
    sm: 'h-5 px-2 text-caption-01',
    md: 'h-6 px-2 text-label-01',
  };

  return (
    <span className={`${baseStyles} ${typeStyles[type]} ${sizeStyles[size]} ${className}`}>
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
