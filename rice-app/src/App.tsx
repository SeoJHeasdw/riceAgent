import { lazy, Suspense, useEffect, useRef, useState } from 'react';
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
import { TourOverlay, TOUR_STEPS, TOUR_DONE_KEY } from './components/carbon/tour';
import { CommandPalette } from './components/carbon/CommandPalette';
import { REPLAY_ONBOARDING_EVENT, REPLAY_TOUR_EVENT } from './lib/tutorial';
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
  onTourDone: () => void;
}

function AppShell({ tourActive, onTourDone }: AppShellProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const settingsOpen = location.pathname === '/settings';

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
          <ChatScreen />
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

      {tourActive && (
        <TourOverlay
          steps={TOUR_STEPS}
          currentPath={location.pathname}
          onNavigate={(path) => navigate(path)}
          onDone={onTourDone}
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
          provider?: { kind?: string; model?: string; base_url?: string; temperature?: number; configured?: boolean };
          registered_models?: RegisteredModel[];
        };
        const prov = data?.provider;
        if (!prov || prov.configured) return;

        // configured: false → 온보딩 강제
        const registered = data?.registered_models ?? [];
        const restoredMode: 'saas' | 'local' = prov.kind === 'ollama' ? 'local' : 'saas';
        setModelMode(restoredMode);
        if (prov.model) setCurrentModel(prov.model);
        setModelSettings((prev) => ({
          ...prev,
          registeredModels: registered,
          ...(prov.base_url ? { endpoint: prov.base_url } : {}),
          ...(prov.temperature !== undefined ? { temperature: prov.temperature } : {}),
        }));
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
      },
      registered_models: settings.registeredModels,
    });
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
  useEffect(() => {
    const onReplayOnboarding = () => setLangSelectOpen(true);
    const onReplayTour = () => setTourActive(true);
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
              <AppShell tourActive={tourActive} onTourDone={handleTourDone} />
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
