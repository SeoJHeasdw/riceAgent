import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Icon, Kbd } from '../ui';
import { NavIcon } from '../../lib/navigation/NavIcon';
import { navDestinations } from '../../lib/navigation';
import { useLearning } from '../../lib/learning/LearningContext';
import { replayOnboarding, replayTour } from '../../lib/tutorial';
import { runSkillCommand, startNewChat } from '../../lib/commandBridge';
import { OPEN_COMMAND_PALETTE_EVENT } from '../../lib/commandBridge';
import { useDialogA11y } from '../../hooks/useDialogA11y';

/**
 * CommandPalette — 앱 전역 Cmd/Ctrl+K 팔레트.
 *
 * 세 그룹(빠른 작업 / 이동 / 스킬 실행)을 하나의 검색·키보드 탐색으로 묶는다.
 * "스킬 실행"·"새 대화"는 ChatScreen 로컬 상태를 다루므로 commandBridge의
 * window 커스텀 이벤트로 위임한다(다른 화면에 있을 때는 /chat으로 이동 후
 * ChatScreen이 마운트될 시간을 주고 이벤트를 쏜다).
 */

interface Row {
  key: string;
  icon: React.ReactNode;
  title: string;
  sub: string;
  tint: string;
  keywords: string;
  run: () => void;
}

const TINT = {
  action: 'var(--ai-gradient-end)',
  nav: 'var(--accent-skill)',
  skill: 'var(--accent-skill)',
} as const;

