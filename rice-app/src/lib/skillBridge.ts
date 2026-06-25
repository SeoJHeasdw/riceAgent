import type { Skill as MarketSkill, SkillFile } from './types';
import type { Skill as OwnedSkill, SkillDetail } from './learningData';
import { getSkillFiles } from './skillFiles';

/**
 * skillBridge — the seam between the two "skill" worlds.
 *
 *   마켓 Skill (types.ts)  = 배포 패키지 (downloads·rating·BP·files)
 *   소유 Skill (learningData) = 소유 인스턴스 (origin·rules·versions·health)
 *
 * 두 타입을 하나로 머지하지 않고, 공유 코어(SkillFile + 식별자)를 통해 변환한다.
 *   install = 마켓 → 소유 (marketSkillToOwned) · fork 지점 기록(origin:installed).
 * 또 소유 스킬의 "소스 뷰"가 항상 보여줄 파일을 제공한다(ownedSkillFiles).
 */

/** 설치된 마켓 스킬의 안정적 소유 id — marketId 기준이라 재설치해도 중복되지 않는다. */
export const installedSkillId = (marketId: string) => `inst-${marketId}`;

/** 마켓 semver("3.0.1") → 학습 버전 표기("v3"). 메이저만 취한다. */
const toLearnVer = (v: string): string => {
  const m = v.match(/\d+/);
  return `v${m ? m[0] : '1'}`;
};

/** 슬래시 커맨드 id — 영문/숫자만 추려 만들고, 비면 marketId로 대체. */
const cmdFromMarket = (m: MarketSkill): string => {
  const slug = m.name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/(^_|_$)/g, '');
  return slug || m.id.replace(/-/g, '_');
};

/**
 * 마켓 스킬을 "내 스킬"(소유 인스턴스)로 materialize 한다.
 * 설치 직후라 학습된 규칙은 비어 있고(쓰면서 쌓임), files는 실제 번들을 그대로 들고 온다.
 * upstream.installedVer = latestVer = 설치 버전 → 처음엔 "최신"(업데이트 없음).
 */
export function marketSkillToOwned(m: MarketSkill): { skill: OwnedSkill; detail: SkillDetail } {
  const ver = toLearnVer(m.version);
  const author = `${m.author}${m.team ? ` · ${m.team}` : ''}`;

  const skill: OwnedSkill = {
    id: installedSkillId(m.id),
    name: m.name,
    ver,
    uses: 0,
    trend: '방금 설치',
    enabled: true,
    shared: false,
    patches: 0,
    command: cmdFromMarket(m),
    state: 'active',
    idleDays: 0,
    origin: 'installed',
    upstream: { author: m.author, team: m.team, marketId: m.id, installedVer: ver, latestVer: ver },
  };

  const detail: SkillDetail = {
    summary: m.description,
    createdFrom: `Skill Market · ${m.author}`,
    lastUsed: '아직 없음',
    shared: false,
    rules: [],
    versions: [
      { ver, when: '방금', note: `${author} ${ver} 설치 — fork 지점`, current: true, author, rules: [] },
    ],
    usage: [],
    permissions: [],
    files: getSkillFiles(m),
  };

  return { skill, detail };
}

/** 소유 스킬을 공개했을 때의 마켓 id — ownedId 기준이라 재공개해도 중복되지 않는다. */
export const publishedMarketId = (ownedId: string) => `pub-${ownedId}`;

/**
 * 소유 스킬 → 마켓 Skill 로 materialize (publish/재공유 방향, install의 역).
 * 설치형(다운로드)을 재공유하면 origin 작가를 forkedFrom 으로 남겨 attribution을 유지한다.
 */
export function ownedToMarketSkill(skill: OwnedSkill, detail: SkillDetail): MarketSkill {
  const origin = skill.origin ?? 'learned';
  const { files } = ownedSkillFiles(skill, detail);
  return {
    id: publishedMarketId(skill.id),
    name: skill.name,
    description: detail.summary,
    version: skill.ver,
    author: '나',
    category: '생산성',
    tags: [],
    downloads: 0,
    rating: 0,
    points: 0,
    installed: true,
    enabled: true,
    files,
    ...(origin === 'installed' && skill.upstream
      ? { forkedFrom: { author: skill.upstream.author, team: skill.upstream.team } }
      : {}),
  };
}

/** 학습 데이터로 SKILL.md 한 장을 합성 — 소스 뷰에서 파일이 없는 학습 스킬에 쓴다. */
function synthesizeSkillMd(skill: OwnedSkill, detail: SkillDetail): SkillFile {
  const steps = detail.steps ?? [];
  const rules = detail.rules.filter((r) => r.on);
  const stepsBlock = steps.length
    ? `\n## 절차\n\n${steps.map((s, i) => `${i + 1}. **${s.label}** — ${s.desc} \`(${s.tool})\``).join('\n')}\n`
    : '';
  const rulesBlock = rules.length
    ? `\n## 학습한 규칙\n\n${rules.map((r) => `- ${r.text}`).join('\n')}\n`
    : '';
  const content = `---
name: ${skill.command ?? skill.id}
description: ${detail.summary}
version: ${skill.ver}
source: 대화 학습에서 자동 구성
---

# ${skill.name}

${detail.summary}
${stepsBlock}${rulesBlock}`;
  return { path: 'SKILL.md', language: 'markdown', content };
}

/**
 * 소유 스킬이 소스 뷰에서 보여줄 파일들.
 * 설치·제작 스킬은 실제 파일(detail.files)을, 학습 스킬은 합성된 SKILL.md를 돌려준다.
 * 두 번째 값(synthesized)은 "이건 자동 구성된 정의"라는 안내를 띄울지 판단하는 데 쓴다.
 */
export function ownedSkillFiles(
  skill: OwnedSkill,
  detail: SkillDetail,
): { files: SkillFile[]; synthesized: boolean } {
  if (detail.files && detail.files.length) return { files: detail.files, synthesized: false };
  return { files: [synthesizeSkillMd(skill, detail)], synthesized: true };
}

export { toLearnVer };

// Made with Bob
