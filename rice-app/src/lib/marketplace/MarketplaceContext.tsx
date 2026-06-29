import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Skill, Plugin } from '../types';
import { platformArtifactToSkill, platformArtifactToPlugin } from '../skillBridge';
import { platform, api } from '../api';

/**
 * MarketplaceContext — single source of truth for the skill/plugin market,
 * shared across the market screens AND "내 스킬" (so publishing an owned skill
 * actually surfaces it in the market). The install direction (market → 내 스킬)
 * lives in LearningContext; this owns the catalog + publish/unpublish.
 */

/** Form payload produced by the SkillAuthorModal when a user publishes a skill. */
export interface SkillDraft {
  name: string;
  description: string;
  category: string;
  tags: string[];
  version: string;
  team?: string;
  /** Authored SKILL.md content (the skill definition). */
  definition: string;
}

export interface MarketplaceContextValue {
  skills: Skill[];
  plugins: Plugin[];
  isLoading: boolean;
  /** 카탈로그 첫 로드 진행 중 (스켈레톤 vs 빈상태 구분용) */
  catalogLoading: boolean;
  searchQuery: string;
  setSearchQuery: (query: string) => void;
  sortBy: string;
  setSortBy: (sort: string) => void;
  selectedTags: string[];
  toggleTag: (tag: string) => void;
  installArtifact: (id: string, type: 'skill' | 'plugin') => Promise<void>;
  uninstallArtifact: (id: string, type: 'skill' | 'plugin') => Promise<void>;
  sponsorSkill: (id: string, amount: number) => Promise<void>;
  /** Publish a user-authored skill (from the author modal); returns the created skill. */
  publishSkill: (draft: SkillDraft) => Skill;
  /** Publish/replace an already-materialized market skill (from "내 스킬" 공유). */
  publishOwnedSkill: (skill: Skill) => Skill;
  /** Remove a published skill from the market (from "내 스킬" 공유 해제). */
  unpublishSkill: (marketId: string) => void;
  togglePlugin: (id: string, enabled: boolean) => Promise<void>;
  filteredSkills: Skill[];
  filteredPlugins: Plugin[];
  bluePointBalance: number;
  setBluePointBalance: (balance: number) => void;
}

const MarketplaceCtx = createContext<MarketplaceContextValue | null>(null);

