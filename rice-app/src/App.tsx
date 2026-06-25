import React, { useEffect } from 'react';
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { Layout } from './components/Layout';
import { api } from './lib/api';
import { sseClient } from './lib/sse';
import ChatScreen from '@screens/ChatScreen';
import MySkillsScreen from '@screens/MySkillsScreen';
import MyPluginsScreen from '@screens/MyPluginsScreen';
import SkillMarketScreen from '@screens/SkillMarketScreen';
import PluginMarketScreen from '@screens/PluginMarketScreen';
import ConnectorsScreen from '@screens/ConnectorsScreen';
import SettingsScreen from '@screens/SettingsScreen';
import type { IconRailItem } from './components/carbon';
import { I18nProvider } from './lib/i18n/I18nContext';
import { LearningProvider } from './lib/learning/LearningContext';
import { MarketplaceProvider } from './lib/marketplace/MarketplaceContext';
import { ToastProvider } from './lib/toast/ToastContext';

const RailIcon: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <svg
    width="18"
    height="18"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    {children}
  </svg>
);

const railItems: IconRailItem[] = [
  {
    id: 'chat',
    label: 'Chat',
    path: '/chat',
    icon: (
      <RailIcon>
        <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
      </RailIcon>
    ),
  },
  {
    id: 'my-skills',
    label: 'My skills',
    path: '/my-skills',
    icon: (
      <RailIcon>
        <circle cx="6" cy="9" r="2.4" />
        <circle cx="18" cy="15" r="2.4" />
        <path d="M8.3 9h5.4M10.3 15h5.4" />
        <path d="M13.7 9 16 12M10.3 15 8 12" />
      </RailIcon>
    ),
  },
  {
    id: 'myplugins',
    label: 'My plugins',
    path: '/my-plugins',
    dividerAfter: true,
    icon: (
      <RailIcon>
        <path d="M9 3v5M15 3v5" />
        <path d="M6 8h12v3a6 6 0 0 1-12 0V8z" />
        <path d="M12 17v4" />
      </RailIcon>
    ),
  },
  {
    id: 'skills',
    label: 'Skill market',
    path: '/skills',
    icon: (
      <RailIcon>
        <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
      </RailIcon>
    ),
  },
  {
    id: 'plugins',
    label: 'Plugin market',
    path: '/plugins',
    dividerAfter: true,
    icon: (
      <RailIcon>
        <path d="M19.439 7.85c-.049.322.059.648.289.878l1.568 1.568c.47.47.706 1.087.706 1.704s-.235 1.233-.706 1.704l-1.611 1.611a.98.98 0 0 1-.837.276c-.47-.07-.802-.48-.968-.925a2.501 2.501 0 1 0-3.214 3.214c.446.166.855.497.925.968a.979.979 0 0 1-.276.837l-1.61 1.61a2.404 2.404 0 0 1-1.705.707 2.402 2.402 0 0 1-1.704-.706l-1.568-1.568a1.026 1.026 0 0 0-.877-.29c-.493.074-.84.504-1.02.968a2.5 2.5 0 1 1-3.237-3.237c.464-.18.894-.527.967-1.02a1.026 1.026 0 0 0-.289-.877l-1.568-1.568A2.402 2.402 0 0 1 1.998 12c0-.617.236-1.234.706-1.704L4.23 8.77c.24-.24.581-.353.917-.303.515.077.877.528 1.073 1.01a2.5 2.5 0 1 0 3.259-3.259c-.482-.196-.933-.558-1.01-1.073a1.026 1.026 0 0 1 .303-.917l1.525-1.525A2.402 2.402 0 0 1 12 1.998c.617 0 1.234.236 1.704.706l1.568 1.568c.23.23.556.338.877.29.493-.074.84-.504 1.02-.968a2.5 2.5 0 1 1 3.237 3.237c-.464.18-.894.527-.967 1.02Z" />
      </RailIcon>
    ),
  },
  {
    id: 'connectors',
    label: 'Connectors',
    path: '/connectors',
    icon: (
      <RailIcon>
        <path d="M10 13a5 5 0 0 0 7.07 0l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
        <path d="M14 11a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
      </RailIcon>
    ),
  },
  {
    id: 'settings',
    label: 'Settings',
    path: '/settings',
    pinBottom: true,
    icon: (
      <RailIcon>
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
      </RailIcon>
    ),
  },
];

function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();

  const activeRailId =
    railItems.find((item) => location.pathname === item.path || location.pathname.startsWith(`${item.path}/`))?.id ??
    'chat';

  return (
    <Layout
      railItems={railItems}
      activeRailId={activeRailId}
      onNavigate={(_, path) => navigate(path)}
    >
      <Routes>
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<ChatScreen />} />
        <Route path="/my-skills" element={<MySkillsScreen />} />
        <Route path="/my-plugins" element={<MyPluginsScreen />} />
        <Route path="/skills" element={<SkillMarketScreen />} />
        <Route path="/plugins" element={<PluginMarketScreen />} />
        <Route path="/connectors" element={<ConnectorsScreen />} />
        <Route path="/connectors/:id" element={<ConnectorsScreen />} />
        <Route path="/settings" element={<SettingsScreen />} />
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Routes>
    </Layout>
  );
}

// Minimal shape of the Electron preload bridge we rely on (typed locally so the
// renderer doesn't depend on the preload's global augmentation).
type RuntimeBridge = { getStatus: () => Promise<{ running: boolean; port?: number }> };

function App() {
  // Connect the API/SSE clients to the local runtime only when Electron reports
  // it running (IPC — no network probe). In the design demo (browser, or runtime
  // not spawned) we stay offline: clients no-op and screens use scripted mock
  // data, so no failing localhost requests hit the console.
  useEffect(() => {
    const bridge = (window as { electronAPI?: { runtime?: RuntimeBridge } }).electronAPI?.runtime;
    if (!bridge) return;
    bridge
      .getStatus()
      .then((status) => {
        if (status?.running && status.port) {
          const baseUrl = `http://localhost:${status.port}`;
          api.setBaseUrl(baseUrl);
          api.setEnabled(true);
          sseClient.setBaseUrl(baseUrl);
          sseClient.setEnabled(true);
        }
      })
      .catch(() => {
        /* runtime not reachable — stay offline */
      });
  }, []);

  return (
    <I18nProvider>
      <LearningProvider>
        <MarketplaceProvider>
          <ToastProvider>
            <BrowserRouter>
              <AppShell />
            </BrowserRouter>
          </ToastProvider>
        </MarketplaceProvider>
      </LearningProvider>
    </I18nProvider>
  );
}

export default App;

// Made with Bob
