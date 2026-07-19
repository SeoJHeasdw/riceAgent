import type { Skill as MarketSkill, SkillFile, Plugin as MarketPlugin, PlatformArtifact } from './types';
import type { Skill as OwnedSkill, SkillDetail, MyPlugin } from './learningData';
import { getSkillFiles } from './skillFiles';
import i18n from './i18n';

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

/**
 * 설치된 마켓 스킬의 안정적 소유 id.
 * platform artifact는 runtime에 `<artifact-name>.md`로 설치되어 GET /skills의 id도
 * artifact name이 된다. 같은 id를 쓰면 설치 직후 UI와 runtime hydrate가 갈라지지 않는다.
 */
export const installedSkillId = (marketId: string) =>
  marketId.startsWith('platform-') ? marketId.slice('platform-'.length) : `inst-${marketId}`;

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
 * 마켓 스킬을 "나의 스킬"(소유 인스턴스)로 materialize 한다.
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
    trend: 'trend_just_now',
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
    createdFrom: 'created_from_market',
    lastUsed: 'last_used_never',
    shared: false,
    rules: [],
    versions: [
      {
        ver,
        when: new Date().toISOString(),
        // stable code — 렌더 시 renderVersionNote('noteInstalled', t, noteParams)로 번역
        note: 'noteInstalled',
        noteParams: { author, ver },
        current: true,
        author,
        rules: [],
      },
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
    // '나'는 렌더 시 t()로 표시해야 하나 ownedToMarketSkill은 순수 변환 함수라 t()에 접근하지 않음.
    // caller(MySkillsScreen, LearningContext)가 표시 시 own author를 재정의한다.
    // stable sentinel: 비어 있으면 호출처가 현재 사용자 이름으로 채운다.
    author: 'me',
    // stable code — 렌더 시 PluginMarketScreen/SkillMarketScreen이 t()로 번역
    category: 'productivity',
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
  // 헤더는 우리 코드가 정적으로 박아 넣는 구조 텍스트라 현재 UI 언어를 따른다
  // (SkillAuthorModal.generatedDef 와 동일 원칙 — docs/i18n.md 참고).
  const stepsHeading = i18n.t('skills:bridge.stepsHeading');
  const rulesHeading = i18n.t('skills:bridge.rulesHeading');
  const stepsBlock = steps.length
    ? `\n${stepsHeading}\n\n${steps.map((s, i) => `${i + 1}. **${s.label}** — ${s.desc} \`(${s.tool})\``).join('\n')}\n`
    : '';
  const rulesBlock = rules.length
    ? `\n${rulesHeading}\n\n${rules.map((r) => `- ${r.text}`).join('\n')}\n`
    : '';
  const content = `---
name: ${skill.command ?? skill.id}
description: ${detail.summary}
version: ${skill.ver}
source: ${i18n.t('skills:bridge.autoComposedSource')}
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

// ── 플러그인 브리지 (마켓 Plugin → 나의 플러그인) ──────────────────────────────
// 스킬의 marketSkillToOwned 와 대칭. 설치 시 마켓 Plugin을 "나의 플러그인"(MyPlugin)으로
// materialize 해 MyPluginsScreen에 즉시 뜨게 한다. marketId 기준 멱등.

/** 설치된 마켓 플러그인의 안정적 소유 id — runtime installed_artifacts id와 맞춘다. */
export const installedPluginId = (marketId: string) =>
  marketId.startsWith('platform-plugin:')
    ? marketId.slice('platform-'.length)
    : marketId.startsWith('platform-') ? `plugin:${marketId.slice('platform-'.length)}` : `inst-${marketId}`;

// ── platform(rice-platform) artifact → 마켓 타입 변환 ────────────────────────
// 카탈로그 응답(PlatformArtifact)을 앱 도메인(Skill·Plugin)으로. MarketplaceContext(카탈로그
// 로드)와 LearningContext(설치목록 복원)가 공유한다.

/** platform skill artifact → 마켓 Skill. files는 모달에서 lazy load 하므로 비움. */
export function platformArtifactToSkill(a: PlatformArtifact): MarketSkill {
  return {
    id: `platform-${a.id}`,
    name: a.display_name || a.name,
    description: a.description,
    version: a.version,
    author: a.author_name || 'unknown',
    team: a.author_team || undefined,
    category: a.category,
    tags: a.tags,
    downloads: a.download_count,
    rating: 0,
    points: 0,
    installed: false,
    enabled: false,
    files: undefined,
  };
}

