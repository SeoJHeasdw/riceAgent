import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import {
  type InstalledTool,
  type MyPlugin,
  type Proposal,
  type Skill,
  type SkillDetail,
  type UserSignal,
} from '../learningData';
import type { Skill as MarketSkill, Plugin as MarketPlugin, SkillSuggestion } from '../types';
import {
  installedSkillId,
  marketSkillToOwned,
  installedPluginId,
  marketPluginToMyPlugin,
  platformArtifactToPlugin,
  runtimePluginToMyPlugin,
} from '../skillBridge';
import { api, platform } from '../api';

/**
 * LearningContext — single source of truth for the learning-loop state shared
 * between the chat learning panel and the "내 스킬" management screen:
 * skills, user-model signals, installed tools/plugins, and per-skill details.
 * Also coordinates "jump to skill" highlighting from the chat thread.
 */

interface LearningContextValue {
  /** 런타임 첫 hydrate 진행 중 (스켈레톤 vs 빈상태 구분용) */
  hydrating: boolean;
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

  /** 영속 제안함 (Self-Improvement v3) — 타임아웃된 HITL 제안 + 발굴 산출물 */
  suggestions: SkillSuggestion[];
  /** 제안함 재조회 (GET /skills/suggestions) */
  refreshSuggestions: () => Promise<void>;
  /** 제안함 항목 승인/거부 → 런타임 적용 + 목록/스킬 동기화 */
  decideSuggestion: (id: string, approved: boolean, editedBody?: string) => Promise<boolean>;
  /** 크로스세션 패턴 발굴(수동) — 새로 추가된 제안 수를 돌려준다 */
  discoverNow: () => Promise<number>;
  /** 신호 제거 — 런타임 메모리(mem_*)면 DELETE /memories/{id}까지 배선(위생) */
  removeSignal: (id: string) => void;

  /**
   * 마켓 스킬을 "내 스킬"(소유 인스턴스)로 materialize 한다 — 설치의 학습-store 측 배선.
   * marketId 기준으로 멱등(이미 설치돼 있으면 그대로). 소유 id(inst-…)를 돌려준다.
   */
  installSkillFromMarket: (market: MarketSkill) => string;

  /**
   * 마켓 플러그인을 "내 플러그인"으로 materialize 한다 — 플러그인 설치의 학습-store 측 배선.
   * marketId 기준 멱등. 소유 id(inst-…)를 돌려준다. (installSkillFromMarket 와 대칭)
   */
  installPluginFromMarket: (market: MarketPlugin, options?: { enabled?: boolean }) => string;

  /** Skill id currently highlighted in the panel (after a chat "jump") */
  highlightedSkill: string | null;
  /** Highlight a skill in the panel for a short pulse */
  jumpToSkill: (id: string) => void;

  /** Scroll target for the learning panel (so chat can scroll it to top) */
  panelScrollRef: React.RefObject<HTMLDivElement | null>;
}

const LearningCtx = createContext<LearningContextValue | null>(null);

/**
 * runtime GET /skills 응답(SkillSummary.__dict__) → learningData.Skill 변환.
 *
 * runtime 스킬은 kebab-case name을 id로 쓴다. version은 정수(예: 3 → "v3").
 * idleDays는 last_used_at에서 계산 (없으면 undefined).
 */
function runtimeSkillToLearning(s: {
  id: string;
  name: string;
  version: number;
  description: string;
  state: 'active' | 'stale' | 'archived';
  tags: string[];
  use: number;
  patch: number;
  last_used_at: string | null;
}): Skill {
  const idleDays = (() => {
    if (!s.last_used_at) return undefined;
    const ms = Date.now() - new Date(s.last_used_at).getTime();
    return Math.floor(ms / 86_400_000);
  })();
  // LearningPanel이 "N회 사용 · {trend}"로 렌더하므로 사용 횟수를 반복하지 않는다
  const useTrend =
    s.use === 0
      ? '아직 사용 전'
      : idleDays == null
        ? '사용 중'
        : idleDays === 0
          ? '오늘 사용'
          : `${idleDays}일 전 사용`;
  return {
    id: s.id,
    name: s.name,
    ver: `v${s.version}`,
    uses: s.use,
    trend: useTrend,
    enabled: s.state === 'active',
    hot: s.use > 10,
    patches: s.patch,
    command: s.name, // runtime id(kebab) = slash command
    state: s.state,
    idleDays,
    shared: false,
  };
}

/**
 * runtime GET /skills 응답으로 최소 SkillDetail을 생성.
 * 규칙·버전 히스토리는 런타임이 별도 엔드포인트(GET /skills/{id}/history)로 제공하나
 * 초기 로드엔 description만 있는 최소 detail로 채운다 (이후 상세 화면에서 lazy load).
 */
