import React, { useState } from 'react';
import { Icon } from '../ui';

/**
 * SessionSidebar - Collapsible sidebar for session history
 * Features: session list, search, new session, delete actions
 */

export interface Session {
  id: string;
  title: string;
  timestamp: Date;
  messageCount: number;
  group?: string;
}

export interface SessionSidebarProps {
  sessions: Session[];
  activeSessionId: string;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onDeleteSession: (id: string) => void;
  isOpen: boolean;
  /** Panel width in px (drag-resizable from parent) */
  width?: number;
  /** Selected model name shown in the footer model button */
  modelName?: string;
  /** Current model mode, controls the footer accent + sub-label */
  modelMode?: 'saas' | 'local';
  /** Opens the model settings panel when the footer button is clicked */
  onOpenModelSettings?: () => void;
}

// Group sessions by time period
const groupSessions = (sessions: Session[]): Array<{ name: string; items: Session[] }> => {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  const lastWeek = new Date(today);
  lastWeek.setDate(lastWeek.getDate() - 7);
  const lastMonth = new Date(today);
  lastMonth.setMonth(lastMonth.getMonth() - 1);

  const groups: Array<{ name: string; items: Session[] }> = [
    { name: '오늘', items: [] },
    { name: '어제', items: [] },
    { name: '최근 7일', items: [] },
    { name: '최근 30일', items: [] },
    { name: '이전', items: [] },
  ];

  sessions.forEach((session) => {
    const sessionDate = new Date(session.timestamp);
    if (sessionDate >= today) {
      groups[0].items.push(session);
    } else if (sessionDate >= yesterday) {
      groups[1].items.push(session);
    } else if (sessionDate >= lastWeek) {
      groups[2].items.push(session);
    } else if (sessionDate >= lastMonth) {
      groups[3].items.push(session);
    } else {
      groups[4].items.push(session);
    }
  });

  return groups.filter(g => g.items.length > 0);
};

