import React from 'react';

/**
 * Button component — Carbon color tokens with modernized shape
 * - Rounded corners, subtle elevation on primary/danger
 * - Multiple variants: primary, secondary, tertiary, ghost, danger
 * - Supports icons and disabled state
 */

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** Button variant */
  kind?: 'primary' | 'secondary' | 'tertiary' | 'ghost' | 'danger';
  /** Button size. 'xs' = compact modal footer actions (below the standard 'sm') */
  size?: 'xs' | 'sm' | 'md' | 'lg';
  /** Icon element to display before text */
  icon?: React.ReactNode;
  /** Full width button */
  fullWidth?: boolean;
  /** Loading state */
  loading?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      kind = 'primary',
      size = 'md',
      icon,
      fullWidth = false,
      loading = false,
      disabled,
      className = '',
      children,
      ...props
    },
    ref
  ) => {
    const baseStyles = 'inline-flex items-center justify-center gap-2 font-sans font-semibold transition-all rounded-lg border-none outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus active:scale-[0.98] disabled:opacity-50 disabled:cursor-not-allowed disabled:active:scale-100';

    const kindStyles = {
      primary: 'bg-interactive text-text-on-color shadow-elevation-1 hover:bg-[#0353e9] hover:shadow-elevation-2 active:bg-[#002d9c]',
      secondary: 'bg-layer-01 text-text-primary border border-border-strong-01 hover:bg-layer-hover-01 active:bg-layer-active-01',
      tertiary: 'bg-transparent text-interactive border border-interactive hover:bg-layer-hover-01 active:bg-layer-active-01',
      ghost: 'bg-transparent text-link-primary hover:bg-layer-hover-01 active:bg-layer-active-01',
      danger: 'bg-support-error text-text-on-color shadow-elevation-1 hover:bg-[#ba1b23] hover:shadow-elevation-2 active:bg-[#750e13]',
    };

    const sizeStyles = {
      xs: 'h-7 px-2.5 text-caption-01',
      sm: 'h-8 px-3 text-body-short-01',
      md: 'h-10 px-4 text-body-short-02',
      lg: 'h-12 px-5 text-body-short-02',
    };

    const widthStyle = fullWidth ? 'w-full' : '';

    return (
      <button
        ref={ref}
        disabled={disabled || loading}
        className={`${baseStyles} ${kindStyles[kind]} ${sizeStyles[size]} ${widthStyle} ${className}`}
        {...props}
      >
        {loading ? (
          <span className="animate-spin">⟳</span>
        ) : icon ? (
          <span className="flex-shrink-0">{icon}</span>
        ) : null}
        {children}
      </button>
    );
  }
);

Button.displayName = 'Button';

// Made with Bob
