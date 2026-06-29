import React, { useState } from 'react';
import { Button, Icon } from '../ui';

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

// Helper to format relative time
const formatRelativeTime = (date: Date): string => {
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
};

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
    { name: 'Today', items: [] },
    { name: 'Yesterday', items: [] },
    { name: 'Last 7 days', items: [] },
    { name: 'Last 30 days', items: [] },
    { name: 'Older', items: [] },
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
  const [hoveredSession, setHoveredSession] = useState<string | null>(null);

  // Filter sessions by search query
  const filteredSessions = sessions.filter(session =>
    session.title.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const groupedSessions = groupSessions(filteredSessions);

  if (!isOpen) {
    return (
      <div className="w-[52px] flex-shrink-0 bg-layer-01 border-r border-border-subtle-01 flex flex-col items-center">
        <div className="h-12 flex items-center justify-center w-full flex-shrink-0">
          <button
            onClick={onToggle}
            className="flex items-center justify-center w-10 h-10 rounded-xl text-icon-secondary hover:bg-layer-hover-01 hover:text-icon-primary transition-colors"
            title="Show sessions"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <path d="M9 3v18" />
              <path d="m14 9 3 3-3 3" />
            </svg>
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      className="flex-shrink-0 bg-layer-01 border-r border-border-subtle-01 flex flex-col min-w-0 overflow-hidden"
      style={{ width }}
    >
      {/* Header */}
      <div className="flex items-center px-4 h-12 flex-shrink-0 min-w-0">
        <span className="text-label-02 font-semibold text-text-primary truncate">
          Sessions
        </span>
        <span className="ml-2 text-caption-01 font-mono text-text-helper flex-shrink-0">
          {sessions.length}
        </span>
        <button
          onClick={onToggle}
          className="ml-auto -mr-1.5 flex items-center justify-center w-8 h-8 flex-shrink-0 rounded-lg text-icon-secondary hover:bg-layer-hover-01 hover:text-icon-primary transition-colors"
          title="Hide sessions"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <path d="M9 3v18" />
            <path d="m15 9-3 3 3 3" />
          </svg>
        </button>
      </div>

      {/* New session button */}
      <div className="px-3 pb-2">
        <Button
          kind="primary"
          size="sm"
          fullWidth
          onClick={onNewSession}
          icon={
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M12 5v14M5 12h14" />
            </svg>
          }
        >
          New chat
        </Button>
      </div>

      {/* Search */}
      <div className="px-3 pb-2">
        <div className="flex items-center gap-2 bg-field-01 px-2.5 h-8 border border-border-subtle-01 rounded-lg transition-colors focus-within:border-focus">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" className="text-icon-secondary flex-shrink-0">
            <circle cx="11" cy="11" r="7" />
            <path d="m21 21-4.35-4.35" />
          </svg>
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search sessions"
            className="flex-1 min-w-0 bg-transparent border-none outline-none text-text-primary text-body-short-01 font-sans placeholder:text-text-placeholder"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery('')}
              className="p-0.5 rounded hover:bg-layer-hover-01 transition-colors"
            >
              <svg width="12" height="12" viewBox="0 0 32 32" fill="currentColor" className="text-icon-secondary">
                <path d="M24 9.4L22.6 8 16 14.6 9.4 8 8 9.4l6.6 6.6L8 22.6 9.4 24l6.6-6.6 6.6 6.6 1.4-1.4-6.6-6.6L24 9.4z"/>
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden px-2 py-1">
        {groupedSessions.length === 0 ? (
          <div className="px-4 py-8 text-center text-body-short-01 text-text-helper">
            {searchQuery ? 'No sessions found' : 'No sessions yet'}
          </div>
        ) : (
          groupedSessions.map((group) => (
            <div key={group.name} className="mb-2">
              <div className="text-caption-01 font-semibold uppercase tracking-wider text-text-helper px-2 py-1.5">
                {group.name}
              </div>
              <div className="space-y-0.5">
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
                        className={`flex flex-col gap-0.5 w-full min-w-0 overflow-hidden border-none px-2.5 py-2 text-left cursor-pointer rounded-lg transition-colors ${
                          isActive
                            ? 'bg-[#0f62fe]/15'
                            : 'bg-transparent hover:bg-layer-hover-01'
                        }`}
                      >
                        <span
                          className={`block max-w-full min-w-0 text-body-short-01 truncate pr-6 ${
                            isActive ? 'text-text-primary font-semibold' : 'text-text-primary'
                          }`}
                        >
                          {session.title}
                        </span>
                        <span className="block max-w-full min-w-0 text-caption-01 text-text-helper truncate">
                          {formatRelativeTime(session.timestamp)}
                          {session.messageCount > 0 ? ` · ${session.messageCount} messages` : ''}
                        </span>
                      </button>

                      {/* Delete button on hover (incl. the active session) */}
                      {hoveredSession === session.id && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            onDeleteSession(session.id);
                          }}
                          className="absolute right-1.5 top-1/2 -translate-y-1/2 p-1 rounded-md text-icon-secondary hover:bg-layer-hover-02 hover:text-support-error transition-colors"
                          title="Delete session"
                        >
                          <svg width="14" height="14" viewBox="0 0 32 32" fill="currentColor">
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