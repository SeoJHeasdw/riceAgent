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
  const isChatScreen = screenKey === 'chat';

  return (
    <div className="flex flex-col h-screen w-screen bg-background overflow-hidden">
      {/* Titlebar / window drag region — 아이콘 레일과 함께 brand shell을 이룬다.
          긴 태그라인은 window chrome이 아니라 Home·Settings About의 몫. */}
      {!isChatScreen && (
        <header
          className={`
            h-11 flex-shrink-0 flex items-center gap-2.5
            bg-shell border-b border-shell-border electron-drag
            ${isElectron ? 'pl-[84px]' : 'pl-4'} pr-4
          `}
        >
          <div
            className="
              flex items-center justify-center w-6 h-6 rounded-md
              bg-gradient-to-br from-blue-60 to-purple-60
              text-white font-mono font-bold text-caption-01
              select-none
            "
            aria-hidden="true"
          >
            R
          </div>
          <span className="text-body-short-01 font-semibold text-white select-none">
            RICE
          </span>
        </header>
      )}

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