export const SessionSidebar: React.FC<SessionSidebarProps> = ({
  sessions,
  activeSessionId,
  onSelectSession,
  onNewSession,
  onDeleteSession,
  isOpen,
  width = 264,
  modelName,
  modelMode = 'saas',
  onOpenModelSettings,
}) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [hoveredSession, setHoveredSession] = useState<string | null>(null);

  const closeSearch = () => {
    setSearchOpen(false);
    setSearchQuery('');
  };

  // Filter sessions by search query
  const filteredSessions = sessions.filter(session =>
    session.title.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const groupedSessions = groupSessions(filteredSessions);

  return (
    <div
      className={`flex-shrink-0 bg-layer-01 flex flex-col min-w-0 overflow-hidden transition-[width] duration-200 ease-out ${
        isOpen ? 'border-r border-shell-border' : 'border-r-0'
      }`}
      style={{ width: isOpen ? width : 0 }}
      aria-hidden={!isOpen}
    >
      <div
        className={`h-full flex flex-col min-w-0 transition-[opacity,transform] duration-200 ease-out ${
          isOpen ? 'opacity-100 translate-x-0' : 'pointer-events-none opacity-0 -translate-x-2'
        }`}
        style={{ width }}
      >
        {/* Header row only reserves the macOS traffic-light zone; the toggle itself is a
            fixed control owned by ChatScreen so it stays put when the panel collapses. */}
        <div className="h-11 flex-shrink-0 electron-drag" aria-hidden="true" />

        {/* Quick actions — 목록과 같은 문법의 한 줄 진입점(대형 버튼·입력박스 대신).
            검색 행은 클릭 시 같은 자리에서 입력으로 변신한다 (레이아웃 점프 없음). */}
        <div className="px-2 pt-1 pb-1.5 flex flex-col gap-0.5 flex-shrink-0">
          <button
            onClick={onNewSession}
            className="group flex items-center w-full gap-2.5 h-9 px-2.5 rounded-lg text-body-short-01 font-medium text-text-primary hover:bg-layer-hover-01 transition-colors"
          >
            <Icon
              name="compose"
              size={17}
              className="flex-shrink-0 text-icon-secondary group-hover:text-icon-primary transition-colors"
            />
            새 대화
          </button>

          {searchOpen ? (
            <div className="flex items-center w-full h-9 gap-2.5 px-2.5 rounded-lg bg-layer-02/60 ring-1 ring-inset ring-border-subtle-02 focus-within:ring-focus transition-shadow">
              <Icon name="search" size={17} className="flex-shrink-0 text-icon-secondary" />
              <input
                type="text"
                autoFocus
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Escape') closeSearch();
                }}
                placeholder="대화 검색"
                className="flex-1 min-w-0 bg-transparent border-none outline-none text-text-primary text-body-short-01 font-sans placeholder:text-text-placeholder"
              />
              <button
                onClick={closeSearch}
                title="검색 닫기"
                className="p-0.5 -mr-0.5 rounded hover:bg-layer-hover-02 transition-colors"
              >
                <Icon name="close" size={13} className="text-icon-secondary" />
              </button>
            </div>
          ) : (
            <button
              onClick={() => setSearchOpen(true)}
              className="group flex items-center gap-2.5 w-full h-9 px-2.5 rounded-lg text-body-short-01 text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary transition-colors"
            >
              <Icon
                name="search"
                size={17}
                className="flex-shrink-0 text-icon-secondary group-hover:text-icon-primary transition-colors"
              />
              대화 검색
            </button>
          )}
        </div>

        {/* Session list */}
        <div className="flex-1 overflow-y-auto overflow-x-hidden px-2 py-1">
          {groupedSessions.length === 0 ? (
            <div className="px-4 py-8 text-center text-body-short-01 text-text-helper">
              {searchQuery ? '검색 결과가 없어요' : '아직 대화가 없어요'}
            </div>
          ) : (
            groupedSessions.map((group) => (
              <div key={group.name} className="mb-1.5">
                <div className="text-caption-01 font-medium text-text-helper px-2 pt-2 pb-0.5">
                  {group.name}
                </div>
                {/* 컴팩트 한 줄 행 — 시점은 그룹 헤더가 말해주므로 행에는 제목만 */}
                <div className="space-y-px">
                  {group.items.map((session) => {
                    const isActive = session.id === activeSessionId;
                    return (
                      <div
                        key={session.id}
                        className="relative"
                        onMouseEnter={() => setHoveredSession(session.id)}
                        onMouseLeave={() => setHoveredSession(null)}
                      >
                        <button
                          onClick={() => onSelectSession(session.id)}
                          title={session.title}
                          className={`flex items-center w-full min-w-0 h-6 px-2 text-left cursor-pointer border-l-2 rounded-lg transition-colors ${
                            isActive
                              ? 'border-l-interactive bg-layer-selected-01'
                              : 'border-l-transparent bg-transparent hover:bg-layer-hover-01'
                          }`}
                        >
                          <span
                            className={`block flex-1 min-w-0 text-body-short-01 truncate ${
                              hoveredSession === session.id ? 'pr-6' : ''
                            } ${isActive ? 'text-text-primary font-medium' : 'text-text-secondary'}`}
                          >
                            {session.title}
                          </span>
                        </button>

                        {/* Delete button on hover (incl. the active session) */}
                        {hoveredSession === session.id && (
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              onDeleteSession(session.id);
                            }}
                            className="absolute right-1 top-1/2 -translate-y-1/2 p-1 rounded-md text-icon-secondary hover:bg-layer-hover-02 hover:text-support-error transition-colors"
                            title="대화 삭제"
                          >
                            <Icon name="trash" size={13} />
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            ))
          )}
        </div>

        {/* Model footer */}
        {modelName && onOpenModelSettings && (
          <button
            onClick={onOpenModelSettings}
            data-tour="model-settings"
            className="flex items-center gap-2.5 border-t border-border-subtle-01 px-3 py-3 text-left flex-shrink-0 hover:bg-layer-hover-01 transition-colors"
            title="모델 설정"
          >
            <div className="w-7 h-6 flex-shrink-0 flex items-center justify-center rounded-lg bg-layer-02 border border-border-subtle-02">
              <Icon
                name={modelMode === 'local' ? 'server' : 'cloud'}
                size={15}
                className={modelMode === 'local' ? 'text-teal-40' : 'text-blue-50'}
              />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-body-short-01 text-text-primary truncate">{modelName}</div>
              <div className="text-caption-01 text-text-helper">
                {modelMode === 'local' ? '로컬 · Ollama' : 'SaaS · watsonx'}
              </div>
            </div>
            <Icon name="settings" size={15} className="text-icon-secondary flex-shrink-0" />
          </button>
        )}
      </div>
    </div>
  );
};

// Made with Bob
