import React, { createContext, useCallback, useContext, useMemo, useState } from 'react';
import { Skill, Plugin } from '../types';
import { mockSkills, mockPlugins } from '../mockData';
import { SKILLS, SKILL_DETAILS } from '../learningData';
import { ownedToMarketSkill } from '../skillBridge';

// 시드에서 이미 공유된 내 스킬(detail.shared)을 마켓에도 게시된 상태로 올린다 —
// "공유됨" 표시가 실제 마켓 등재와 일치하도록. (이후 내 스킬에서 공유 토글로 추가/제거)
const seededPublishedSkills = (): Skill[] =>
  SKILLS.filter((s) => SKILL_DETAILS[s.id]?.shared).map((s) =>
    ownedToMarketSkill({ ...s, shared: true }, SKILL_DETAILS[s.id]),
  );

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
  const [skills, setSkills] = useState<Skill[]>(() => [...seededPublishedSkills(), ...mockSkills]);
  const [plugins, setPlugins] = useState<Plugin[]>(mockPlugins);
  const [isLoading, setIsLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [sortBy, setSortBy] = useState('popular');
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [bluePointBalance, setBluePointBalance] = useState(12480);

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
    await new Promise((resolve) => setTimeout(resolve, 1500));
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
    await new Promise((resolve) => setTimeout(resolve, 500));
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
