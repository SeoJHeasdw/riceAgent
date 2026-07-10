import React from 'react';
import { Card, Icon } from '../ui';

/**
 * SkillCard - Skill marketplace card.
 *
 * 조용한 카드 패턴: 카드 전체가 클릭 대상(상세 모달)이고, 액션은 우상단의
 * 컴팩트한 "받기" pill 하나로 좁힌다 — 카드마다 대형 CTA를 반복하지 않는다.
 * 실제 설치는 상세 모달에서 확정되므로 pill 도 모달을 여는 진입점이다.
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

const nf = (n: number) => n.toLocaleString();

/** 카드 푸터 스탯용 12px 스트로크 아이콘 (공용 Icon 세트에 없는 글리프) */
const StatIcon: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <svg
    width="12"
    height="12"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden
  >
    {children}
  </svg>
);

const STAR = <path d="M12 2.5l2.9 5.9 6.5.95-4.7 4.6 1.1 6.5L12 17.4l-5.8 3.05 1.1-6.5-4.7-4.6 6.5-.95L12 2.5z" />;
const ZAP = <path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" />;

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
  return (
    <Card
      padding="md"
      className={`group relative flex flex-col min-h-[176px] cursor-pointer overflow-hidden transition-colors duration-150 ${
        installed
          ? 'skill-card--installed'
          : 'skill-card--idle'
      }`}
      onClick={onInstall}
    >
      <span
        aria-hidden="true"
        className={`absolute left-0 top-0 h-full w-0.5 bg-blue-60 transition-opacity ${
          installed ? 'opacity-70' : 'opacity-0 group-hover:opacity-50'
        }`}
      />
      {/* Header — 아이콘 · 이름/출처 · 받기 pill */}
      <div className="relative flex items-start gap-3">
        <div
          className={`flex-shrink-0 w-10 h-10 flex items-center justify-center rounded-lg border ${
            installed
              ? 'bg-blue-60/14 border-blue-50/30 text-blue-30'
              : 'bg-blue-60/12 border-blue-50/25 text-blue-40'
          }`}
        >
          <Icon name="skill" size={20} />
        </div>
        <div className="flex-1 min-w-0 pt-0.5">
          <h3 className="text-heading-02 font-semibold text-text-primary leading-tight truncate">
            <button
              type="button"
              onClick={(event) => { event.stopPropagation(); onInstall(); }}
              className="max-w-full truncate text-left rounded-sm hover:text-link-primary transition-colors duration-150"
            >
              {name}
            </button>
          </h3>
          <p className="text-caption-01 text-text-helper truncate">
            by {author}{team && ` · ${team}`}{version && ` · v${version}`}
          </p>
        </div>
        <button
          onClick={(e) => {
            e.stopPropagation();
            if (!installed) onInstall();
          }}
          disabled={installed}
          className={`flex-shrink-0 inline-flex items-center gap-1 h-7 rounded-full text-caption-01 font-semibold transition-colors ${
            installed
              ? 'px-3 bg-blue-60/12 text-blue-30 ring-1 ring-inset ring-blue-50/25 cursor-default'
              : 'px-3 bg-transparent text-text-secondary ring-1 ring-inset ring-border-strong-01 hover:bg-blue-60/10 hover:text-blue-30 hover:ring-blue-50/35 cursor-pointer'
          }`}
        >
          {installed && <Icon name="check" size={12} />}
          {installed ? '설치됨' : '받기'}
        </button>
      </div>

      {/* Description — 2줄 고정 클램프(전문은 상세 모달에서) */}
      <p className="relative text-body-short-01 text-text-secondary line-clamp-2 min-h-[2.25rem] mt-3 leading-5">
        {description}
      </p>

      <div className="flex-1" />

      {/* Footer — 스탯 · 태그 · 후원 (0값 스탯은 숨겨 깨져 보이지 않게) */}
      <div className="relative flex items-center gap-2 mt-3 text-caption-01 text-text-helper">
        <span className="flex items-center gap-1 flex-shrink-0">
          <Icon name="download" size={12} />
          {nf(downloads)}
        </span>
        {rating > 0 && (
          <span className="flex items-center gap-1 flex-shrink-0">
            <StatIcon>{STAR}</StatIcon>
            {rating}
          </span>
        )}
        {points !== undefined && points > 0 && (
          <span className="flex items-center gap-1 flex-shrink-0 text-blue-30 font-mono">
            <StatIcon>{ZAP}</StatIcon>
            {nf(points)} BP
          </span>
        )}
      </div>

      <div className="relative flex items-center gap-1.5 mt-2 min-w-0">
        <div className="flex items-center gap-1.5 min-w-0 flex-1 overflow-hidden">
          {tags.slice(0, 2).map((t) => (
            <span
              key={t}
              className="min-w-0 truncate rounded-full border border-border-subtle-01 bg-layer-02/65 px-2 py-0.5 text-caption-01 text-text-secondary"
            >
              #{t}
            </span>
          ))}
          {tags.length > 2 && (
            <span className="flex-shrink-0 text-caption-01 text-text-helper">+{tags.length - 2}</span>
          )}
        </div>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onSponsor();
          }}
          title="블루 포인트로 후원하기"
          className="flex-shrink-0 inline-flex items-center gap-1 h-6 px-2 -mr-1 rounded-full text-caption-01 font-medium text-text-helper hover:text-blue-30 hover:bg-blue-60/10 transition-colors"
        >
          <StatIcon>{ZAP}</StatIcon>
          후원
        </button>
      </div>
    </Card>
  );
};

// Made with Bob
