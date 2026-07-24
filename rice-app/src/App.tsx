import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { Layout } from './components/Layout';
import { api } from './lib/api';
import { sseClient } from './lib/sse';
import ChatScreen from '@screens/ChatScreen';
import { toRailItems } from './lib/navigation';
import { I18nProvider } from './lib/i18n/I18nContext';
import { LearningProvider } from './lib/learning/LearningContext';
import { MarketplaceProvider } from './lib/marketplace/MarketplaceContext';
import { ToastProvider } from './lib/toast/ToastProvider';
import { LanguageSelectDialog } from './components/carbon/LanguageSelectDialog';
import {
  TourOverlay,
  TOUR_STEPS,
  TOUR_DONE_KEY,
  SCREEN_TOUR_KEYS,
  MY_SKILLS_TOUR_STEPS,
  MY_PLUGINS_TOUR_STEPS,
  CONNECTORS_TOUR_STEPS,
  screenTourIdForPath,
  useScreenTour,
} from './components/carbon/tour';
import { CommandPalette } from './components/carbon/CommandPalette';
import { REPLAY_ONBOARDING_EVENT, REPLAY_TOUR_EVENT, RESET_ALL_DATA_EVENT } from './lib/tutorial';
import { useLearning } from './lib/learning/LearningContext';
import { defaultModelSettings } from './lib/mockData';
import type { ModelSettings } from './components/carbon/ModelSettingsPanel';
import type { RegisteredModel } from './components/carbon/ModelSettingsPanel';
import HomeScreen from '@screens/HomeScreen';
import MySkillsScreen from '@screens/MySkillsScreen';
import MyPluginsScreen from '@screens/MyPluginsScreen';
import SkillMarketScreen from '@screens/SkillMarketScreen';
import PluginMarketScreen from '@screens/PluginMarketScreen';
import ConnectorsScreen from '@screens/ConnectorsScreen';
import SettingsScreen from '@screens/SettingsScreen';

// The cinematic renderer includes Three.js. Load it only after language
// selection so the everyday app shell stays lean and fast to reopen.
const ModelOnboarding = lazy(() =>
  import('./components/carbon/ModelOnboarding').then((module) => ({ default: module.ModelOnboarding })),
);

// 좌측 레일 항목은 공용 네비게이션 매니페스트에서 파생한다(홈 허브와 동일 출처).
const railItems = toRailItems();

interface AppShellProps {
  tourActive: boolean;
  screenToursSuspended: boolean;
  onTourDone: () => void;
  onTriggerOnboarding: () => void;
  onStopTour: () => void;
}

/** 화면 경로(콘텐츠 pathname) → 해당 화면 투어 step 배열 */
const SCREEN_TOUR_STEPS_MAP = {
  mySkills:   MY_SKILLS_TOUR_STEPS,
  myPlugins:  MY_PLUGINS_TOUR_STEPS,
  connectors: CONNECTORS_TOUR_STEPS,
} as const;

