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
  // ChatScreen은 AppShell에서 항상 mount한 채 가시성만 바꾼다. 여기서 경로를
  // key로 삼으면 숨겨진 ChatScreen까지 remount되어 진행 중 SSE 상태가 유실된다.
  // 화면별 진입 애니메이션은 AppShell의 가시 wrapper가 담당한다.
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
          topInset={isChatScreen && isElectron}
        />
        <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
          <div className="flex-1 flex flex-col min-h-0">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
};

// Made with Bob
