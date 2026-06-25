import React, { useState } from 'react';

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
}

export const Tabs: React.FC<TabsProps> = ({
  tabs,
  defaultTab,
  activeTab: controlledActiveTab,
  onChange,
  className = '',
}) => {
  const [internalActiveTab, setInternalActiveTab] = useState(defaultTab || tabs[0]?.id);
  
  const activeTab = controlledActiveTab !== undefined ? controlledActiveTab : internalActiveTab;

  const handleTabClick = (tabId: string, disabled?: boolean) => {
    if (disabled) return;
    
    if (controlledActiveTab === undefined) {
      setInternalActiveTab(tabId);
    }
    onChange?.(tabId);
  };

  const activeTabContent = tabs.find(tab => tab.id === activeTab)?.content;

  return (
    <div className={`flex flex-col ${className}`}>
      {/* Tab List */}
      <div
        className="flex border-b border-border-subtle-01"
        role="tablist"
      >
        {tabs.map((tab) => {
          const isActive = tab.id === activeTab;
          const isDisabled = tab.disabled;

          return (
            <button
              key={tab.id}
              role="tab"
              aria-selected={isActive}
              aria-disabled={isDisabled}
              disabled={isDisabled}
              onClick={() => handleTabClick(tab.id, isDisabled)}
              className={`
                relative px-4 py-3 font-sans text-body-short-02 font-normal
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
        <div role="tabpanel" className="py-4">
          {activeTabContent}
        </div>
      )}
    </div>
  );
};

// Made with Bob