/**
 * platform plugin artifact → 마켓 Plugin.
 * plugin = skills 묶음 + 요구 connector (CONTRACTS §2.2). 카탈로그 메타로 구성:
 * skillCount=skill_files 수, connectors=.mcp.json 상세(없으면 requires 이름), gains=태그.
 */
export function platformArtifactToPlugin(a: PlatformArtifact): MarketPlugin {
  // skill_files("skills/<name>/SKILL.md") → 읽기 쉬운 스킬명. 내용(desc)은 모달에서 lazy load.
  const skillNames = a.skill_files.map((f) =>
    f.replace(/^skills\//, '').replace(/\/SKILL\.md$/i, '').replace(/\.md$/i, ''),
  );
  const pluginSkills = a.plugin_skills?.length
    ? a.plugin_skills.map((s) => ({
        command: s.command || s.name,
        desc: s.description || '',
      }))
    : skillNames.map((command) => ({ command, desc: '' }));
  const connectors = a.connectors?.length
    ? a.connectors.map((c) => ({ name: c.title || c.name, desc: c.description || undefined }))
    : (a.requires?.connectors ?? []).map((c) => ({ name: c }));
  // examples가 없으면 빈 배열로 — useCases fallback에 한글 하드코딩을 피한다.
  // MyPluginsScreen/ArtifactDetailModal은 빈 배열 시 별도 안내(examplesEmpty)를 표시한다.
  const useCases = (a.examples && a.examples.length) ? a.examples : [];
  return {
    id: `platform-${a.id}`,
    name: a.display_name || a.name,
    description: a.description,
    version: a.version,
    author: a.author_name || 'unknown',
    provider: a.author_team || a.author_name || 'community',
    category: a.category,
    tags: a.tags,
    skillCount: pluginSkills.length || a.skill_files.length,
    gains: a.tags.length ? a.tags.slice(0, 4).join(' · ') : a.category,
    useCases,
    skills: pluginSkills,
    connectors,
    installed: false,
    enabled: false,
  };
}

/**
 * 런타임 GET /plugins(PluginInfo) → 나의 플러그인(MyPlugin) 폴백 변환.
 * 카탈로그에 매칭이 없을 때만 사용(설치는 됐으나 마켓엔 없는 경우). 풍부한 복원은
 * LearningContext가 platform 카탈로그와 교차참조해 처리한다.
 */
export function runtimePluginToMyPlugin(p: {
  id: string;
  name: string;
  version: string;
  enabled: boolean;
  required_connectors?: string[];
  description?: string;
  skill_names?: string[];
  source?: string;
  author?: string;
  risk_flags?: string[];
  agent_names?: string[];
}): MyPlugin {
  const builtin = p.source === 'builtin';
  return {
    id: p.id,
    name: p.name,
    icon: 'plug',
    enabled: p.enabled,
    builtin,
    // stable code — 렌더 시 renderPluginSource()로 번역
    source: builtin ? 'plugin_source_builtin' : 'plugin_source_runtime',
    version: p.version,
    author: p.author ?? '',
    updated: builtin ? 'plugin_updated_builtin' : 'plugin_updated_installed',
    summary: p.description || '',
    skills: (p.skill_names ?? []).map((name) => ({ name, desc: '' })),
    knowledge: [],
    permissions: (p.required_connectors ?? []).map((c) => ({ name: c, mode: 'needs_connection' as const })),
    examples: [],
    riskFlags: p.risk_flags ?? [],
    agentNames: p.agent_names ?? [],
  };
}

export function marketPluginToMyPlugin(p: MarketPlugin): MyPlugin {
  return {
    id: installedPluginId(p.id),
    name: p.name,
    icon: 'plug',
    enabled: true,
    // stable code — 렌더 시 renderPluginSource()로 번역 (provider는 보간 파라미터)
    source: `plugin_source_market:${p.provider}`,
    version: p.version,
    author: p.author,
    updated: 'plugin_updated_just_installed',
    summary: p.description,
    // 마켓 PluginSkill { command, desc } → 내 PluginSkill { name, desc }
    skills: (p.skills ?? []).map((s) => ({ name: s.command, desc: s.desc })),
    knowledge: (p.knowledge ?? []).map((k) => ({ name: k.name, desc: k.desc, meta: k.meta ?? '' })),
    // 요구 connector를 권한 항목으로 표시 (연결 필요 상태)
    permissions: (p.connectors ?? []).map((c) => ({ name: c.name, mode: 'needs_connection' as const })),
    examples: p.useCases ?? [],
  };
}

// Made with Bob
