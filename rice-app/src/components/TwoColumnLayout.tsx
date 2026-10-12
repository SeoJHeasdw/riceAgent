import React from 'react';

/**
 * TwoColumnLayout - Rail + Main content
 * Simple two-column layout for marketplace and settings screens
 */

export interface TwoColumnLayoutProps {
  children: React.ReactNode;
}

export const TwoColumnLayout: React.FC<TwoColumnLayoutProps> = ({ children }) => {
  return (
    <div className="flex flex-1 min-w-0 overflow-hidden">
      {children}
    </div>
  );
};
