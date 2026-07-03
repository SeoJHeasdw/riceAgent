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

/**
 * 카테고리별 아이콘 틴트 — 모든 카드가 똑같은 파란 사각형으로 보이는
 * 단조로움을 깬다. 타일 자체는 중립(layer-02 + border)이고 아이콘 색만 다르다.
 * 첫 매칭 태그의 계열을 따르고, 못 찾으면 이름 해시로 고정 배정
 * (같은 스킬은 항상 같은 색).
 */
const TILE_ACCENTS = [
  { tint: 'text-blue-50' }, // blue — 데이터·개발
  { tint: 'text-teal-40' }, // teal — 자동화·연동
  { tint: 'text-purple-50' }, // purple — 커뮤니케이션·창작
  { tint: 'text-magenta-50' }, // magenta — 생산성·기타
] as const;

const TILE_KEYWORDS: [RegExp, number][] = [
  [/data|analysis|csv|excel|statistic|dev|code|git|vcs/i, 0],
  [/automation|workflow|integration|tool/i, 1],
  [/communication|writing|brevity|art|creative|visualization/i, 2],
  [/productivity|token/i, 3],
];

const tileAccentFor = (tags: string[], name: string) => {
  for (const t of tags) {
    const hit = TILE_KEYWORDS.find(([re]) => re.test(t));
    if (hit) return TILE_ACCENTS[hit[1]];
  }
  const hash = [...name].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) | 0, 0);
  return TILE_ACCENTS[Math.abs(hash) % TILE_ACCENTS.length];
};

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
  const accent = tileAccentFor(tags, name);

  return (
    <Card
      padding="md"
      className="group relative flex flex-col min-h-[164px] cursor-pointer overflow-hidden border-border-subtle-01 transition-colors duration-150 hover:border-border-strong-01"
    >
      {/* Header — 아이콘 · 이름/출처 · 받기 pill */}
      <div className="relative flex items-start gap-3">
        <div className={`flex-shrink-0 w-10 h-10 flex items-center justify-center rounded-lg bg-layer-02 border border-border-subtle-02 ${accent.tint}`}>
          <Icon name="skill" size={20} />
        </div>
        <div className="flex-1 min-w-0 pt-0.5">
          <h3 className="text-heading-02 font-semibold text-text-primary leading-tight truncate">
            {name}
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
              ? 'px-3 bg-layer-02 text-text-helper ring-1 ring-inset ring-border-subtle-02 cursor-default'
              : 'px-3 bg-transparent text-text-secondary ring-1 ring-inset ring-border-strong-01 hover:bg-layer-hover-01 hover:text-text-primary cursor-pointer'
          }`}
        >
          {installed && <Icon name="check" size={12} />}
          {installed ? '설치됨' : '받기'}
        </button>
      </div>

      {/* Description — 2줄 고정 클램프(전문은 상세 모달에서) */}
      <p className="relative text-body-short-01 text-text-secondary line-clamp-2 min-h-[2.25rem] mt-3">
        {description}
      </p>

      <div className="flex-1" />

      {/* Footer — 스탯 · 태그 · 후원 (0값 스탯은 숨겨 깨져 보이지 않게) */}
      <div className="relative flex items-center gap-3 mt-3 text-caption-01 text-text-helper">
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
          <span className="flex items-center gap-1 flex-shrink-0 text-link-primary font-mono">
            <StatIcon>{ZAP}</StatIcon>
            {nf(points)} BP
          </span>
        )}
        <span className="flex-1 min-w-0 truncate text-text-placeholder">
          {tags.slice(0, 2).map((t) => `#${t}`).join(' ')}
          {tags.length > 2 && `  +${tags.length - 2}`}
        </span>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onSponsor();
          }}
          title="블루 포인트로 후원하기"
          className="flex-shrink-0 inline-flex items-center gap-1 h-6 px-2 -mr-1 rounded-full text-caption-01 font-medium text-text-helper hover:text-text-primary hover:bg-layer-hover-01 transition-colors"
        >
          <StatIcon>{ZAP}</StatIcon>
          후원
        </button>
      </div>
    </Card>
  );
};

// Made with Bob