export const MarketplaceProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  // mock 시드 제거 — 마켓은 전적으로 platform 카탈로그에서 채운다. platform 오프라인이면 빈 상태.
  const [skills, setSkills] = useState<Skill[]>([]);
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  /** 카탈로그 첫 로드 진행 중 — 스크린이 스켈레톤/빈상태를 구분하는 데 쓴다. */
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [sortBy, setSortBy] = useState('popular');
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [bluePointBalance, setBluePointBalance] = useState(12480);

  // platform 카탈로그 로드 (skill + plugin). 오프라인이면 빈 상태로 남는다(빈상태 UI가 처리).
  useEffect(() => {
    const loadSkills = platform.getCatalog({ type: 'skill', sort: 'popular' }).then((res) => {
      if (res.success && res.data) setSkills(res.data.map(platformArtifactToSkill));
    });
    const loadPlugins = platform.getCatalog({ type: 'plugin', sort: 'popular' }).then((res) => {
      if (res.success && res.data) setPlugins(res.data.map(platformArtifactToPlugin));
    });
    void Promise.allSettled([loadSkills, loadPlugins]).then(() => setCatalogLoading(false));
  }, []);

  const toggleTag = useCallback((tag: string) => {
    setSelectedTags((prev) => (prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]));
  }, []);

  const filteredSkills = useMemo(() => {
    let filtered = skills;
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      filtered = filtered.filter(
        (skill) =>
          skill.name.toLowerCase().includes(query) ||
          skill.description.toLowerCase().includes(query) ||
          skill.author.toLowerCase().includes(query) ||
          skill.tags.some((tag) => tag.toLowerCase().includes(query)),
      );
    }
    if (selectedTags.length > 0) {
      filtered = filtered.filter((skill) => skill.tags.some((tag) => selectedTags.includes(tag)));
    }
    const sorted = [...filtered];
    switch (sortBy) {
      case 'popular':
        sorted.sort((a, b) => b.downloads - a.downloads);
        break;
      case 'recent':
        sorted.sort((a, b) => b.id.localeCompare(a.id));
        break;
      case 'rating':
        sorted.sort((a, b) => b.rating - a.rating);
        break;
      case 'sponsored':
        sorted.sort((a, b) => b.points - a.points);
        break;
      default:
        break;
    }
    return sorted;
  }, [skills, searchQuery, selectedTags, sortBy]);

  const filteredPlugins = useMemo(() => {
    let filtered = plugins;
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      filtered = filtered.filter(
        (plugin) =>
          plugin.name.toLowerCase().includes(query) ||
          plugin.description.toLowerCase().includes(query) ||
          plugin.provider.toLowerCase().includes(query) ||
          plugin.useCases.some((uc) => uc.toLowerCase().includes(query)),
      );
    }
    const sorted = [...filtered];
    switch (sortBy) {
      case 'popular':
      case 'skills':
        sorted.sort((a, b) => b.skillCount - a.skillCount);
        break;
      case 'recent':
        sorted.sort((a, b) => b.id.localeCompare(a.id));
        break;
      default:
        break;
    }
    return sorted;
  }, [plugins, searchQuery, sortBy]);

  const installArtifact = useCallback(async (id: string, type: 'skill' | 'plugin') => {
    setIsLoading(true);
    // platform 스킬(id: "platform-{name}")이면 실제 설치 파이프라인 실행 (CONTRACTS §2.3):
    //   1) Electron main이 platform에서 tar.gz를 받아 임시 경로에 저장 (+ X-Checksum)
    //   2) artifact 상세로 manifest 구성 (checksum 포함 → runtime이 무결성 검증)
    //   3) runtime POST /artifacts/install { path, manifest } 로 설치
    // runtime(api.isEnabled)·Electron 브리지가 모두 있어야 실제 경로를 타고,
    // 그 외에는 mock 지연으로 폴백한다 (렌더러-only dev / 오프라인).
    const bridge = (
      window as {
        electronAPI?: { artifact?: { download: (url: string) => Promise<{ path: string; checksum: string }> } };
      }
    ).electronAPI?.artifact;
    if (id.startsWith('platform-') && api.isEnabled && bridge) {
      const artifactId = id.slice('platform-'.length);
      try {
        const [detail, dl] = await Promise.all([
          platform.getArtifact(artifactId),
          bridge.download(platform.downloadUrl(artifactId)),
        ]);
        if (detail.success && detail.data && dl?.path) {
          const a = detail.data;
          await api.installArtifact({
            path: dl.path,
            manifest: {
              schema_version: 1,
              type: a.type,
              name: a.name,
              version: a.version,
              display_name: a.display_name,
              description: a.description,
              category: a.category,
              tags: a.tags,
              requires: a.requires,
              checksum: dl.checksum,
            },
          });
        }
      } catch (err) {
        console.error('platform skill install failed', err);
      }
    } else {
      // mock / 오프라인: 지연 시뮬레이션
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
    if (type === 'skill') {
      setSkills((prev) => prev.map((skill) => (skill.id === id ? { ...skill, installed: true, enabled: true } : skill)));
    } else {
      setPlugins((prev) => prev.map((plugin) => (plugin.id === id ? { ...plugin, installed: true, enabled: true } : plugin)));
    }
    setIsLoading(false);
  }, []);

  const uninstallArtifact = useCallback(async (id: string, type: 'skill' | 'plugin') => {
    setIsLoading(true);
    await new Promise((resolve) => setTimeout(resolve, 500));
    if (type === 'skill') {
      setSkills((prev) => prev.map((skill) => (skill.id === id ? { ...skill, installed: false, enabled: false } : skill)));
    } else {
      setPlugins((prev) => prev.map((plugin) => (plugin.id === id ? { ...plugin, installed: false, enabled: false } : plugin)));
    }
    setIsLoading(false);
  }, []);

  const sponsorSkill = useCallback(async (id: string, amount: number) => {
    setIsLoading(true);
    // platform 스킬이면 실제 sponsor API 호출 (Blue Point mock)
    if (id.startsWith('platform-')) {
      const artifactId = id.slice('platform-'.length);
      await platform.sponsorArtifact(artifactId, amount);
    } else {
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    setSkills((prev) => prev.map((skill) => (skill.id === id ? { ...skill, points: skill.points + amount } : skill)));
    setBluePointBalance((prev) => Math.max(0, prev - amount));
    setIsLoading(false);
  }, []);

  // Publish a user-authored skill (author modal) — prepend, pre-installed.
  const publishSkill = useCallback((draft: SkillDraft): Skill => {
    const skill: Skill = {
      id: `skill-user-${Date.now()}`,
      name: draft.name.trim(),
      description: draft.description.trim(),
      version: draft.version.trim() || '1.0.0',
      author: '나',
      team: draft.team?.trim() || undefined,
      category: draft.category,
      tags: draft.tags,
      downloads: 0,
      rating: 0,
      points: 0,
      installed: true,
      enabled: true,
      files: [{ path: 'SKILL.md', language: 'markdown', content: draft.definition }],
    };
    setSkills((prev) => [skill, ...prev]);
    return skill;
  }, []);

  // Publish/replace an already-built market skill (from 내 스킬 공유) — upsert by id.
  const publishOwnedSkill = useCallback((skill: Skill): Skill => {
    setSkills((prev) => {
      const without = prev.filter((s) => s.id !== skill.id);
      return [skill, ...without];
    });
    return skill;
  }, []);

  const unpublishSkill = useCallback((marketId: string) => {
    setSkills((prev) => prev.filter((s) => s.id !== marketId));
  }, []);

  const togglePlugin = useCallback(async (id: string, enabled: boolean) => {
    setPlugins((prev) => prev.map((plugin) => (plugin.id === id ? { ...plugin, enabled } : plugin)));
    await new Promise((resolve) => setTimeout(resolve, 300));
  }, []);

  const value = useMemo<MarketplaceContextValue>(
    () => ({
      skills,
      plugins,
      isLoading,
      catalogLoading,
      searchQuery,
      setSearchQuery,
      sortBy,
      setSortBy,
      selectedTags,
      toggleTag,
      installArtifact,
      uninstallArtifact,
      sponsorSkill,
      publishSkill,
      publishOwnedSkill,
      unpublishSkill,
      togglePlugin,
      filteredSkills,
      filteredPlugins,
      bluePointBalance,
      setBluePointBalance,
    }),
    [
      skills,
      plugins,
      isLoading,
      catalogLoading,
      searchQuery,
      sortBy,
      selectedTags,
      toggleTag,
      installArtifact,
      uninstallArtifact,
      sponsorSkill,
      publishSkill,
      publishOwnedSkill,
      unpublishSkill,
      togglePlugin,
      filteredSkills,
      filteredPlugins,
      bluePointBalance,
    ],
  );

  return <MarketplaceCtx.Provider value={value}>{children}</MarketplaceCtx.Provider>;
};

// eslint-disable-next-line react-refresh/only-export-components -- context hook lives with its provider
export const useMarketplace = (): MarketplaceContextValue => {
  const ctx = useContext(MarketplaceCtx);
  if (!ctx) throw new Error('useMarketplace must be used within a MarketplaceProvider');
  return ctx;
};

// Made with Bob
