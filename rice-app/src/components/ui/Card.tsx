import React from 'react';

/**
 * Card component — RICE color tokens with modernized shape
 * - Flat by default: border가 형태를 잡고, 그림자는 overlay 표면 전용
 * - Uses layer backgrounds
 */

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Card content */
  children: React.ReactNode;
  /** Card padding size */
  padding?: 'none' | 'sm' | 'md' | 'lg';
  /** Use layer-01 background (default is layer-01) */
  layer?: '01' | '02' | '03';
  /** Show border */
  bordered?: boolean;
  /** Additional CSS classes */
  className?: string;
}

export const Card = React.forwardRef<HTMLDivElement, CardProps>(
  (
    {
      children,
      padding = 'md',
      layer = '01',
      bordered = true,
      className = '',
      ...props
    },
    ref
  ) => {
    const baseStyles = 'rounded-xl transition-colors';

    const paddingStyles = {
      none: '',
      sm: 'p-3',
      md: 'p-4',
      lg: 'p-6',
    };

    const layerStyles = {
      '01': 'bg-layer-01',
      '02': 'bg-layer-02',
      '03': 'bg-layer-03',
    };

    const borderStyle = bordered ? 'border border-border-subtle-01' : '';

    return (
      <div
        ref={ref}
        className={`${baseStyles} ${layerStyles[layer]} ${paddingStyles[padding]} ${borderStyle} ${className}`}
        {...props}
      >
        {children}
      </div>
    );
  }
);

Card.displayName = 'Card';
