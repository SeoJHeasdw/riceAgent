import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Skill, Plugin } from '../types';
import { installedPluginId, installedSkillId, platformArtifactToSkill, platformArtifactToPlugin } from '../skillBridge';
import { platform, api } from '../api';

/**
 * MarketplaceContext — single source of truth for the skill/plugin market,
 * shared across the market screens AND "나의 스킬" (so publishing an owned skill
 * actually surfaces it in the market). The install direction (market → 나의 스킬)
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

export interface InstallArtifactResult {
  id: string;
  enabled: boolean;
  missingConnectors: string[];
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
  installArtifact: (id: string, type: 'skill' | 'plugin') => Promise<InstallArtifactResult | undefined>;
  uninstallArtifact: (id: string, type: 'skill' | 'plugin') => Promise<void>;
  sponsorSkill: (id: string, amount: number) => Promise<void>;
  /** Publish a user-authored skill (from the author modal); returns the created skill. */
  publishSkill: (draft: SkillDraft) => Skill;
  /** Publish/replace an already-materialized market skill (from "나의 스킬" 공유). */
  publishOwnedSkill: (skill: Skill) => Skill;
  /** Remove a published skill from the market (from "나의 스킬" 공유 해제). */
  unpublishSkill: (marketId: string) => void;
  togglePlugin: (id: string, enabled: boolean) => Promise<boolean>;
  filteredSkills: Skill[];
  filteredPlugins: Plugin[];
  bluePointBalance: number;
  setBluePointBalance: (balance: number) => void;
}

const MarketplaceCtx = createContext<MarketplaceContextValue | null>(null);

type RuntimeSkillSummary = { id: string; state?: 'active' | 'stale' | 'archived' };
type RuntimePluginInfo = { id: string; enabled: boolean; source?: string };

const runtimePluginIdForMarketId = (id: string) => (id.startsWith('plugin:') ? id : installedPluginId(id));
const runtimeSkillIdForMarketId = (id: string) => (id.startsWith('platform-') ? installedSkillId(id) : id);

const mergeSkillInstallState = (catalog: Skill[], installed: RuntimeSkillSummary[]): Skill[] => {
  const byId = new Map(installed.map((s) => [s.id, s] as const));
  return catalog.map((skill) => {
    const rt = byId.get(runtimeSkillIdForMarketId(skill.id));
    return rt ? { ...skill, installed: true, enabled: rt.state !== 'archived' } : skill;
  });
};

