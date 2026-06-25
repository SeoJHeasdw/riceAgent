import React from 'react';
import { IconRail, IconRailItem } from './carbon';

/**
 * Layout - Main app layout: titlebar + icon rail + content
 * Titlebar reserves space for macOS traffic lights (hiddenInset)
 * and acts as the window drag region.
 */

const isElectron = navigator.userAgent.toLowerCase().includes('electron');

export interface LayoutProps {
  children: React.ReactNode;
  railItems: IconRailItem[];
  activeRailId: string;
  onNavigate: (id: string, path: string) => void;
}

export const Layout: React.FC<LayoutProps> = ({
  children,
  railItems,
  activeRailId,
  onNavigate,
}) => {
  return (
    <div className="flex flex-col h-screen w-screen bg-background overflow-hidden">
      {/* Titlebar / window drag region */}
      <header
        className={`
          h-11 flex-shrink-0 flex items-center gap-2.5
          bg-layer-01 border-b border-border-subtle-01 electron-drag
          ${isElectron ? 'pl-[84px]' : 'pl-4'} pr-4
        `}
      >
        <div
          className="
            flex items-center justify-center w-6 h-6 rounded-md
            bg-gradient-to-br from-[#0f62fe] to-[#8a3ffc]
            text-white font-mono font-bold text-caption-01
            shadow-elevation-1 select-none
          "
          aria-hidden="true"
        >
          R
        </div>
        <span className="text-body-short-01 font-semibold text-text-primary select-none">
          RICE
        </span>
        <span className="text-caption-01 text-text-helper select-none">
          Runtime Intelligence & Continuous Evolution
        </span>
      </header>

      {/* Rail + content */}
      <div className="flex flex-1 min-h-0">
        <IconRail
          items={railItems}
          activeId={activeRailId}
          onNavigate={onNavigate}
        />
        <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
          {children}
        </main>
      </div>
    </div>
  );
};

// Made with Bob
