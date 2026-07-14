import React, { useId } from 'react';
import { useTranslation } from 'react-i18next';

/**
 * IconRail - left navigation rail
 * Modernized: squircle icon buttons with tinted active state,
 * bottom-pinned items (e.g. Settings), animated tooltip
 */

export interface IconRailItem {
  id: string;
  icon: React.ReactNode;
  label: string;
  path: string;
  /** Pin item to the bottom of the rail (e.g. Settings) */
  pinBottom?: boolean;
  /** Draw a divider line below this item, separating it from the next group (e.g. Chat) */
  dividerAfter?: boolean;
}

export interface IconRailProps {
  items: IconRailItem[];
  activeId: string;
  onNavigate: (id: string, path: string) => void;
  /** Reserve the macOS traffic-light zone at the top (screens without the app titlebar, e.g. Chat) */
  topInset?: boolean;
}

const RailButton: React.FC<{
  item: IconRailItem;
  isActive: boolean;
  onNavigate: (id: string, path: string) => void;
}> = ({ item, isActive, onNavigate }) => {
  const tooltipId = useId();
  return (
  <div className="relative group flex justify-center w-full">
    {/* Active indicator pill */}
    <span
      className={`
        absolute left-0 top-1/2 -translate-y-1/2 w-1 rounded-r-full bg-interactive
        transition-all duration-200
        ${isActive ? 'h-4 opacity-100' : 'h-0 opacity-0'}
      `}
    />

    <button
      onClick={() => onNavigate(item.id, item.path)}
      data-tour={`nav-${item.id}`}
      className={`
        electron-no-drag
        flex items-center justify-center w-9 h-9 rounded-[10px]
        transition-colors duration-150
        outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus
        ${isActive
          ? 'bg-shell-active text-shell-text shadow-elevation-1'
          : 'text-shell-muted hover:bg-shell-hover hover:text-shell-text'
        }
      `}
      aria-label={item.label}
      aria-current={isActive ? 'page' : undefined}
      aria-describedby={tooltipId}
    >
      {item.icon}
    </button>

    {/* Tooltip */}
    <span
      id={tooltipId}
      className="
        absolute left-full top-1/2 -translate-y-1/2 ml-1.5 px-2 py-1
        bg-layer-02 text-text-primary text-caption-01 font-medium
        whitespace-nowrap rounded-md border border-border-subtle-01 shadow-elevation-2
        opacity-0 pointer-events-none
        group-hover:opacity-100 group-focus-within:opacity-100
        transition-opacity duration-150 delay-75 z-50
      "
      role="tooltip"
    >
      {item.label}
    </span>
  </div>
  );
};

export const IconRail: React.FC<IconRailProps> = ({ items, activeId, onNavigate, topInset = false }) => {
  const { t } = useTranslation('common');
  const topItems = items.filter((item) => !item.pinBottom);
  const bottomItems = items.filter((item) => item.pinBottom);

  return (
    <nav aria-label={t('mainMenu')} className="w-[52px] h-full bg-shell border-r border-shell-border flex flex-col items-center py-2 electron-drag">
      {/* Traffic-light clearance — mirrors the h-11 titlebar the other screens reserve,
          so the first rail icon sits below the macOS window buttons instead of under them. */}
      {topInset && <div className="h-11 flex-shrink-0" aria-hidden="true" />}

      {/* Main navigation */}
      <div className="flex flex-col items-center gap-1 w-full">
        {topItems.map((item) => (
          <React.Fragment key={item.id}>
            <RailButton
              item={item}
              isActive={item.id === activeId}
              onNavigate={onNavigate}
            />
            {item.dividerAfter && (
              <div
                className="w-6 h-px my-1 bg-shell-divider"
                role="separator"
                aria-orientation="horizontal"
              />
            )}
          </React.Fragment>
        ))}
      </div>

      <div className="flex-1" />

      {/* Bottom-pinned navigation */}
      <div className="flex flex-col items-center gap-1 w-full">
        {bottomItems.map((item) => (
          <RailButton
            key={item.id}
            item={item}
            isActive={item.id === activeId}
            onNavigate={onNavigate}
          />
        ))}
      </div>
    </nav>
  );
};

// Made with Bob