export const CommandPalette: React.FC = () => {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [active, setActive] = useState(0);
  const navigate = useNavigate();
  const location = useLocation();
  const { skills } = useLearning();
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const close = () => setOpen(false);
  useDialogA11y({ open, dialogRef: panelRef, onClose: close, initialFocusRef: inputRef });

  // 다른 화면에 있으면 /chat으로 이동 후, ChatScreen이 마운트되어 리스너가
  // 붙을 시간을 주고서 이벤트를 쏜다(같은 틱 dispatch는 유실될 수 있다).
  const dispatchToChat = (fire: () => void) => {
    if (location.pathname === '/chat') {
      fire();
    } else {
      navigate('/chat');
      window.setTimeout(fire, 80);
    }
  };

  // 전역 Cmd/Ctrl+K 토글
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    const openPalette = () => setOpen(true);
    window.addEventListener(OPEN_COMMAND_PALETTE_EVENT, openPalette);
    return () => window.removeEventListener(OPEN_COMMAND_PALETTE_EVENT, openPalette);
  }, []);

  useEffect(() => {
    if (!open) return;
    setQ('');
    setActive(0);
  }, [open]);

  const groups = useMemo(() => {
    const query = q.trim().toLowerCase();
    const hit = (...parts: string[]) => !query || parts.some((p) => p.toLowerCase().includes(query));

    const actions: Row[] = [
      {
        key: 'new-chat',
        icon: <Icon name="add" size={15} />,
        title: '새 대화 시작',
        sub: '현재 대화를 접고 새로 시작',
        tint: TINT.action,
        keywords: '새 대화 new chat',
        run: () => dispatchToChat(startNewChat),
      },
      {
        key: 'replay-tour',
        icon: <Icon name="idea" size={15} />,
        title: '가이드 투어 다시보기',
        sub: '핵심 기능 말풍선 투어',
        tint: TINT.action,
        keywords: '투어 가이드 tour guide',
        run: replayTour,
      },
      {
        key: 'replay-onboarding',
        icon: <Icon name="model" size={15} />,
        title: '모델 온보딩 다시보기',
        sub: '실행 위치·모델 다시 설정',
        tint: TINT.action,
        keywords: '온보딩 모델 설정 onboarding model',
        run: replayOnboarding,
      },
    ].filter((r) => hit(r.title, r.sub, r.keywords));

    const nav: Row[] = navDestinations
      .map((d) => ({
        key: `nav-${d.id}`,
        icon: <NavIcon size={15}>{d.iconPaths}</NavIcon>,
        title: d.title ?? d.label,
        sub: d.description,
        tint: TINT.nav,
        keywords: `${d.label} ${d.path}`,
        run: () => navigate(d.path),
      }))
      .filter((r) => hit(r.title, r.sub, r.keywords));

    const skillRows: Row[] = [...skills]
      .filter((s) => s.enabled && s.command)
      .sort((a, b) => b.uses - a.uses)
      .filter((s) => hit(s.name, s.command!))
      .slice(0, query ? 20 : 6)
      .map((s) => ({
        key: `skill-${s.id}`,
        icon: <Icon name="skill" size={15} />,
        title: s.name,
        sub: `/${s.command} · ${s.ver} · ${s.uses}회 사용`,
        tint: TINT.skill,
        keywords: `${s.name} ${s.command}`,
        run: () => dispatchToChat(() => runSkillCommand(s.command!)),
      }));

    return { actions, nav, skillRows };
  }, [q, skills, location.pathname]); // eslint-disable-line react-hooks/exhaustive-deps

  const flat = [...groups.actions, ...groups.nav, ...groups.skillRows];
  const activeIdx = Math.min(active, Math.max(flat.length - 1, 0));

  useEffect(() => {
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [activeIdx]);

  const select = (row: Row) => {
    row.run();
    close();
  };

  if (!open) return null;

  let cursor = -1;
  const renderRow = (r: Row) => {
    cursor += 1;
    const idx = cursor;
    const isActive = idx === activeIdx;
    return (
      <button
        key={r.key}
        id={`command-${r.key}`}
        role="option"
        aria-selected={isActive}
        data-active={isActive || undefined}
        onClick={() => select(r)}
        onMouseEnter={() => setActive(idx)}
        className={`relative flex items-center gap-2.5 w-full py-2 pl-3 pr-3 text-left transition-colors ${
          isActive ? 'bg-layer-hover-02' : ''
        }`}
      >
        {isActive && (
          <span className="absolute left-0 top-1 bottom-1 w-0.5 rounded-full" style={{ background: r.tint }} />
        )}
        <span
          className="w-7 h-7 rounded-md flex items-center justify-center flex-shrink-0"
          style={{ color: r.tint, background: `color-mix(in srgb, ${r.tint} 13%, transparent)` }}
        >
          {r.icon}
        </span>
        <div className="flex-1 min-w-0">
          <div className="text-body-short-01 text-text-primary truncate">{r.title}</div>
          <div className="text-caption-01 text-text-helper truncate">{r.sub}</div>
        </div>
        {isActive && <Icon name="arrow-right" size={13} className="text-icon-secondary flex-shrink-0" />}
      </button>
    );
  };

  const GroupLabel: React.FC<{ children: React.ReactNode; tint: string }> = ({ children, tint }) => (
    <div className="flex items-center gap-1.5 px-3 pt-2.5 pb-1">
      <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: tint }} />
      <span className="text-label-micro font-semibold uppercase tracking-wider text-text-helper">{children}</span>
    </div>
  );

  const empty = flat.length === 0;

  return (
    <div
      className="fixed inset-0 z-[100] flex justify-center pt-[15vh]"
      onMouseDown={(e) => {
        if (!panelRef.current?.contains(e.target as Node)) close();
      }}
    >
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" style={{ animation: 'cpFadeIn 140ms ease' }} />

      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="커맨드 팔레트"
        className="relative z-[1] w-[560px] max-w-[92vw] max-h-[60vh] bg-layer-02/95 backdrop-blur-md border border-border-subtle-01 rounded-xl shadow-elevation-3 overflow-hidden flex flex-col"
        style={{ animation: 'cpPanelIn 160ms cubic-bezier(0.16, 1, 0.3, 1)' }}
      >
        {/* Search */}
        <div className="flex items-center gap-2.5 px-4 h-11 border-b border-border-subtle-01 flex-shrink-0">
          <Icon name="search" size={16} className="text-icon-secondary flex-shrink-0" />
          <input
            ref={inputRef}
            value={q}
            role="combobox"
            aria-autocomplete="list"
            aria-expanded="true"
            aria-controls="command-palette-results"
            aria-activedescendant={flat[activeIdx] ? `command-${flat[activeIdx].key}` : undefined}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') {
                e.preventDefault();
                setActive((a) => Math.min(a + 1, Math.max(flat.length - 1, 0)));
              } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                setActive((a) => Math.max(a - 1, 0));
              } else if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                e.preventDefault();
                const row = flat[activeIdx];
                if (row) select(row);
              } else if (e.key === 'Escape') {
                e.preventDefault();
                close();
              }
            }}
            placeholder="이동·스킬·빠른 작업 검색…"
            className="flex-1 min-w-0 bg-transparent border-none outline-none text-body-short-01 text-text-primary placeholder:text-text-placeholder"
          />
          <Kbd>esc</Kbd>
        </div>

        {/* Results */}
        <div id="command-palette-results" ref={listRef} role="listbox" className="flex-1 overflow-y-auto py-1">
          {empty && <div className="px-3 py-8 text-center text-caption-01 text-text-helper">검색 결과가 없어요</div>}

          {groups.actions.length > 0 && (
            <div>
              <GroupLabel tint={TINT.action}>빠른 작업</GroupLabel>
              {groups.actions.map(renderRow)}
            </div>
          )}
          {groups.nav.length > 0 && (
            <div>
              <GroupLabel tint={TINT.nav}>이동</GroupLabel>
              {groups.nav.map(renderRow)}
            </div>
          )}
          {groups.skillRows.length > 0 && (
            <div>
              <GroupLabel tint={TINT.skill}>스킬 실행</GroupLabel>
              {groups.skillRows.map(renderRow)}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center gap-1 px-3 py-2 border-t border-border-subtle-01 text-label-micro text-text-helper select-none flex-shrink-0">
          <Kbd>↑↓</Kbd> 탐색
          <Kbd>↵</Kbd> 실행
          <span className="ml-auto flex items-center gap-1">
            <Kbd>⌘K</Kbd> 열기/닫기
          </span>
        </div>
      </div>

      <style>{`
        @keyframes cpFadeIn { from { opacity: 0; } to { opacity: 1; } }
        @keyframes cpPanelIn {
          from { opacity: 0; transform: translateY(-8px) scale(0.98); }
          to { opacity: 1; transform: none; }
        }
      `}</style>
    </div>
  );
};

// Made with Bob