function runtimeSkillToDetail(s: {
  id: string;
  name: string;
  version: number;
  description: string;
  state: string;
  source: string | null;
  last_used_at: string | null;
}): SkillDetail {
  return {
    summary: s.description,
    createdFrom: s.source ? `${s.source}` : '대화에서 자동 생성',
    lastUsed: s.last_used_at ? new Date(s.last_used_at).toLocaleDateString('ko-KR') : '아직 없음',
    shared: false,
    rules: [],
    versions: [{ ver: `v${s.version}`, when: s.last_used_at ?? '알 수 없음', note: '런타임 동기화', current: true }],
    usage: [],
    permissions: [],
  };
}

export const LearningProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  // mock 시드 제거 — 전부 런타임에서 채운다(GET /skills·/memories·/plugins).
  // 런타임 오프라인이면 빈 상태로 남고, 빈상태 UI가 이를 표시한다.
  const [skills, setSkills] = useState<Skill[]>([]);
  const [signals, setSignals] = useState<UserSignal[]>([]);
  const [installed, setInstalled] = useState<InstalledTool[]>([]);
  const [details, setDetails] = useState<Record<string, SkillDetail>>({});
  const [myPlugins, setMyPlugins] = useState<MyPlugin[]>([]);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [suggestions, setSuggestions] = useState<SkillSuggestion[]>([]);
  /** 런타임 첫 hydrate 진행 중 — 스크린이 스켈레톤 vs 빈상태를 구분하는 데 쓴다. */
  const [hydrating, setHydrating] = useState(true);
  const [highlightedSkill, setHighlightedSkill] = useState<string | null>(null);
  const panelScrollRef = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 스킬/제안함 로더 — hydrate와 제안 결정(승인 후 목록 동기화) 양쪽에서 재사용하므로
  // useCallback으로 훅 스코프에 둔다(useEffect 안에 가두면 decideSuggestion이 못 쓴다).
  const loadSkills = useCallback(() => {
    return api.getSkills().then((res) => {
      if (!res.success || !res.data) return;
      type RuntimeSkill = {
        id: string; name: string; version: number; description: string;
        state: 'active' | 'stale' | 'archived'; tags: string[];
        use: number; patch: number; last_used_at: string | null; source?: string | null;
      };
      const raw = res.data as RuntimeSkill[];
      const loadedSkills = raw.map(runtimeSkillToLearning);
      const loadedDetails: Record<string, SkillDetail> = {};
      raw.forEach((s) => { loadedDetails[s.id] = runtimeSkillToDetail({ ...s, source: s.source ?? null }); });
      setSkills(loadedSkills);
      setDetails(loadedDetails);
    });
  }, []);

  const refreshSuggestions = useCallback(async () => {
    const res = await api.getSkillSuggestions();
    if (res.success && res.data) setSuggestions(res.data);
  }, []);

  // runtime API에서 스킬 목록·메모리 로드 (GET /skills, GET /memories).
  // 런타임 연결은 Electron health probe라 비동기 — provider 마운트 시점엔 아직 offline일 수
  // 있다(그땐 ApiClient.get()이 { success: false } 반환). 그래서 마운트 때 한 번 시도하고,
  // enabled가 true로 바뀌는 순간 다시 로드한다(api.onEnabledChange). 안 그러면 빈 목록으로 굳는다.
  useEffect(() => {
    const loadMemories = () => {
      return api.getMemories().then((res) => {
        if (!res.success || !res.data) return;
        type MemItem = { id: string; kind: 'fact' | 'preference'; content: string; created_at?: string };
        const raw = res.data as { memories?: MemItem[] } | MemItem[];
        const list: MemItem[] = Array.isArray(raw) ? raw : (raw.memories ?? []);
        if (!list.length) return;
        // 런타임 메모리를 UserSignal 형식으로 변환해 신호 목록에 병합.
        setSignals((prev) => {
          const existingIds = new Set(prev.map((s) => s.id));
          const newSignals = list
            .filter((m) => !existingIds.has(m.id))
            .map((m): UserSignal => ({
              id: m.id,
              label: m.kind === 'fact' ? '사실' : '선호',
              value: m.content,
              conf: 0.8,
              source: '런타임 메모리',
            }));
          return newSignals.length ? [...prev, ...newSignals] : prev;
        });
      });
    };
    // 설치된 플러그인 복원 (GET /plugins) → 내 플러그인. 런타임이 installed_artifacts에
    // 영속하므로 재시작해도 남아야 한다(설치 후 사라지던 버그). platform 카탈로그와 교차참조해
    // 스킬명·커넥터 설명까지 풍부하게 복원하고, 매칭이 없으면 런타임 메타로 폴백.
    const loadPlugins = () => {
      return Promise.all([api.getPlugins(), platform.getCatalog({ type: 'plugin' }), api.getConnectors()]).then(
        ([rt, cat, conns]) => {
          if (!rt.success || !rt.data) return; // 런타임 오프라인 → 빈 상태 유지
          type RtPlugin = {
            id: string; name: string; version: string; enabled: boolean;
            required_connectors?: string[]; skill_count?: number; description?: string;
          };
          type RtConnector = { connector_type: string; status: string };
          const installed = rt.data as unknown as RtPlugin[];
          const catalog = cat.success && cat.data ? cat.data : [];
          const connectedTypes = new Set(
            ((conns.success && conns.data ? conns.data : []) as RtConnector[])
              .filter((c) => c.status === 'connected')
              .map((c) => c.connector_type.toLowerCase()),
          );
          const byId = new Map(catalog.map((a) => [a.id, a] as const));
          const connectorKey = (name: string) => {
            const s = name.toLowerCase();
            if (s.includes('gmail')) return 'gmail';
            if (s.includes('calendar') || s.includes('캘린더')) return 'calendar';
            if (s.includes('drive') || s.includes('드라이브')) return 'drive';
            return s;
          };
          const decoratePermissions = (p: MyPlugin): MyPlugin => ({
            ...p,
            permissions: p.permissions.map((perm) => ({
              ...perm,
              mode: connectedTypes.has(connectorKey(perm.name)) ? '연결됨' : perm.mode,
            })),
          });
          const mapped = installed.map((p): MyPlugin => {
            const art = byId.get(p.id) ?? byId.get(p.id.replace(/^plugin:/, ''));
            if (art) {
              // 카탈로그 매칭: 마켓 Plugin으로 풍부하게, 단 정체성·상태는 런타임 기준.
              return decoratePermissions({ ...marketPluginToMyPlugin(platformArtifactToPlugin(art)), id: p.id, enabled: p.enabled, version: p.version });
            }
            return decoratePermissions(runtimePluginToMyPlugin(p));
          });
          setMyPlugins(mapped);
        },
      );
    };

    // 첫 hydrate가 끝나면(성공/실패 무관) 로딩 플래그를 내려 빈상태/스켈레톤을 전환한다.
    void Promise.allSettled([loadSkills(), loadMemories(), loadPlugins(), refreshSuggestions()]).then(
      () => setHydrating(false),
    );
    const unsub = api.onEnabledChange((enabled) => {
      if (enabled) {
        setHydrating(true);
        void Promise.allSettled([loadSkills(), loadMemories(), loadPlugins(), refreshSuggestions()]).then(
          () => setHydrating(false),
        );
      }
    });
    return unsub;
  }, [loadSkills, refreshSuggestions]);

  // 제안함 항목 승인/거부 — 승인이 적용되면(runtime이 create/patch/archive 수행)
  // 스킬 목록을 재동기화해 MySkills/패널이 즉시 새 버전을 보여주게 한다.
  const decideSuggestion = useCallback(
    async (id: string, approved: boolean, editedBody?: string): Promise<boolean> => {
      const res = await api.decideSkillSuggestion(id, approved, editedBody);
      if (!res.success) return false;
      setSuggestions((prev) => prev.filter((s) => s.id !== id));
      if (approved) void loadSkills();
      return true;
    },
    [loadSkills],
  );

  // 크로스세션 발굴(수동 트리거) — LLM 1회 호출이라 느릴 수 있다(호출측이 로딩 표시).
  const discoverNow = useCallback(async (): Promise<number> => {
    const res = await api.discoverSkills();
    if (!res.success || !res.data) return 0;
    await refreshSuggestions();
    return res.data.suggestions.length;
  }, [refreshSuggestions]);

  // 신호 제거 — 런타임 메모리에서 온 신호(mem_*)는 서버에서도 지운다(위생: 메모리는
  // HITL 없이 매 턴 주입되므로 사후 제거가 유일한 통제 수단).
  const removeSignal = useCallback((id: string) => {
    setSignals((prev) => prev.filter((x) => x.id !== id));
    if (id.startsWith('mem_')) void api.deleteMemory(id);
  }, []);

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

  // install(plugin) = 마켓 → 내 플러그인 materialize. marketId 기준 멱등(중복 설치 방지).
  const installPluginFromMarket = useCallback((market: MarketPlugin, options?: { enabled?: boolean }): string => {
    const id = installedPluginId(market.id);
    const enabled = options?.enabled ?? true;
    const myPlugin = {
      ...marketPluginToMyPlugin(market),
      enabled,
      updated: enabled ? '방금 설치' : '커넥터 연결 필요',
    };
    setMyPlugins((prev) => (prev.some((p) => p.id === id) ? prev : [myPlugin, ...prev]));
    return id;
  }, []);

  const value = useMemo<LearningContextValue>(
    () => ({
      hydrating,
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
      suggestions,
      refreshSuggestions,
      decideSuggestion,
      discoverNow,
      removeSignal,
      installSkillFromMarket,
      installPluginFromMarket,
      highlightedSkill,
      jumpToSkill,
      panelScrollRef,
    }),
    [hydrating, skills, signals, installed, details, myPlugins, proposals, suggestions, refreshSuggestions, decideSuggestion, discoverNow, removeSignal, installSkillFromMarket, installPluginFromMarket, highlightedSkill, jumpToSkill],
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
