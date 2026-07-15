import React, { useId, useRef, useState } from 'react';

/**
 * Tabs component following IBM Carbon design principles
 * - Tab navigation component
 * - 0px border radius
 * - Underline indicator for active tab
 */

export interface Tab {
  id: string;
  label: string;
  content?: React.ReactNode;
  disabled?: boolean;
  /** data-tour 속성값 — 투어 타겟으로 사용할 때 */
  dataTour?: string;
}

export interface TabsProps {
  /** Array of tabs */
  tabs: Tab[];
  /** Default active tab id */
  defaultTab?: string;
  /** Controlled active tab id */
  activeTab?: string;
  /** Tab change handler */
  onChange?: (tabId: string) => void;
  /** Additional CSS classes */
  className?: string;
  /** Additional classes for the tab-list row */
  tabListClassName?: string;
  /** Additional classes for the active tab panel */
  panelClassName?: string;
  /** Accessible label for the tab list */
  ariaLabel?: string;
}

export const Tabs: React.FC<TabsProps> = ({
  tabs,
  defaultTab,
  activeTab: controlledActiveTab,
  onChange,
  className = '',
  tabListClassName = '',
  panelClassName = '',
  ariaLabel,
}) => {
  const [internalActiveTab, setInternalActiveTab] = useState(defaultTab || tabs[0]?.id);
  const baseId = useId();
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  
  const activeTab = controlledActiveTab !== undefined ? controlledActiveTab : internalActiveTab;

  const handleTabClick = (tabId: string, disabled?: boolean) => {
    if (disabled) return;
    
    if (controlledActiveTab === undefined) {
      setInternalActiveTab(tabId);
    }
    onChange?.(tabId);
  };

  const activeTabContent = tabs.find(tab => tab.id === activeTab)?.content;
  const enabledTabs = tabs.filter((tab) => !tab.disabled);

  const moveFocus = (currentId: string, direction: 1 | -1) => {
    const current = enabledTabs.findIndex((tab) => tab.id === currentId);
    if (current < 0 || enabledTabs.length === 0) return;
    const next = enabledTabs[(current + direction + enabledTabs.length) % enabledTabs.length];
    handleTabClick(next.id);
    const nextIndex = tabs.findIndex((tab) => tab.id === next.id);
    tabRefs.current[nextIndex]?.focus();
  };

  return (
    <div className={`flex flex-col ${className}`}>
      {/* Tab List */}
      <div
        className={`flex border-b border-border-subtle-01 ${tabListClassName}`}
        role="tablist"
        aria-label={ariaLabel}
      >
        {tabs.map((tab) => {
          const isActive = tab.id === activeTab;
          const isDisabled = tab.disabled;

          return (
            <button
              key={tab.id}
              ref={(node) => { tabRefs.current[tabs.indexOf(tab)] = node; }}
              id={`${baseId}-tab-${tab.id}`}
              role="tab"
              aria-selected={isActive}
              aria-disabled={isDisabled}
              aria-controls={`${baseId}-panel-${tab.id}`}
              tabIndex={isActive ? 0 : -1}
              disabled={isDisabled}
              data-tour={tab.dataTour}
              onClick={() => handleTabClick(tab.id, isDisabled)}
              onKeyDown={(event) => {
                if (event.key === 'ArrowRight') {
                  event.preventDefault();
                  moveFocus(tab.id, 1);
                } else if (event.key === 'ArrowLeft') {
                  event.preventDefault();
                  moveFocus(tab.id, -1);
                } else if (event.key === 'Home' || event.key === 'End') {
                  event.preventDefault();
                  const target = event.key === 'Home' ? enabledTabs[0] : enabledTabs[enabledTabs.length - 1];
                  if (target) {
                    handleTabClick(target.id);
                    tabRefs.current[tabs.findIndex((item) => item.id === target.id)]?.focus();
                  }
                }
              }}
              className={`
                relative px-4 py-2.5 font-sans text-body-short-01 font-medium
                transition-colors rounded-t-md border-0 outline-none
                focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus
                ${isActive
                  ? 'text-text-primary'
                  : isDisabled
                  ? 'text-text-placeholder cursor-not-allowed'
                  : 'text-text-secondary hover:text-text-primary cursor-pointer'
                }
              `}
            >
              {tab.label}
              {/* Active indicator */}
              {isActive && (
                <span className="absolute bottom-0 left-2 right-2 h-0.5 rounded-full bg-interactive" />
              )}
            </button>
          );
        })}
      </div>

      {/* Tab Panel */}
      {activeTabContent && (
        <div
          id={`${baseId}-panel-${activeTab}`}
          role="tabpanel"
          aria-labelledby={`${baseId}-tab-${activeTab}`}
          tabIndex={0}
          className={`py-4 focus-visible:outline-offset-4 ${panelClassName}`}
        >
          {activeTabContent}
        </div>
      )}
    </div>
  );
};

// Made with Bob
