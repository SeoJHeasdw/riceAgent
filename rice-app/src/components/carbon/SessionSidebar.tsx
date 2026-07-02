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
  onToggle: () => void;
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
  onToggle,
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

  // 접히면 미니 레일 없이 완전히 사라진다 — 다시 여는 토글은 채팅 헤더가 맡는다.
  if (!isOpen) return null;

  return (
    <div
      className="flex-shrink-0 bg-layer-01 border-r border-border-subtle-01 flex flex-col min-w-0 overflow-hidden"
      style={{ width }}
    >
      {/* Header — 채팅 헤더(h-[52px] + border-b)와 같은 높이로 맞춰 한 줄로 이어지게 */}
      <div className="flex items-center px-4 h-[52px] border-b border-border-subtle-01 flex-shrink-0 min-w-0">
        <span className="text-label-02 font-semibold text-text-primary truncate">
          대화
        </span>
        <span className="ml-2 text-caption-01 font-mono text-text-helper flex-shrink-0">
          {sessions.length}
        </span>
        <button
          onClick={onToggle}
          className="ml-auto -mr-1.5 flex items-center justify-center w-8 h-8 flex-shrink-0 rounded-lg text-icon-secondary hover:bg-layer-hover-01 hover:text-icon-primary transition-colors"
          title="대화 목록 접기"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <path d="M9 3v18" />
            <path d="m15 9-3 3 3 3" />
          </svg>
        </button>
      </div>

      {/* Quick actions — 대형 버튼·입력박스 대신 목록과 같은 문법의 한 줄 진입점.
          검색 행은 클릭 시 같은 자리에서 입력으로 변신한다 (레이아웃 점프 없음). */}
      <div className="px-2 pt-2 pb-1 flex flex-col gap-0.5 flex-shrink-0">
        <button
          onClick={onNewSession}
          className="flex items-center w-full gap-0.5 h-6 px-0.5 rounded-lg text-body-short-01 font-medium text-link-primary hover:bg-interactive/10 transition-colors"
        >
          <span className="flex items-center justify-center w-5 h-5 flex-shrink-0">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </span>
          새 대화
        </button>

        {searchOpen ? (
          <div className="flex items-center w-full h-6 px-0.5 rounded-lg bg-layer-02/60 ring-1 ring-inset ring-border-subtle-02 focus-within:ring-focus transition-shadow">
            <span className="flex items-center justify-center w-5 h-5 flex-shrink-0 text-icon-secondary">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                <circle cx="11" cy="11" r="7" />
                <path d="m21 21-4.35-4.35" />
              </svg>
            </span>
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
              <svg width="12" height="12" viewBox="0 0 32 32" fill="currentColor" className="text-icon-secondary">
                <path d="M24 9.4L22.6 8 16 14.6 9.4 8 8 9.4l6.6 6.6L8 22.6 9.4 24l6.6-6.6 6.6 6.6 1.4-1.4-6.6-6.6L24 9.4z"/>
              </svg>
            </button>
          </div>
        ) : (
          <button
            onClick={() => setSearchOpen(true)}
            className="flex items-center gap-0.5 w-full h-5 px-0.5 rounded-lg text-body-short-01 text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary transition-colors"
          >
            <span className="flex items-center justify-center w-5 h-5 flex-shrink-0">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                <circle cx="11" cy="11" r="7" />
                <path d="m21 21-4.35-4.35" />
              </svg>
            </span>
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
                        className={`flex items-center w-full min-w-0 h-6 px-2 text-left cursor-pointer border-none rounded-lg transition-colors ${
                          isActive
                            ? 'bg-layer-selected-01'
                            : 'bg-transparent hover:bg-layer-hover-01'
                        }`}
                      >
                        <span
                          className={`block flex-1 min-w-0 text-[13px] truncate ${
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
                          <svg width="13" height="13" viewBox="0 0 32 32" fill="currentColor">
                            <path d="M12 12h2v12h-2zm6 0h2v12h-2z"/>
                            <path d="M4 6v2h2v20a2 2 0 002 2h16a2 2 0 002-2V8h2V6zm4 22V8h16v20zm4-26h8v2h-8z"/>
                          </svg>
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
          <div
            className={`w-7 h-7 flex-shrink-0 flex items-center justify-center rounded-lg ${
              modelMode === 'local' ? 'bg-teal-60' : 'bg-blue-60'
            }`}
          >
            <Icon name={modelMode === 'local' ? 'server' : 'cloud'} size={15} className="text-white" />
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
  );
};

// Made with Bob