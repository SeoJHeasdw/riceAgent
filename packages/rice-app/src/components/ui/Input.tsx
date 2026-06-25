import React from 'react';

/**
 * Input component — Carbon color tokens with modernized shape
 * - Boxed style with rounded corners
 * - Focus ring highlight
 */

export interface InputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size'> {
  /** Input label */
  label?: string;
  /** Helper text below input */
  helperText?: string;
  /** Error message */
  error?: string;
  /** Input size */
  size?: 'sm' | 'md' | 'lg';
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  (
    {
      label,
      helperText,
      error,
      size = 'md',
      className = '',
      disabled,
      ...props
    },
    ref
  ) => {
    const sizeStyles = {
      sm: 'h-8 text-body-short-01',
      md: 'h-10 text-body-short-02',
      lg: 'h-12 text-body-short-02',
    };

    const inputStyles = `
      w-full px-4 bg-field-01 text-text-primary font-sans
      border border-border-strong-01 rounded-lg
      outline-none transition-colors
      focus:border-focus focus:ring-1 focus:ring-focus
      disabled:opacity-50 disabled:cursor-not-allowed
      placeholder:text-text-placeholder
      ${sizeStyles[size]}
      ${error ? 'border-support-error focus:border-support-error' : ''}
      ${className}
    `;

    return (
      <div className="flex flex-col gap-1">
        {label && (
          <label className="text-label-01 text-text-secondary font-sans font-normal">
            {label}
          </label>
        )}
        <input
          ref={ref}
          disabled={disabled}
          className={inputStyles.trim().replace(/\s+/g, ' ')}
          {...props}
        />
        {error && (
          <span className="text-caption-01 text-support-error">{error}</span>
        )}
        {!error && helperText && (
          <span className="text-helper-text-01 text-text-helper">{helperText}</span>
        )}
      </div>
    );
  }
);

Input.displayName = 'Input';

// Made with Bob
