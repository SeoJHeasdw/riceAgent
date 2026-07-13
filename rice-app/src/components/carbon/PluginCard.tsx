import React from 'react';
import { useTranslation } from 'react-i18next';
import { Card, Icon, Tag, Toggle } from '../ui';

/**
 * PluginCard - Plugin marketplace card with install/enable toggle
 */

export interface PluginCardProps {
  id: string;
  name: string;
  description: string;
  author: string;
  skillCount: number;
  installed: boolean;
  enabled: boolean;
  builtin?: boolean;
  icon?: string;
  gains?: string;
  useCases: string[];
  /** 구성 스트립용 — plugin이 끌어오는 커넥터/지식 (스킬보다 "무겁다"는 신호) */
  connectors?: { name: string }[];
  knowledge?: { name: string }[];
  onInstall: () => void;
  onToggle: () => void;
  onOpenDetails?: () => void;
}

export const PluginCard: React.FC<PluginCardProps> = ({
  name,
  description,
  author,
  skillCount,
  installed,
  enabled,
  builtin,
  gains,
  useCases,
  connectors,
  knowledge,
  onInstall,
  onToggle,
  onOpenDetails,
}) => {
  const { t } = useTranslation('plugins');
  const connectorCount = connectors?.length ?? 0;
  const knowledgeCount = knowledge?.length ?? 0;
  type CompositionKey = 'card.compositionSkill' | 'card.compositionConnector' | 'card.compositionKnowledge';
  const allComposition: Array<{ n: number; labelKey: CompositionKey; accent: string }> = [
    { n: skillCount, labelKey: 'card.compositionSkill' as const, accent: 'text-blue-50' },
    { n: connectorCount, labelKey: 'card.compositionConnector' as const, accent: 'text-teal-40' },
    { n: knowledgeCount, labelKey: 'card.compositionKnowledge' as const, accent: 'text-magenta-50' },
  ];
  const composition = allComposition.filter((c, i) => i === 0 || c.n > 0); // 스킬은 항상, 커넥터·지식은 있을 때만
  return (
    <Card
      padding="md"
      className={`group relative flex flex-col min-h-[176px] cursor-pointer overflow-hidden transition-colors duration-150 ${
        installed
          ? 'plugin-card--installed'
          : 'plugin-card--idle'
      }`}
      onClick={onOpenDetails}
    >
      <span
        aria-hidden="true"
        className={`absolute left-0 top-0 h-full w-0.5 bg-purple-60 transition-opacity ${
          installed ? 'opacity-70' : 'opacity-0 group-hover:opacity-50'
        }`}
      />
      {/* Header */}
      <div className="relative flex items-start gap-3 mb-3">
        <div className="flex-shrink-0 w-10 h-10 flex items-center justify-center rounded-lg bg-purple-60/12 border border-purple-60/25 text-purple-40">
          <Icon name="plug" size={20} />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-heading-02 font-semibold text-text-primary leading-tight">
            <button
              type="button"
              onClick={(event) => { event.stopPropagation(); onOpenDetails?.(); }}
              className="max-w-full truncate text-left rounded-sm hover:text-link-primary transition-colors duration-150"
            >
              {name}
            </button>
          </h3>
          <p className="text-caption-01 text-text-helper">{author}</p>
        </div>
        {builtin ? (
          <Tag type="teal" size="sm">
            {t('myPlugins.tagBuiltin')}
          </Tag>
        ) : installed ? (
          enabled ? (
            <Tag type="green" size="sm" dot>
              {t('myPlugins.statusActive')}
            </Tag>
          ) : (
            <Tag type="gray" size="sm">
              {t('installed')}
            </Tag>
          )
        ) : (
          // 카드마다 반복되던 대형 CTA 대신 컴팩트 pill — 설치는 즉시 실행
          <button
            onClick={(e) => {
              e.stopPropagation();
              onInstall();
            }}
            className="flex-shrink-0 inline-flex items-center gap-1 h-7 px-3 rounded-full text-caption-01 font-semibold bg-transparent text-text-secondary ring-1 ring-inset ring-border-strong-01 hover:bg-layer-hover-01 hover:text-text-primary transition-colors cursor-pointer"
          >
            {t('install')}
          </button>
        )}
      </div>

      {/* Description */}
      <p className="relative text-body-short-01 text-text-secondary mb-3 flex-1 line-clamp-2">
        {description}
      </p>

      {/* Composition — plugin의 "무게"(스킬·커넥터·지식)를 한눈에. skill 카드와 구분되는 지점. */}
      <div className="relative flex flex-wrap items-center gap-x-2.5 gap-y-1 mb-2.5 text-caption-01">
        {composition.map((c, i) => (
          <React.Fragment key={c.labelKey}>
            {i > 0 && <span className="text-text-helper/50" aria-hidden="true">·</span>}
            <span className="inline-flex items-baseline gap-1">
              <span className={`font-semibold tabular-nums ${c.accent}`}>{c.n}</span>
              <span className="text-text-helper">{t(c.labelKey)}</span>
            </span>
          </React.Fragment>
        ))}
      </div>

      {/* Gains */}
      {gains && (
        <div className="relative flex items-start gap-2 mb-2.5 text-caption-01 text-text-helper">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="flex-shrink-0 mt-0.5">
            <path d="M12 2.5 21.5 12 12 21.5 2.5 12 12 2.5z" />
            <circle cx="12" cy="12" r="3" />
          </svg>
          <span className="flex-1">{gains}</span>
        </div>
      )}

      {/* Use Cases — pill 나열 대신 조용한 한 줄 텍스트 */}
      {useCases.length > 0 && (
        <div className="relative text-caption-01 text-text-placeholder truncate mb-1">
          {useCases.slice(0, 3).join(' · ')}
        </div>
      )}

      {/* Installed footer — 사용 상태 토글 (카드 자체가 상세로 연결되므로 별도 링크 없음) */}
      {installed && (
        <div className="relative flex items-center gap-2.5 pt-3 mt-2 border-t border-border-subtle-01" onClick={(e) => e.stopPropagation()}>
          <Toggle
            checked={enabled}
            onChange={onToggle}
            tone="purple"
            aria-label={enabled ? t('card.ariaToggleDisable', { name }) : t('card.ariaToggleEnable', { name })}
          />
          <span className="flex items-center gap-1.5 flex-1 min-w-0">
            <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${enabled ? 'bg-support-success' : 'bg-text-helper'}`} />
            <span className="text-caption-01 text-text-secondary truncate">
              {enabled ? t('card.statusActive') : t('card.statusPaused')}
            </span>
          </span>
        </div>
      )}
    </Card>
  );
};

// Made with Bob
