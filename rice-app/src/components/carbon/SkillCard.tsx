import React, { useEffect, useRef, useState } from 'react';
import { Card, Tag } from '../ui';

/**
 * SkillCard - Skill marketplace card with download/sponsor actions
 */

export interface SkillCardProps {
  id: string;
  name: string;
  description: string;
  author: string;
  team?: string;
  version?: string;
  downloads: number;
  rating: number;
  points?: number;
  tags: string[];
  installed: boolean;
  onInstall: () => void;
  onSponsor: () => void;
}

export const SkillCard: React.FC<SkillCardProps> = ({
  name,
  description,
  author,
  team,
  version,
  downloads,
  rating,
  points,
  tags,
  installed,
  onInstall,
  onSponsor,
}) => {
  const [expanded, setExpanded] = useState(false);
  const descRef = useRef<HTMLParagraphElement>(null);
  const [clamped, setClamped] = useState(false);

  // Detect whether the description overflows two lines (only while collapsed).
  useEffect(() => {
    if (expanded) return;
    const el = descRef.current;
    if (el) setClamped(el.scrollHeight - el.clientHeight > 1);
  }, [description, expanded]);

  return (
    <Card
      padding="md"
      className="group relative flex flex-col min-h-[232px] cursor-pointer overflow-hidden rounded-2xl border-border-subtle-01 transition-all duration-200 hover:border-interactive/40 hover:shadow-elevation-2 hover:-translate-y-0.5"
    >
      {/* Brand wash — blooms on hover */}
      <div
        className="pointer-events-none absolute -top-16 -right-16 h-40 w-40 rounded-full bg-interactive/10 blur-3xl opacity-0 transition-opacity duration-300 group-hover:opacity-100"
        aria-hidden="true"
      />

      {/* Header */}
      <div className="relative flex items-start gap-3 mb-3">
        <div className="relative flex-shrink-0">
          <div
            className="absolute inset-0 rounded-xl bg-interactive/25 blur-md opacity-0 transition-opacity duration-300 group-hover:opacity-100"
            aria-hidden="true"
          />
          <div className="relative w-10 h-10 flex items-center justify-center rounded-xl bg-gradient-to-br from-[#4589ff] to-[#0043ce] ring-1 ring-white/10 shadow-elevation-1">
            <svg width="20" height="20" viewBox="0 0 32 32" fill="white">
              <path d="M30 8h-4.1c-.5-2.3-2.5-4-4.9-4s-4.4 1.7-4.9 4H2v2h14.1c.5 2.3 2.5 4 4.9 4s4.4-1.7 4.9-4H30V8zM21 12c-1.7 0-3-1.3-3-3s1.3-3 3-3 3 1.3 3 3-1.3 3-3 3zM2 24h4.1c.5 2.3 2.5 4 4.9 4s4.4-1.7 4.9-4H30v-2H15.9c-.5-2.3-2.5-4-4.9-4s-4.4 1.7-4.9 4H2v2zm9-4c1.7 0 3 1.3 3 3s-1.3 3-3 3-3-1.3-3-3 1.3-3 3-3z"/>
            </svg>
          </div>
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-heading-02 font-semibold text-text-primary leading-tight">
            {name}
          </h3>
          <p className="text-caption-01 text-text-helper">
            by {author}{team && ` · ${team}`}{version && ` · v${version}`}
          </p>
        </div>
      </div>

      {/* Description — reserves two lines; longer text collapses behind a toggle */}
      <div className="relative mb-3">
        <p
          ref={descRef}
          className={`text-body-short-01 text-text-secondary ${expanded ? '' : 'line-clamp-2 min-h-[2.25rem]'}`}
        >
          {description}
        </p>
        {(clamped || expanded) && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              setExpanded((v) => !v);
            }}
            className="mt-1 inline-flex items-center gap-0.5 text-caption-01 font-semibold text-link-primary hover:underline"
          >
            {expanded ? '접기' : '… 더보기'}
          </button>
        )}
      </div>

      {/* Spacer keeps the action row pinned to the bottom regardless of text length */}
      <div className="flex-1" />

      {/* Tags */}
      <div className="relative flex flex-wrap gap-1.5 mb-3">
        {tags.map((tag) => (
          <Tag key={tag} type="gray" size="sm">
            {tag}
          </Tag>
        ))}
      </div>

      {/* Stats */}
      <div className="relative flex items-center gap-3.5 mb-3.5 text-caption-01 text-text-secondary">
        <span className="flex items-center gap-1">
          <svg width="13" height="13" viewBox="0 0 32 32" fill="currentColor">
            <path d="M26 24v4H6v-4H4v4a2 2 0 002 2h20a2 2 0 002-2v-4zM26 14l-1.41-1.41L17 20.17V2h-2v18.17l-7.59-7.58L6 14l10 10 10-10z"/>
          </svg>
          {downloads.toLocaleString()}
        </span>
        <span className="flex items-center gap-1">
          <svg width="13" height="13" viewBox="0 0 32 32" fill="currentColor">
            <path d="M16 6.52l2.76 5.58.46 1 1.1.16 6.16.89-4.46 4.35-.8.77.19 1.09 1.05 6.13-5.51-2.89L16 23l-.95.5-5.51 2.89 1.05-6.13.19-1.09-.8-.77-4.46-4.35 6.16-.89 1.1-.16.46-1L16 6.52M16 2l-4.55 9.22-10.17 1.47 7.36 7.18L6.9 30l9.1-4.78L25.1 30l-1.74-10.13 7.36-7.18-10.17-1.47L16 2z"/>
          </svg>
          {rating}
        </span>
        {points !== undefined && (
          <span className="ml-auto flex items-center gap-1 text-interactive font-mono">
            <svg width="13" height="13" viewBox="0 0 32 32" fill="currentColor">
              <path d="M22.45 6h-7.88l-1.94-4.68A1 1 0 0011.7 1H3.53a1 1 0 00-.93.63L.1 8.31a1 1 0 00.93 1.37h4.67l-2.5 6.54a1 1 0 00.55 1.3 1 1 0 001.3-.55l2.5-6.54h4.67l-2.5 6.54a1 1 0 00.55 1.3 1 1 0 001.3-.55l2.5-6.54h4.67l-2.5 6.54a1 1 0 00.55 1.3 1 1 0 001.3-.55l2.5-6.54h4.67a1 1 0 00.93-1.37l-2.5-6.68A1 1 0 0022.45 6z"/>
            </svg>
            {points.toLocaleString()} BP
          </span>
        )}
      </div>

      {/* Actions */}
      <div className="relative flex gap-2">
        <button
          onClick={(e) => {
            e.stopPropagation();
            onInstall();
          }}
          disabled={installed}
          className={`
            flex-1 flex items-center justify-center gap-1.5 py-2.5 border-0 rounded-xl
            text-body-short-01 font-semibold transition-all
            ${installed
              ? 'bg-layer-02 text-text-secondary cursor-default ring-1 ring-inset ring-border-subtle-01'
              : 'bg-gradient-to-r from-[#4589ff] to-[#0043ce] text-white shadow-elevation-1 hover:from-[#0f62fe] hover:to-[#002d9c] hover:shadow-elevation-2 cursor-pointer'
            }
          `}
        >
          <svg width="15" height="15" viewBox="0 0 32 32" fill="currentColor">
            {installed ? (
              <path d="M16 2C8.3 2 2 8.3 2 16s6.3 14 14 14 14-6.3 14-14S23.7 2 16 2zm-2 19.6l-5-5 1.4-1.4 3.6 3.6 7.6-7.6 1.4 1.4-9 9z"/>
            ) : (
              <path d="M26 24v4H6v-4H4v4a2 2 0 002 2h20a2 2 0 002-2v-4zM26 14l-1.41-1.41L17 20.17V2h-2v18.17l-7.59-7.58L6 14l10 10 10-10z"/>
            )}
          </svg>
          {installed ? '설치됨' : '다운로드'}
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onSponsor();
          }}
          className="flex-shrink-0 w-[110px] flex items-center justify-center gap-1.5 py-2.5 bg-layer-02 text-text-primary ring-1 ring-inset ring-border-subtle-01 hover:bg-layer-hover-01 hover:ring-interactive/40 hover:text-interactive border-0 rounded-xl text-body-short-01 font-semibold transition-all cursor-pointer"
        >
          <svg width="15" height="15" viewBox="0 0 32 32" fill="currentColor">
            <path d="M22.45 6h-7.88l-1.94-4.68A1 1 0 0011.7 1H3.53a1 1 0 00-.93.63L.1 8.31a1 1 0 00.93 1.37h4.67l-2.5 6.54a1 1 0 00.55 1.3 1 1 0 001.3-.55l2.5-6.54h4.67l-2.5 6.54a1 1 0 00.55 1.3 1 1 0 001.3-.55l2.5-6.54h4.67l-2.5 6.54a1 1 0 00.55 1.3 1 1 0 001.3-.55l2.5-6.54h4.67a1 1 0 00.93-1.37l-2.5-6.68A1 1 0 0022.45 6z"/>
          </svg>
          후원
        </button>
      </div>
    </Card>
  );
};

// Made with Bob