const mergePluginInstallState = (catalog: Plugin[], installed: RuntimePluginInfo[]): Plugin[] => {
  const byId = new Map(installed.map((p) => [p.id, p] as const));
  return catalog.map((plugin) => {
    const rt = byId.get(runtimePluginIdForMarketId(plugin.id)) ?? byId.get(plugin.id);
    return rt
      ? { ...plugin, installed: true, enabled: rt.enabled, builtin: rt.source === 'builtin' }
      : plugin;
  });
};

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

  // platform 카탈로그 + runtime 설치 상태 병합. 런타임 연결이 뒤늦게 켜지는 Electron
  // 부트 흐름에서도 재조회해 마켓 카드의 설치/활성 상태가 재시작 뒤 흔들리지 않게 한다.
  const loadCatalog = useCallback(async () => {
    setCatalogLoading(true);
    const [skillCat, pluginCat, installedSkills, installedPlugins] = await Promise.all([
      platform.getCatalog({ type: 'skill', sort: 'popular' }),
      platform.getCatalog({ type: 'plugin', sort: 'popular' }),
      api.getSkills(),
      api.getPlugins(),
    ]);

    const runtimeSkills =
      installedSkills.success && installedSkills.data ? (installedSkills.data as unknown as RuntimeSkillSummary[]) : [];
    const runtimePlugins =
      installedPlugins.success && installedPlugins.data ? (installedPlugins.data as unknown as RuntimePluginInfo[]) : [];

    if (skillCat.success && skillCat.data) {
      setSkills(mergeSkillInstallState(skillCat.data.map(platformArtifactToSkill), runtimeSkills));
    }
    if (pluginCat.success && pluginCat.data) {
      setPlugins(mergePluginInstallState(pluginCat.data.map(platformArtifactToPlugin), runtimePlugins));
    }
    setCatalogLoading(false);
  }, []);

  useEffect(() => {
    void loadCatalog();
    const unsub = api.onEnabledChange((enabled) => {
      if (enabled) void loadCatalog();
    });
    return unsub;
  }, [loadCatalog]);

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
    let result: InstallArtifactResult | undefined;
    // platform artifact(id: "platform-{name}")이면 실제 설치 파이프라인 실행:
    //   Electron app: main이 tar.gz를 받아 temp path + X-Checksum을 runtime에 전달.
    //   Browser dev: runtime이 localhost platform URL을 직접 다운로드해 설치.
    // runtime이 오프라인이면 mock 지연으로 폴백한다.
    const bridge = (
      window as {
        electronAPI?: { artifact?: { download: (url: string) => Promise<{ path: string; checksum: string }> } };
      }
    ).electronAPI?.artifact;
    if (id.startsWith('platform-') && api.isEnabled) {
      const artifactId = id.slice('platform-'.length);
      try {
        const detail = await platform.getArtifact(artifactId);
        if (!detail.success || !detail.data) throw new Error(detail.error || 'platform artifact not found');
        const a = detail.data;
        const url = platform.downloadUrl(artifactId);
        const dl = bridge ? await bridge.download(url) : null;
        const res = await api.installArtifact({
          ...(dl?.path ? { path: dl.path } : { url }),
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
            checksum: dl?.checksum,
          },
          checksum: dl?.checksum,
        });
        if (!res.success) throw new Error(res.error || 'artifact install failed');
        const data = (res.data ?? {}) as { id?: string; enabled?: boolean; missing_connectors?: string[] };
        result = {
          id: data.id ?? (type === 'plugin' ? runtimePluginIdForMarketId(id) : runtimeSkillIdForMarketId(id)),
          enabled: data.enabled ?? true,
          missingConnectors: data.missing_connectors ?? [],
        };
      } catch (err) {
        console.error('platform artifact install failed', err);
        setIsLoading(false);
        return undefined;
      }
    } else {
      // mock / 오프라인: 지연 시뮬레이션
      await new Promise((resolve) => setTimeout(resolve, 1500));
      result = {
        id: type === 'plugin' ? runtimePluginIdForMarketId(id) : runtimeSkillIdForMarketId(id),
        enabled: true,
        missingConnectors: [],
      };
    }
    if (type === 'skill') {
      setSkills((prev) => prev.map((skill) => (skill.id === id ? { ...skill, installed: true, enabled: result?.enabled ?? true } : skill)));
    } else {
      setPlugins((prev) => prev.map((plugin) => (plugin.id === id ? { ...plugin, installed: true, enabled: result?.enabled ?? true } : plugin)));
    }
    setIsLoading(false);
    return result;
  }, []);

  const uninstallArtifact = useCallback(async (id: string, type: 'skill' | 'plugin') => {
    setIsLoading(true);
    if (api.isEnabled) {
      const artifactId = type === 'plugin' ? runtimePluginIdForMarketId(id) : runtimeSkillIdForMarketId(id);
      const res = await api.removeArtifact(artifactId);
      if (!res.success) console.error('artifact uninstall failed', res.error);
    } else {
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
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

  // Publish/replace an already-built market skill (from 나의 스킬 공유) — upsert by id.
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
    if (api.isEnabled) {
      const res = await api.setPluginEnabled(runtimePluginIdForMarketId(id), enabled);
      if (!res.success) {
        setPlugins((prev) => prev.map((plugin) => (plugin.id === id ? { ...plugin, enabled: !enabled } : plugin)));
        console.error('plugin toggle failed', res.error);
        return false;
      }
    } else {
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
    return true;
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
