import { useEffect, useState } from 'react';
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { Layout } from './components/Layout';
import { api } from './lib/api';
import { sseClient } from './lib/sse';
import HomeScreen from '@screens/HomeScreen';
import ChatScreen from '@screens/ChatScreen';
import MySkillsScreen from '@screens/MySkillsScreen';
import MyPluginsScreen from '@screens/MyPluginsScreen';
import SkillMarketScreen from '@screens/SkillMarketScreen';
import PluginMarketScreen from '@screens/PluginMarketScreen';
import ConnectorsScreen from '@screens/ConnectorsScreen';
import SettingsScreen from '@screens/SettingsScreen';
import { toRailItems } from './lib/navigation';
import { I18nProvider } from './lib/i18n/I18nContext';
import { LearningProvider } from './lib/learning/LearningContext';
import { MarketplaceProvider } from './lib/marketplace/MarketplaceContext';
import { ToastProvider } from './lib/toast/ToastProvider';
import { ModelOnboarding } from './components/carbon/ModelOnboarding';
import { defaultModelSettings } from './lib/mockData';
import type { ModelSettings } from './components/carbon/ModelSettingsPanel';
import type { RegisteredModel } from './components/carbon/ModelSettingsPanel';

// 좌측 레일 항목은 공용 네비게이션 매니페스트에서 파생한다(홈 허브와 동일 출처).
const railItems = toRailItems();

function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();

  const activeRailId =
    railItems.find((item) => location.pathname === item.path || location.pathname.startsWith(`${item.path}/`))?.id ??
    'home';

  return (
    <Layout
      railItems={railItems}
      activeRailId={activeRailId}
      onNavigate={(_, path) => navigate(path)}
    >
      <Routes>
        <Route path="/" element={<Navigate to="/home" replace />} />
        <Route path="/home" element={<HomeScreen />} />
        <Route path="/chat" element={<ChatScreen />} />
        <Route path="/my-skills" element={<MySkillsScreen />} />
        <Route path="/my-plugins" element={<MyPluginsScreen />} />
        <Route path="/skills" element={<SkillMarketScreen />} />
        <Route path="/plugins" element={<PluginMarketScreen />} />
        <Route path="/connectors" element={<ConnectorsScreen />} />
        <Route path="/connectors/:id" element={<ConnectorsScreen />} />
        <Route path="/settings" element={<SettingsScreen />} />
        <Route path="*" element={<Navigate to="/home" replace />} />
      </Routes>
    </Layout>
  );
}

// Minimal shape of the Electron preload bridge we rely on (typed locally so the
// renderer doesn't depend on the preload's global augmentation).
type RuntimeBridge = { getStatus: () => Promise<{ running: boolean; port?: number }> };

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
  const [onboardingOpen, setOnboardingOpen] = useState(false);
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
      bridge
        .getStatus()
        .then((status) => {
          if (status?.running && status.port) connect(`http://localhost:${status.port}`);
        })
        .catch(() => {
          /* runtime not reachable — stay offline */
        });
      return;
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
        setOnboardingOpen(true);
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
  };

  return (
    <I18nProvider>
      <LearningProvider>
        <MarketplaceProvider>
          <ToastProvider>
            <BrowserRouter>
              <AppShell />
              {onboardingOpen && (
                <ModelOnboarding
                  currentModel={currentModel}
                  modelMode={modelMode}
                  settings={modelSettings}
                  onComplete={handleOnboardingComplete}
                />
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
