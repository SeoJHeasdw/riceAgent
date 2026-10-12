import React from 'react';
import { useTranslation } from 'react-i18next';

/**
 * MarketplaceHeader - Header for marketplace screens with search, sort, and filters
 */

export interface MarketplaceHeaderProps {
  title: string;
  subtitle?: string;
  searchValue: string;
  onSearchChange: (value: string) => void;
  sortValue: string;
  sortOptions: { value: string; label: string }[];
  onSortChange: (value: string) => void;
  filterTags?: string[];
  selectedTags?: string[];
  onTagToggle?: (tag: string) => void;
}

export const MarketplaceHeader: React.FC<MarketplaceHeaderProps> = ({
  title,
  subtitle,
  searchValue,
  onSearchChange,
  sortValue,
  sortOptions,
  onSortChange,
  filterTags,
  selectedTags = [],
  onTagToggle,
}) => {
  const { t } = useTranslation('connectors');

  return (
    <div className="px-8 pt-7 pb-1">
      {/* Title and subtitle */}
      <div className="flex flex-col items-start gap-1 mb-1">
        <h1 className="text-heading-03 font-light text-text-primary leading-tight">
          {title}
        </h1>
        {subtitle && (
          <span className="max-w-[760px] text-body-short-01 text-text-helper leading-relaxed">
            {subtitle}
          </span>
        )}
      </div>

      {/* Search and sort */}
      <div className="flex items-center gap-3 mt-5 mb-2">
        {/* Search input */}
        <div className="flex items-center gap-2 bg-field-01 px-3 h-9 flex-1 max-w-[380px] border border-border-subtle-01 rounded-lg transition-colors focus-within:border-focus hover:border-border-strong-01">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" className="text-icon-secondary flex-shrink-0">
            <circle cx="11" cy="11" r="7" />
            <path d="m21 21-4.35-4.35" />
          </svg>
          <input
            type="text"
            aria-label={t('marketplaceHeader.searchAriaLabel', { title })}
            value={searchValue}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder={t('marketplaceHeader.searchPlaceholder')}
            className="flex-1 min-w-0 bg-transparent border-0 outline-none text-text-primary text-body-short-01 placeholder:text-text-placeholder"
          />
          {searchValue && (
            <button
              onClick={() => onSearchChange('')}
              className="p-0.5 rounded hover:bg-layer-hover-01 transition-colors"
              aria-label={t('marketplaceHeader.clearSearch')}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="text-icon-secondary">
                <path d="M18 6 6 18M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>

        {/* Sort — segmented control */}
        <div role="group" aria-label={t('marketplaceHeader.sortAriaLabel')} className="ml-auto flex items-center gap-0.5 p-1 bg-field-01 border border-border-subtle-01 rounded-xl">
          {sortOptions.map((option) => {
            const isActive = sortValue === option.value;
            return (
              <button
                key={option.value}
                onClick={() => onSortChange(option.value)}
                aria-pressed={isActive}
                className={`px-3 h-7 text-label-01 font-medium rounded-lg whitespace-nowrap border transition-colors duration-150 ${
                  isActive
                    ? 'bg-layer-selected-01 text-text-primary border-interactive'
                    : 'bg-transparent text-text-secondary border-transparent hover:text-text-primary hover:bg-layer-hover-01'
                }`}
              >
                {option.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Filter tags */}
      {filterTags && filterTags.length > 0 && onTagToggle && (
        <div role="group" aria-label={t('marketplaceHeader.tagFilterAriaLabel')} className="flex flex-wrap gap-2 mt-4">
          {filterTags.map((tag) => {
            const isSelected = selectedTags.includes(tag);
            return (
              <button
                key={tag}
                onClick={() => onTagToggle(tag)}
                className={`
                  px-3 py-1.5 text-label-01 border rounded-full transition-colors
                  ${isSelected
                    ? 'bg-interactive border-interactive text-white'
                    : 'bg-transparent border-border-strong-01 text-text-secondary hover:bg-layer-hover-01'
                  }
                `}
              >
                {tag}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};
