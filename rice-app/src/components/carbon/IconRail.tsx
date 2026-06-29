import React from 'react';

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
}

const RailButton: React.FC<{
  item: IconRailItem;
  isActive: boolean;
  onNavigate: (id: string, path: string) => void;
}> = ({ item, isActive, onNavigate }) => (
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
        flex items-center justify-center w-9 h-9 rounded-[10px]
        transition-all duration-150 active:scale-95
        outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus
        ${isActive
          ? 'bg-[#0f62fe]/15 text-[#78a9ff] shadow-[inset_0_0_0_1px_rgba(15,98,254,0.25)]'
          : 'text-icon-secondary hover:bg-layer-hover-01 hover:text-icon-primary'
        }
      `}
      aria-label={item.label}
      aria-current={isActive ? 'page' : undefined}
    >
      {item.icon}
    </button>

    {/* Tooltip */}
    <span
      className="
        absolute left-full top-1/2 -translate-y-1/2 ml-1.5 px-2 py-1
        bg-layer-02 text-text-primary text-caption-01 font-medium
        whitespace-nowrap rounded-md border border-border-subtle-01 shadow-elevation-2
        opacity-0 -translate-x-1 pointer-events-none
        group-hover:opacity-100 group-hover:translate-x-0
        transition-all duration-150 delay-75 z-50
      "
      role="tooltip"
    >
      {item.label}
    </span>
  </div>
);

export const IconRail: React.FC<IconRailProps> = ({ items, activeId, onNavigate }) => {
  const topItems = items.filter((item) => !item.pinBottom);
  const bottomItems = items.filter((item) => item.pinBottom);

  return (
    <nav className="w-[52px] h-full bg-gradient-to-b from-[#0e1a3a] to-[#0a1020] border-r border-[#1d2c52] flex flex-col items-center py-2">
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
                className="w-6 h-px my-1 bg-text-secondary opacity-40"
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
