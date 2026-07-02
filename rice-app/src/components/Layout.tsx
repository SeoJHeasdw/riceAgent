import React from 'react';
import { useLocation } from 'react-router-dom';
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
  // 화면(최상위 경로 세그먼트) 전환 시에만 새로 mount해 크로스페이드를 재생한다.
  // 같은 화면 안의 하위 경로 변화(예: /connectors/:id)는 key가 그대로라 remount되지 않는다.
  const location = useLocation();
  const screenKey = location.pathname.split('/')[1] || 'home';

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
          <div key={screenKey} className="flex-1 flex flex-col min-h-0 animate-fade-in">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
};

// Made with Bob
