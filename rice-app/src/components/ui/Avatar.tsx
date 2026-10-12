import React from 'react';

/**
 * Avatar component — RICE color tokens with modernized shape
 * - User/agent avatar component (rounded squircle)
 * - Multiple sizes
 */

export interface AvatarProps {
  /** Avatar type */
  type?: 'user' | 'agent' | 'skill' | 'tool';
  /** Avatar size */
  size?: 'sm' | 'md' | 'lg' | 'xl';
  /** Custom initials or text */
  label?: string;
  /** Custom background color */
  bgColor?: string;
  /** Additional CSS classes */
  className?: string;
}

export const Avatar: React.FC<AvatarProps> = ({
  type = 'user',
  size = 'md',
  label,
  bgColor,
  className = '',
}) => {
  const sizeStyles = {
    sm: 'w-6 h-6 text-caption-01',
    md: 'w-7 h-7 text-label-01',
    lg: 'w-10 h-10 text-body-short-02',
    xl: 'w-12 h-12 text-heading-02',
  };

  const typeStyles = {
    user: 'bg-gray-70 text-white',
    agent: 'bg-interactive text-white',
    skill: 'bg-purple-70 text-white',
    tool: 'bg-teal-70 text-white',
  };

  const defaultLabels = {
    user: 'U',
    agent: 'IA',
    skill: 'S',
    tool: 'T',
  };

  const displayLabel = label || defaultLabels[type];
  const bgStyle = bgColor ? { backgroundColor: bgColor } : {};

  return (
    <div
      className={`flex items-center justify-center rounded-lg font-mono font-semibold flex-shrink-0 ${sizeStyles[size]} ${!bgColor ? typeStyles[type] : ''} ${className}`}
      style={bgStyle}
      aria-label={`${type} avatar`}
    >
      {displayLabel}
    </div>
  );
};