function AppShell({ tourActive, screenToursSuspended, onTourDone, onTriggerOnboarding, onStopTour }: AppShellProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const [dataGeneration, setDataGeneration] = useState(0);
  const settingsOpen = location.pathname === '/settings';
  const { isDone, markDone, resetAll: resetScreenTours } = useScreenTour();

  // Settings is a destination in the URL, but visually it is an overlay. Keep
  // the last workspace route mounted beneath it so opening preferences never
  // feels like leaving the task in progress. A direct /settings launch falls
  // back to Chat, which is the app's default workspace.
  const currentPath = `${location.pathname}${location.search}${location.hash}`;
  const backgroundPathRef = useRef(location.pathname === '/settings' ? '/chat' : currentPath);
  if (!settingsOpen) backgroundPathRef.current = currentPath;

  const contentPath = settingsOpen ? backgroundPathRef.current : currentPath;
  const contentPathname = contentPath.split(/[?#]/, 1)[0];
  const isChatRoute = contentPathname === '/chat';

  const contentRailId =
    railItems.find((item) => contentPathname === item.path || contentPathname.startsWith(`${item.path}/`))?.id ??
    'home';
  const activeRailId = settingsOpen ? 'settings' : contentRailId;

  const closeSettings = () => {
    const historyIndex = (window.history.state as { idx?: number } | null)?.idx ?? 0;
    if (historyIndex > 0) navigate(-1);
    else navigate(backgroundPathRef.current, { replace: true });
  };

  // ── 화면별 단발성 투어 ────────────────────────────────────────────────────
  // 전역 투어 실행 중에는 절대 동시에 뜨지 않는다(전역 투어 우선).
  // 경로 기준으로 해당 화면의 완료 키가 없는 첫 방문에만 표시.
  const screenTourId = screenTourIdForPath(contentPathname);
  const screenTourActive = !tourActive
    && !screenToursSuspended
    && !settingsOpen
    && screenTourId !== null
    && !isDone(screenTourId);
  const activeScreenTourSteps = screenTourId ? SCREEN_TOUR_STEPS_MAP[screenTourId] : null;

  const handleScreenTourDone = useCallback(() => {
    if (screenTourId) markDone(screenTourId);
  }, [screenTourId, markDone]);

  // 전체 데이터 삭제 이벤트 — LearningProvider 안에 있으므로 여기서 useLearning 사용 가능
  const { resetAll } = useLearning();
  const handleResetAllData = useCallback(() => {
    resetAll();
    // Chat owns session/thread/model preference caches internally. Remount it
    // after destructive reset so deleted sessions and local UI settings cannot
    // survive until the next application launch.
    setDataGeneration((generation) => generation + 1);
    resetScreenTours(); // 화면별 투어 완료 키도 초기화
    onStopTour();
    onTriggerOnboarding(); // 온보딩 첫 화면(언어 선택)부터 재시작
  }, [resetAll, resetScreenTours, onStopTour, onTriggerOnboarding]);

  useEffect(() => {
    window.addEventListener(RESET_ALL_DATA_EVENT, handleResetAllData);
    return () => window.removeEventListener(RESET_ALL_DATA_EVENT, handleResetAllData);
  }, [handleResetAllData]);

  return (
    <>
      <Layout
        railItems={railItems}
        activeRailId={activeRailId}
        onNavigate={(_, path) => navigate(path)}
        contentPath={contentPathname}
      >
        {/* Keep ChatScreen mounted while another top-level screen is open. Its
            SSE handlers own the in-progress turn renderer; unmounting it used to
            discard every incremental event even though the runtime kept working. */}
        <div className={isChatRoute ? 'flex flex-1 min-h-0 animate-fade-in' : 'hidden'}>
          <ChatScreen key={`chat-${dataGeneration}`} active={isChatRoute} />
        </div>

        {!isChatRoute && (
          <div key={contentRailId} className="flex flex-1 flex-col min-h-0 animate-fade-in">
            <Routes location={contentPath}>
              <Route path="/" element={<Navigate to="/chat" replace />} />
              <Route path="/home" element={<HomeScreen />} />
              <Route path="/my-skills" element={<MySkillsScreen />} />
              <Route path="/my-plugins" element={<MyPluginsScreen />} />
              <Route path="/skills" element={<SkillMarketScreen />} />
              <Route path="/plugins" element={<PluginMarketScreen />} />
              <Route path="/connectors" element={<ConnectorsScreen />} />
              <Route path="/connectors/:id" element={<ConnectorsScreen />} />
              <Route path="*" element={<Navigate to="/chat" replace />} />
            </Routes>
          </div>
        )}
      </Layout>

      <SettingsScreen open={settingsOpen} onClose={closeSettings} />

      <CommandPalette />

      {/* 전역 투어 — 전역 투어는 항상 우선 */}
      {tourActive && (
        <TourOverlay
          steps={TOUR_STEPS}
          currentPath={location.pathname}
          onNavigate={(path) => navigate(path)}
          onDone={onTourDone}
        />
      )}

      {/* 화면별 단발성 투어 — 전역 투어가 없을 때만 표시 */}
      {screenTourActive && activeScreenTourSteps && (
        <TourOverlay
          key={`screen-tour-${screenTourId}`}
          steps={activeScreenTourSteps}
          currentPath={location.pathname}
          onNavigate={(path) => navigate(path)}
          onDone={handleScreenTourDone}
          missingTargetBehavior="wait"
        />
      )}
    </>
  );
}

// Minimal shape of the Electron preload bridge we rely on (typed locally so the
// renderer doesn't depend on the preload's global augmentation).
type RuntimeStatusMsg = { running: boolean; port?: number; token?: string };
type RuntimeBridge = {
  getStatus: () => Promise<RuntimeStatusMsg>;
  onReady?: (cb: (status: RuntimeStatusMsg) => void) => () => void;
};

// 등록 모델의 provider(UI) → 런타임 provider kind 매핑.
const providerKind = (provider?: string): 'claude' | 'openai' | 'watsonx' => {
  switch (provider) {
    case 'openai':
    case 'custom':
      return 'openai';
    case 'watsonx':
      return 'watsonx';
    default:
      return 'claude';
  }
};

function App() {
  const [langSelectOpen, setLangSelectOpen] = useState(false);
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  const [tourActive, setTourActive] = useState(false);
  const [modelMode, setModelMode] = useState<'saas' | 'local'>('saas');
  const [currentModel, setCurrentModel] = useState('claude-sonnet-4-6');
  const [modelSettings, setModelSettings] = useState<ModelSettings>(defaultModelSettings);

  // Connect the API/SSE clients to the local runtime only when Electron reports
  // it running (IPC — no network probe). In the design demo (browser, or runtime
  // not spawned) we stay offline: clients no-op and screens use scripted mock
  // data, so no failing localhost requests hit the console.
  useEffect(() => {
    const connect = (baseUrl: string, token: string | null = null) => {
      // A factory reset/restart mints a new port and bearer token. Cycle the
      // connected flag even when it was already true so every runtime-backed
      // provider discards stale in-memory data and hydrates from the fresh DB.
      api.setEnabled(false);
      sseClient.setEnabled(false);
      api.setBaseUrl(baseUrl);
      api.setToken(token);
      api.setEnabled(true);
      sseClient.setBaseUrl(baseUrl);
      sseClient.setToken(token);
      sseClient.setEnabled(true);
      console.info('[RICE] runtime connected:', baseUrl);
    };

    const bridge = (window as { electronAPI?: { runtime?: RuntimeBridge } }).electronAPI?.runtime;
    if (bridge) {
      // Runtime may already be up (fast restart) → probe once now …
      bridge
        .getStatus()
        .then((status) => {
          if (status?.running && status.port) connect(`http://localhost:${status.port}`, status.token ?? null);
        })
        .catch(() => {
          /* runtime not reachable yet — wait for the ready push below */
        });
      // … and/or connect when main reports it healthy (window paints before spawn).
      const off = bridge.onReady?.((status) => {
        if (status?.running && status.port) connect(`http://localhost:${status.port}`, status.token ?? null);
      });
      return () => off?.();
    }

    // Renderer-only dev (no Electron IPC): connect directly to a locally-run
    // runtime if VITE_RICE_RUNTIME_URL is set AND /health answers. The health
    // probe means we silently stay on scripted mock data when the runtime is
    // down — no failing requests, no console noise.
    const env = import.meta.env as Record<string, string | undefined>;
    const devUrl = env.VITE_RICE_RUNTIME_URL;
    if (!devUrl) return;
    const token = env.VITE_RICE_RUNTIME_TOKEN ?? null;
    void fetch(`${devUrl}/health`)
      .then((r) => {
        if (r.ok) connect(devUrl, token);
      })
      .catch(() => {
        /* dev runtime not up — stay on mock */
      });
  }, []);

  // 런타임 연결 시 configured 여부를 확인해 온보딩을 트리거한다.
  // 어떤 화면에서 시작하든 동작하도록 App 레벨에서 처리한다.
  useEffect(() => {
    return api.onEnabledChange((enabled) => {
      if (!enabled) return;
      void api.getSettings().then((res) => {
        if (!res.success) return;
        const data = res.data as {
          provider?: { kind?: string; model?: string; base_url?: string; temperature?: number; configured?: boolean; show_thinking?: boolean };
          registered_models?: RegisteredModel[];
        };
        const prov = data?.provider;
        if (!prov) return;

        // 온보딩 다시 보기에서도 현재 런타임 설정으로 시작하도록 configured
        // 여부와 관계없이 App 레벨 draft를 먼저 복원한다.
        const registered = data?.registered_models ?? [];
        const restoredMode: 'saas' | 'local' = prov.kind === 'ollama' ? 'local' : 'saas';
        setModelMode(restoredMode);
        if (prov.model) setCurrentModel(prov.model);
        setModelSettings((prev) => ({
          ...prev,
          registeredModels: registered,
          ...(prov.base_url ? { endpoint: prov.base_url } : {}),
          ...(prov.temperature !== undefined ? { temperature: prov.temperature } : {}),
          ...(prov.show_thinking !== undefined ? { showThinking: prov.show_thinking } : {}),
        }));

        // configured: false → 온보딩 강제
        if (prov.configured) return;
        setLangSelectOpen(true);
      });
    });
  }, []);

  const handleOnboardingComplete = ({ modelId, settings, mode }: { modelId: string; settings: ModelSettings; mode: 'saas' | 'local' }) => {
    setCurrentModel(modelId);
    setModelSettings(settings);
    setModelMode(mode);
    const activeEntry = settings.registeredModels.find((r) => r.modelId === modelId);
    const kind = mode === 'local' ? 'ollama' : providerKind(activeEntry?.provider);
    void api.updateSettings({
      provider: {
        kind,
        model: modelId,
        temperature: settings.temperature,
        base_url: mode === 'local' ? settings.endpoint ?? null : null,
        configured: true,
        show_thinking: settings.showThinking,
      },
      registered_models: settings.registeredModels,
    });
    try {
      localStorage.setItem('rice.chat.showThinking', String(settings.showThinking));
    } catch {
      // 런타임 저장이 기준이며 localStorage는 첫 렌더용 캐시일 뿐이다.
    }
    setOnboardingOpen(false);
    // 투어를 아직 본 적 없으면 바로 시작
    if (!localStorage.getItem(TOUR_DONE_KEY)) {
      setTourActive(true);
    }
  };

  const handleTourDone = () => {
    localStorage.setItem(TOUR_DONE_KEY, '1');
    setTourActive(false);
  };

  // 설정 화면에서 "다시 보기"를 누르면 온보딩/투어를 재실행한다(tutorial.ts 브리지).
  // 투어 다시 보기 시 화면별 완료 키도 제거해 각 화면 재방문 시 다시 볼 수 있게 한다.
  useEffect(() => {
    const onReplayOnboarding = () => setLangSelectOpen(true);
    const onReplayTour = () => {
      // 화면별 투어 완료 키 초기화 (SCREEN_TOUR_KEYS 직접 참조)
      try {
        for (const key of Object.values(SCREEN_TOUR_KEYS)) {
          localStorage.removeItem(key);
        }
      } catch { /* ignore */ }
      setTourActive(true);
    };
    window.addEventListener(REPLAY_ONBOARDING_EVENT, onReplayOnboarding);
    window.addEventListener(REPLAY_TOUR_EVENT, onReplayTour);
    return () => {
      window.removeEventListener(REPLAY_ONBOARDING_EVENT, onReplayOnboarding);
      window.removeEventListener(REPLAY_TOUR_EVENT, onReplayTour);
    };
  }, []);

  return (
    <I18nProvider>
      <LearningProvider>
        <MarketplaceProvider>
          <ToastProvider>
            <BrowserRouter>
              <AppShell
                tourActive={tourActive}
                screenToursSuspended={langSelectOpen || onboardingOpen}
                onTourDone={handleTourDone}
                onTriggerOnboarding={() => {
                  setModelMode('saas');
                  setCurrentModel('claude-sonnet-4-6');
                  setModelSettings(defaultModelSettings);
                  setOnboardingOpen(false);
                  setLangSelectOpen(true);
                }}
                onStopTour={() => setTourActive(false)}
              />
              <LanguageSelectDialog
                open={langSelectOpen}
                onConfirm={() => {
                  setLangSelectOpen(false);
                  setOnboardingOpen(true);
                }}
              />
              {onboardingOpen && (
                <Suspense fallback={<div className="cinematic-onboarding" aria-hidden="true" />}>
                  <ModelOnboarding
                    currentModel={currentModel}
                    modelMode={modelMode}
                    settings={modelSettings}
                    onComplete={handleOnboardingComplete}
                  />
                </Suspense>
              )}
            </BrowserRouter>
          </ToastProvider>
        </MarketplaceProvider>
      </LearningProvider>
    </I18nProvider>
  );
}

export default App;

// Made with Bob
