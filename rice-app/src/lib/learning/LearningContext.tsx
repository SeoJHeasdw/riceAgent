import React, { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import {
  INSTALLED_TOOLS,
  MY_PLUGINS,
  PROPOSALS,
  SKILLS,
  SKILL_DETAILS,
  USER_MODEL,
  type InstalledTool,
  type MyPlugin,
  type Proposal,
  type Skill,
  type SkillDetail,
  type UserSignal,
} from '../learningData';
import type { Skill as MarketSkill } from '../types';
import { installedSkillId, marketSkillToOwned } from '../skillBridge';

/**
 * LearningContext — single source of truth for the learning-loop state shared
 * between the chat learning panel and the "내 스킬" management screen:
 * skills, user-model signals, installed tools/plugins, and per-skill details.
 * Also coordinates "jump to skill" highlighting from the chat thread.
 */

interface LearningContextValue {
  skills: Skill[];
  setSkills: React.Dispatch<React.SetStateAction<Skill[]>>;
  signals: UserSignal[];
  setSignals: React.Dispatch<React.SetStateAction<UserSignal[]>>;
  installed: InstalledTool[];
  setInstalled: React.Dispatch<React.SetStateAction<InstalledTool[]>>;
  details: Record<string, SkillDetail>;
  setDetails: React.Dispatch<React.SetStateAction<Record<string, SkillDetail>>>;
  myPlugins: MyPlugin[];
  setMyPlugins: React.Dispatch<React.SetStateAction<MyPlugin[]>>;
  /** Pending learning-loop proposals awaiting accept/dismiss */
  proposals: Proposal[];
  setProposals: React.Dispatch<React.SetStateAction<Proposal[]>>;

  /**
   * 마켓 스킬을 "내 스킬"(소유 인스턴스)로 materialize 한다 — 설치의 학습-store 측 배선.
   * marketId 기준으로 멱등(이미 설치돼 있으면 그대로). 소유 id(inst-…)를 돌려준다.
   */
  installSkillFromMarket: (market: MarketSkill) => string;

  /** Skill id currently highlighted in the panel (after a chat "jump") */
  highlightedSkill: string | null;
  /** Highlight a skill in the panel for a short pulse */
  jumpToSkill: (id: string) => void;

  /** Scroll target for the learning panel (so chat can scroll it to top) */
  panelScrollRef: React.RefObject<HTMLDivElement | null>;
}

const LearningCtx = createContext<LearningContextValue | null>(null);

const cloneDetails = (): Record<string, SkillDetail> => {
  const out: Record<string, SkillDetail> = {};
  Object.keys(SKILL_DETAILS).forEach((k) => {
    out[k] = { ...SKILL_DETAILS[k], rules: SKILL_DETAILS[k].rules.map((r) => ({ ...r })) };
  });
  return out;
};

export const LearningProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [skills, setSkills] = useState<Skill[]>(() =>
    SKILLS.map((s) => ({ ...s, shared: SKILL_DETAILS[s.id]?.shared })),
  );
  const [signals, setSignals] = useState<UserSignal[]>(() => USER_MODEL.map((s) => ({ ...s })));
  const [installed, setInstalled] = useState<InstalledTool[]>(() => INSTALLED_TOOLS.map((t) => ({ ...t })));
  const [details, setDetails] = useState<Record<string, SkillDetail>>(cloneDetails);
  const [myPlugins, setMyPlugins] = useState<MyPlugin[]>(() =>
    MY_PLUGINS.map((p) => ({ ...p, skills: p.skills.map((s) => ({ ...s })), examples: [...p.examples] })),
  );
  const [proposals, setProposals] = useState<Proposal[]>(() => PROPOSALS.map((p) => ({ ...p })));
  const [highlightedSkill, setHighlightedSkill] = useState<string | null>(null);
  const panelScrollRef = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const jumpToSkill = useCallback((id: string) => {
    setHighlightedSkill(id);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setHighlightedSkill(null), 2400);
  }, []);

  // install = 마켓 → 소유 materialize. marketId 기준 멱등(중복 설치 방지).
  const installSkillFromMarket = useCallback((market: MarketSkill): string => {
    const id = installedSkillId(market.id);
    const { skill, detail } = marketSkillToOwned(market);
    setSkills((prev) => (prev.some((s) => s.id === id) ? prev : [skill, ...prev]));
    setDetails((prev) => (prev[id] ? prev : { ...prev, [id]: detail }));
    return id;
  }, []);

  const value = useMemo<LearningContextValue>(
    () => ({
      skills,
      setSkills,
      signals,
      setSignals,
      installed,
      setInstalled,
      details,
      setDetails,
      myPlugins,
      setMyPlugins,
      proposals,
      setProposals,
      installSkillFromMarket,
      highlightedSkill,
      jumpToSkill,
      panelScrollRef,
    }),
    [skills, signals, installed, details, myPlugins, proposals, installSkillFromMarket, highlightedSkill, jumpToSkill],
  );

  return <LearningCtx.Provider value={value}>{children}</LearningCtx.Provider>;
};

// eslint-disable-next-line react-refresh/only-export-components -- context hook lives with its provider
export const useLearning = (): LearningContextValue => {
  const ctx = useContext(LearningCtx);
  if (!ctx) throw new Error('useLearning must be used within a LearningProvider');
  return ctx;
};

// Made with Bob
