import React, { useEffect, useRef, useState } from 'react';
import { Icon } from './Icon';
import type { IconName } from './Icon';

/**
 * OverflowMenu — 케밥(•••) 트리거 + 드롭다운.
 * 주 액션 외의 보조·위험 액션을 한 곳에 접어 액션 바를 평면 나열에서 구해낸다.
 * danger 항목은 빨강 + 명확한 hover 배경을 가지며, 앞에 구분선이 들어간다.
 *
 * 외부 클릭·ESC 닫기 패턴은 StatusControl·VersionChipMenu(MySkillsScreen)와 동일.
 */
export interface OverflowMenuItem {
  label: string;
  icon: IconName;
  onClick: () => void;
  tone?: 'default' | 'danger';
  disabled?: boolean;
}

export interface OverflowMenuProps {
  items: OverflowMenuItem[];
  /** 메뉴 패널 정렬 — 트리거 기준 좌/우 */
  align?: 'left' | 'right';
  /** 펼침 방향 — 'down'(기본)은 아래로, 화면 하단 고정 바에선 'up'으로 위로 펼쳐 잘림을 피한다 */
  direction?: 'up' | 'down';
  /** 트리거 접근성 라벨 */
  label?: string;
}

export const OverflowMenu: React.FC<OverflowMenuProps> = ({ items, align = 'right', direction = 'down', label = '더보기' }) => {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const focusItem = (index: number) => {
    const enabled = itemRefs.current.filter((item): item is HTMLButtonElement => !!item && !item.disabled);
    if (enabled.length === 0) return;
    enabled[(index + enabled.length) % enabled.length]?.focus();
  };

  useEffect(() => {
    if (!open) return;
    const frame = requestAnimationFrame(() => {
      itemRefs.current.find((item) => item && !item.disabled)?.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, [open]);

  // 외부 클릭 닫기
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, [open]);

  // ESC 닫기
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener('keydown', h);
    return () => document.removeEventListener('keydown', h);
  }, [open]);

  return (
    <div ref={ref} className="relative flex-shrink-0">
      <button
        ref={triggerRef}
        onClick={() => setOpen((o) => !o)}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        className={`w-7 h-7 flex items-center justify-center rounded-lg transition-colors ${
          open ? 'bg-layer-hover-01 text-text-primary' : 'text-icon-secondary hover:bg-layer-hover-01 hover:text-text-primary'
        }`}
      >
        <Icon name="overflow" size={18} />
      </button>
      {open && (
        <div
          role="menu"
          onKeyDown={(event) => {
            const enabled = itemRefs.current.filter((item): item is HTMLButtonElement => !!item && !item.disabled);
            const current = enabled.indexOf(document.activeElement as HTMLButtonElement);
            if (event.key === 'ArrowDown') {
              event.preventDefault();
              focusItem(current + 1);
            } else if (event.key === 'ArrowUp') {
              event.preventDefault();
              focusItem(current - 1);
            } else if (event.key === 'Home' || event.key === 'End') {
              event.preventDefault();
              focusItem(event.key === 'Home' ? 0 : -1);
            } else if (event.key === 'Tab') {
              setOpen(false);
            }
          }}
          className={`absolute z-50 min-w-[200px] bg-background border border-border-subtle-01 rounded-lg shadow-elevation-2 overflow-hidden py-1 ${
            direction === 'up' ? 'bottom-full mb-1.5' : 'top-full mt-1.5'
          } ${align === 'right' ? 'right-0' : 'left-0'}`}
        >
          {items.map((item, i) => {
            const danger = item.tone === 'danger';
            // danger 항목 앞 구분선 — 위험 액션을 시각적으로 분리
            const divider = danger && i > 0 && items[i - 1].tone !== 'danger';
            return (
              <React.Fragment key={item.label}>
                {divider && <div className="my-1 border-t border-border-subtle-01" />}
                <button
                  ref={(node) => { itemRefs.current[i] = node; }}
                  role="menuitem"
                  tabIndex={-1}
                  disabled={item.disabled}
                  onClick={() => {
                    if (item.disabled) return;
                    setOpen(false);
                    item.onClick();
                  }}
                  className={`flex items-center gap-2.5 w-full text-left px-3 py-2 text-body-short-01 transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                    danger
                      ? 'text-support-error hover:bg-support-error/10'
                      : 'text-text-primary hover:bg-layer-hover-01'
                  }`}
                >
                  <Icon name={item.icon} size={15} className="flex-shrink-0" />
                  <span className="truncate">{item.label}</span>
                </button>
              </React.Fragment>
            );
          })}
        </div>
      )}
    </div>
  );
};

// Made with Bob
