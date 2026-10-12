import React, { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon, OverflowMenu } from '../ui';

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
  onRenameSession: (id: string, title: string) => Promise<boolean>;
  onDeleteSession: (id: string) => void;
  isOpen: boolean;
  /** Disable width easing while the user is actively dragging the divider. */
  isResizing?: boolean;
  /** Panel width in px (drag-resizable from parent) */
  width?: number;
  /** Selected model name shown in the footer model button */
  modelName?: string;
  /** Current model mode, controls the footer accent + sub-label */
  modelMode?: 'saas' | 'local';
  /** Opens the model settings panel when the footer button is clicked */
  onOpenModelSettings?: () => void;
}

// Stable group keys (not translated) — translation happens at render time.
type GroupKey = 'groupToday' | 'groupYesterday' | 'groupLast7Days' | 'groupLast30Days' | 'groupOlder';

const groupSessions = (sessions: Session[]): Array<{ key: GroupKey; items: Session[] }> => {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  const lastWeek = new Date(today);
  lastWeek.setDate(lastWeek.getDate() - 7);
  const lastMonth = new Date(today);
  lastMonth.setMonth(lastMonth.getMonth() - 1);

  const groups: Array<{ key: GroupKey; items: Session[] }> = [
    { key: 'groupToday', items: [] },
    { key: 'groupYesterday', items: [] },
    { key: 'groupLast7Days', items: [] },
    { key: 'groupLast30Days', items: [] },
    { key: 'groupOlder', items: [] },
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
  onRenameSession,
  onDeleteSession,
  isOpen,
  isResizing = false,
  width = 264,
  modelName,
  modelMode = 'saas',
  onOpenModelSettings,
}) => {
  const { t } = useTranslation('chat');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [renamingSessionId, setRenamingSessionId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState('');
  const [listScrolled, setListScrolled] = useState(false);
  const skipRenameCommit = useRef(false);

  const closeSearch = () => {
    setSearchOpen(false);
    setSearchQuery('');
  };

  // Filter sessions by search query
  const filteredSessions = sessions.filter(session =>
    session.title.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const groupedSessions = groupSessions(filteredSessions);

  const beginRename = (session: Session) => {
    skipRenameCommit.current = false;
    setRenamingSessionId(session.id);
    setRenameDraft(session.title);
  };

  const cancelRename = () => {
    skipRenameCommit.current = true;
    setRenamingSessionId(null);
    setRenameDraft('');
  };

  const commitRename = async (session: Session) => {
    const title = renameDraft.trim();
    setRenamingSessionId(null);
    setRenameDraft('');
    if (!title || title === session.title) return;
    await onRenameSession(session.id, title);
  };

  return (
    <div
      className={`flex-shrink-0 bg-layer-01 flex flex-col min-w-0 overflow-hidden ${
        isResizing ? 'transition-none will-change-[width]' : 'transition-[width] duration-200 ease-out'
      } ${
        isOpen ? 'border-r border-shell-border' : 'border-r-0'
      }`}
      style={{ width: isOpen ? `var(--rice-session-panel-width, ${width}px)` : 0 }}
      aria-hidden={!isOpen}
    >
      <div
        className={`h-full flex flex-col min-w-0 transition-[opacity,transform] duration-200 ease-out ${
          isOpen ? 'opacity-100 translate-x-0' : 'pointer-events-none opacity-0 -translate-x-2'
        }`}
        style={{ width: `var(--rice-session-panel-width, ${width}px)` }}
      >
        {/* Header row only reserves the macOS traffic-light zone; the toggle itself is a
            fixed control owned by ChatScreen so it stays put when the panel collapses. */}
        <div className="h-11 flex-shrink-0 electron-drag" aria-hidden="true" />

        {/* Quick actions — 목록과 같은 문법의 한 줄 진입점(대형 버튼·입력박스 대신).
            검색 행은 클릭 시 같은 자리에서 입력으로 변신한다 (레이아웃 점프 없음). */}
        <div
          className={`relative z-10 px-2 pt-1 pb-1.5 flex flex-col gap-0.5 flex-shrink-0 bg-layer-01 transition-shadow duration-200 ${
            listScrolled ? 'shadow-[0_8px_12px_-14px_rgba(0,0,0,0.8)]' : ''
          }`}
        >
          <button
            onClick={onNewSession}
            className="group flex items-center w-full gap-2.5 h-9 px-2.5 rounded-lg text-body-short-01 font-medium text-text-primary hover:bg-layer-hover-01 transition-colors"
          >
            <Icon
              name="compose"
              size={17}
              className="flex-shrink-0 text-icon-secondary group-hover:text-icon-primary transition-colors"
            />
            {t('sessionSidebar.newChat')}
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
                placeholder={t('sessionSidebar.searchPlaceholder')}
                className="flex-1 min-w-0 bg-transparent border-none outline-none text-text-primary text-body-short-01 font-sans placeholder:text-text-placeholder"
              />
              <button
                onClick={closeSearch}
                title={t('sessionSidebar.searchClose')}
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
              {t('sessionSidebar.searchButton')}
            </button>
          )}
        </div>

        {/* Session list */}
        <div className="relative flex-1 min-h-0">
          <div
            onScroll={(event) => setListScrolled(event.currentTarget.scrollTop > 2)}
            className="h-full overflow-y-auto overflow-x-hidden px-2 py-1"
          >
            {groupedSessions.length === 0 ? (
              <div className="px-4 py-8 text-center text-body-short-01 text-text-helper">
                {searchQuery ? t('sessionSidebar.emptySearch') : t('sessionSidebar.emptyDefault')}
              </div>
            ) : (
              groupedSessions.map((group) => (
                <div key={group.key} className="mb-1.5">
                  <div className="text-caption-01 font-medium text-text-helper px-2 pt-2 pb-0.5">
                    {t(`sessionSidebar.${group.key}`)}
                  </div>
                  {/* 컴팩트 한 줄 행 — 시점은 그룹 헤더가 말해주므로 행에는 제목만 */}
                  <div className="space-y-0">
                    {group.items.map((session) => {
                    const isActive = session.id === activeSessionId;
                    const isRenaming = renamingSessionId === session.id;
                    return (
                      <div
                        key={session.id}
                        className="group relative focus-within:z-30"
                      >
                        {isRenaming ? (
                          <div className="flex h-6 items-center px-3 pr-2">
                            <input
                              type="text"
                              autoFocus
                              maxLength={100}
                              value={renameDraft}
                              onFocus={(event) => event.currentTarget.select()}
                              onChange={(event) => setRenameDraft(event.target.value)}
                              onBlur={() => {
                                if (skipRenameCommit.current) {
                                  skipRenameCommit.current = false;
                                  return;
                                }
                                void commitRename(session);
                              }}
                              onKeyDown={(event) => {
                                if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
                                  event.preventDefault();
                                  event.currentTarget.blur();
                                } else if (event.key === 'Escape') {
                                  event.preventDefault();
                                  cancelRename();
                                }
                              }}
                              aria-label={t('sessionSidebar.renameInputLabel')}
                              className="h-5 min-w-0 flex-1 border-0 border-b border-border-subtle-02 bg-transparent px-0.5 text-body-short-01 text-text-primary outline-none transition-colors focus:border-border-strong-01"
                            />
                          </div>
                        ) : (
                          <button
                            onClick={() => onSelectSession(session.id)}
                            title={session.title}
                           className={`flex h-6 w-full min-w-0 cursor-pointer items-center rounded-md border-l-2 px-3 pr-8 text-left transition-colors ${
                             isActive
                               ? 'session-sidebar-row-active'
                               : 'border-l-transparent bg-transparent hover:bg-layer-hover-01'
                           }`}
                          >
                            <span className={`block min-w-0 flex-1 truncate text-body-short-01 ${
                             isActive ? 'font-medium text-text-primary' : 'text-text-secondary'
                           }`}>
                              {session.title}
                            </span>
                          </button>
                        )}

                        {!isRenaming && (
                          <div className={`absolute right-0 top-1/2 -translate-y-1/2 transition-opacity ${
                            isActive
                              ? 'pointer-events-auto opacity-100'
                              : 'pointer-events-none opacity-0 group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100'
                          }`}>
                            <OverflowMenu
                              variant="compact"
                              label={t('sessionSidebar.moreActions', { title: session.title })}
                              items={[
                                {
                                  label: t('sessionSidebar.renameSession'),
                                  icon: 'edit',
                                  onClick: () => beginRename(session),
                                },
                                {
                                  label: t('sessionSidebar.deleteSession'),
                                  icon: 'trash',
                                  tone: 'danger',
                                  onClick: () => onDeleteSession(session.id),
                                },
                              ]}
                            />
                          </div>
                        )}
                      </div>
                    );
                    })}
                  </div>
                </div>
              ))
            )}
          </div>

          {/* 스크롤된 첫 행이 고정 퀵 액션 아래로 자연스럽게 사라지도록 하는 fade. */}
          <div
            aria-hidden="true"
            className={`pointer-events-none absolute inset-x-0 top-0 z-10 h-4 bg-gradient-to-b from-layer-01 via-layer-01/85 to-transparent transition-opacity duration-200 ${
              listScrolled ? 'opacity-100' : 'opacity-0'
            }`}
          />
        </div>

        {/* Model footer */}
        {modelName && onOpenModelSettings && (
          <button
            onClick={onOpenModelSettings}
            data-tour="model-settings"
            className="flex items-center gap-2.5 border-t border-border-subtle-01 px-3 py-3 text-left flex-shrink-0 hover:bg-layer-hover-01 transition-colors"
            title={t('sessionSidebar.modelSettings')}
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
                {modelMode === 'local' ? t('sessionSidebar.modelLocal') : t('sessionSidebar.modelSaas')}
              </div>
            </div>
          </button>
        )}
      </div>
    </div>
  );
};
