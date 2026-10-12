import React from 'react';

/**
 * Button component — compact application actions using RICE color tokens
 * - A transparent border is always reserved so variants never change size
 * - Ghost actions are neutral; blue is reserved for primary/tertiary emphasis
 * - Destructive secondary actions use danger-ghost instead of ad-hoc overrides
 * - Supports icons and disabled state
 */

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** Button variant */
  kind?: 'primary' | 'secondary' | 'tertiary' | 'ghost' | 'danger' | 'danger-ghost';
  /** Button size. 앱 표준은 'sm'(h-7) — md/lg는 넓은 CTA 등 예외적 표면에만. */
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
      size = 'sm',
      icon,
      fullWidth = false,
      loading = false,
      disabled,
      type = 'button',
      className = '',
      children,
      ...props
    },
    ref
  ) => {
    const baseStyles = 'inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md border font-sans font-medium transition-colors duration-150 outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus disabled:cursor-not-allowed disabled:opacity-50';

    const kindStyles = {
      primary: 'border-interactive bg-interactive text-text-on-color hover:border-interactive-hover hover:bg-interactive-hover active:border-interactive-active active:bg-interactive-active',
      secondary: 'border-border-strong-01 bg-layer-01 text-text-primary hover:bg-layer-hover-01 active:bg-layer-active-01',
      tertiary: 'border-interactive bg-transparent text-interactive hover:bg-interactive/10 active:bg-interactive/15',
      ghost: 'border-transparent bg-transparent text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary active:bg-layer-active-01',
      danger: 'border-support-error bg-support-error text-text-on-color hover:border-danger-hover hover:bg-danger-hover active:border-danger-active active:bg-danger-active',
      'danger-ghost': 'border-support-error/45 bg-transparent text-text-error hover:border-support-error/75 hover:bg-support-error/10 active:bg-support-error/15',
    };

    const sizeStyles = {
      xs: 'h-6 px-2 text-caption-01',
      sm: 'h-7 px-2.5 text-caption-01',
      md: 'h-8 px-3 text-body-short-01',
      lg: 'h-9 px-4 text-body-short-01',
    };

    const widthStyle = fullWidth ? 'w-full' : '';

    return (
      <button
        ref={ref}
        type={type}
        disabled={disabled || loading}
        aria-busy={loading || undefined}
        className={`${baseStyles} ${kindStyles[kind]} ${sizeStyles[size]} ${widthStyle} ${className}`}
        {...props}
      >
        {loading ? (
          // currentColor 스피너 — primary(흰 텍스트)/secondary 어디서든 대비 유지
          <span className="w-3.5 h-3.5 rounded-full border-2 border-current border-t-transparent animate-spin flex-shrink-0" aria-hidden />
        ) : icon ? (
          <span className="flex-shrink-0">{icon}</span>
        ) : null}
        {children}
      </button>
    );
  }
);

Button.displayName = 'Button';
