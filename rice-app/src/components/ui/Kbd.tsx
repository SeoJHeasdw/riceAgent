import React from 'react';

/** Kbd — small keyboard-hint badge (shortcut labels in popovers/palettes). */
export const Kbd: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <kbd className="px-1 py-px rounded bg-layer-01 border border-border-subtle-01 font-mono text-label-micro text-text-secondary">
    {children}
  </kbd>
);

// Made with Bob
