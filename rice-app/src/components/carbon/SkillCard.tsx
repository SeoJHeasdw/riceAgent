import React from 'react';
import { Card } from '../ui';

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
 * 카테고리별 아이콘 타일 액센트 — 모든 카드가 똑같은 파란 사각형으로 보이는
 * 단조로움을 깬다. 첫 매칭 태그의 계열을 따르고, 못 찾으면 이름 해시로 고정 배정
 * (같은 스킬은 항상 같은 색).
 */
const TILE_ACCENTS = [
  { grad: 'from-[#0f62fe] to-[#4589ff]', wash: 'bg-blue-50/25' }, // blue — 데이터·개발
  { grad: 'from-[#007d79] to-[#08bdba]', wash: 'bg-teal-40/25' }, // teal — 자동화·연동
  { grad: 'from-[#8a3ffc] to-[#a56eff]', wash: 'bg-purple-50/25' }, // purple — 커뮤니케이션·창작
  { grad: 'from-[#d02670] to-[#ee5396]', wash: 'bg-[#ee5396]/25' }, // magenta — 생산성·기타
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
      className="group relative flex flex-col min-h-[164px] cursor-pointer overflow-hidden rounded-2xl border-border-subtle-01 transition-all duration-200 hover:border-interactive/40 hover:shadow-elevation-2 hover:-translate-y-0.5"
    >
      {/* Brand wash — blooms on hover */}
      <div
        className="pointer-events-none absolute -top-16 -right-16 h-40 w-40 rounded-full bg-interactive/10 blur-3xl opacity-0 transition-opacity duration-300 group-hover:opacity-100"
        aria-hidden="true"
      />

      {/* Header — 아이콘 · 이름/출처 · 받기 pill */}
      <div className="relative flex items-start gap-3">
        <div className="relative flex-shrink-0">
          <div
            className={`absolute inset-0 rounded-xl ${accent.wash} blur-md opacity-0 transition-opacity duration-300 group-hover:opacity-100`}
            aria-hidden="true"
          />
          <div className={`relative w-10 h-10 flex items-center justify-center rounded-xl bg-gradient-to-br ${accent.grad} ring-1 ring-white/10 shadow-elevation-1`}>
            <svg width="20" height="20" viewBox="0 0 32 32" fill="white">
              <path d="M30 8h-4.1c-.5-2.3-2.5-4-4.9-4s-4.4 1.7-4.9 4H2v2h14.1c.5 2.3 2.5 4 4.9 4s4.4-1.7 4.9-4H30V8zM21 12c-1.7 0-3-1.3-3-3s1.3-3 3-3 3 1.3 3 3-1.3 3-3 3zM2 24h4.1c.5 2.3 2.5 4 4.9 4s4.4-1.7 4.9-4H30v-2H15.9c-.5-2.3-2.5-4-4.9-4s-4.4 1.7-4.9 4H2v2zm9-4c1.7 0 3 1.3 3 3s-1.3 3-3 3-3-1.3-3-3 1.3-3 3-3z"/>
            </svg>
          </div>
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
          className={`flex-shrink-0 inline-flex items-center gap-1 h-7 rounded-full text-caption-01 font-semibold transition-all ${
            installed
              ? 'px-3 bg-layer-02 text-text-helper ring-1 ring-inset ring-border-subtle-02 cursor-default'
              : 'px-3.5 bg-interactive/15 text-link-primary group-hover:bg-interactive group-hover:text-white active:scale-95 cursor-pointer'
          }`}
        >
          {installed && (
            <svg width="12" height="12" viewBox="0 0 32 32" fill="currentColor">
              <path d="M13 24l-9-9 1.41-1.41L13 21.17 26.59 7.59 28 9l-15 15z"/>
            </svg>
          )}
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
          <svg width="12" height="12" viewBox="0 0 32 32" fill="currentColor">
            <path d="M26 24v4H6v-4H4v4a2 2 0 002 2h20a2 2 0 002-2v-4zM26 14l-1.41-1.41L17 20.17V2h-2v18.17l-7.59-7.58L6 14l10 10 10-10z"/>
          </svg>
          {nf(downloads)}
        </span>
        {rating > 0 && (
          <span className="flex items-center gap-1 flex-shrink-0">
            <svg width="12" height="12" viewBox="0 0 32 32" fill="currentColor">
              <path d="M16 2l4.55 9.22 10.17 1.47-7.36 7.18L25.1 30 16 25.22 6.9 30l1.74-10.13-7.36-7.18 10.17-1.47L16 2z"/>
            </svg>
            {rating}
          </span>
        )}
        {points !== undefined && points > 0 && (
          <span className="flex items-center gap-1 flex-shrink-0 text-link-primary font-mono">
            <svg width="12" height="12" viewBox="0 0 32 32" fill="currentColor">
              <path d="M22.45 6h-7.88l-1.94-4.68A1 1 0 0011.7 1H3.53a1 1 0 00-.93.63L.1 8.31a1 1 0 00.93 1.37h4.67l-2.5 6.54a1 1 0 00.55 1.3 1 1 0 001.3-.55l2.5-6.54h4.67l-2.5 6.54a1 1 0 00.55 1.3 1 1 0 001.3-.55l2.5-6.54h4.67l-2.5 6.54a1 1 0 00.55 1.3 1 1 0 001.3-.55l2.5-6.54h4.67a1 1 0 00.93-1.37l-2.5-6.68A1 1 0 0022.45 6z"/>
            </svg>
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
          className="flex-shrink-0 inline-flex items-center gap-1 h-6 px-2 -mr-1 rounded-full text-caption-01 font-medium text-text-helper hover:text-link-primary hover:bg-interactive/10 transition-colors"
        >
          <svg width="12" height="12" viewBox="0 0 32 32" fill="currentColor">
            <path d="M22.45 6h-7.88l-1.94-4.68A1 1 0 0011.7 1H3.53a1 1 0 00-.93.63L.1 8.31a1 1 0 00.93 1.37h4.67l-2.5 6.54a1 1 0 00.55 1.3 1 1 0 001.3-.55l2.5-6.54h4.67l-2.5 6.54a1 1 0 00.55 1.3 1 1 0 001.3-.55l2.5-6.54h4.67l-2.5 6.54a1 1 0 00.55 1.3 1 1 0 001.3-.55l2.5-6.54h4.67a1 1 0 00.93-1.37l-2.5-6.68A1 1 0 0022.45 6z"/>
          </svg>
          후원
        </button>
      </div>
    </Card>
  );
};

// Made with Bob
