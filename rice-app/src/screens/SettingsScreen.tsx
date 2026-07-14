import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Button, Icon, Input, Toggle } from '../components/ui';
import { dialogBackdropCls, dialogSurfaceCls } from '../components/ui/surfaces';
import { useDialogA11y } from '../hooks/useDialogA11y';
import { useI18n } from '../lib/i18n/I18nContext';
import { replayOnboarding, replayTour, RESET_ALL_DATA_EVENT } from '../lib/tutorial';
import { applyAppearance } from '../lib/appearance';
import { api } from '../lib/api';
import { TOUR_DONE_KEY } from '../components/carbon/tour';

interface AppSettings {
  theme: 'dark' | 'light';
  language: 'en' | 'ko';
  font: 'system' | 'mono' | 'sans';
  watsonxApiKey: string;
  ollamaEndpoint: string;
  runtimeAutoStart: boolean;
  telemetryEnabled: boolean;
  userModelingEnabled: boolean;
  localStorageOnly: boolean;
}

interface SettingsScreenProps {
  open: boolean;
  onClose: () => void;
}

type SettingsSectionId = 'general' | 'privacy' | 'about';
type GlyphName = 'general' | 'privacy' | 'about' | 'sun' | 'moon';

const DEFAULT_SETTINGS: AppSettings = {
  theme: 'dark',
  language: 'en',
  font: 'system',
  watsonxApiKey: '',
  ollamaEndpoint: 'http://localhost:11434',
  runtimeAutoStart: true,
  telemetryEnabled: false,
  userModelingEnabled: true,
  localStorageOnly: true,
};

const STORAGE_KEY = 'rice.settings';

function loadSettings(): AppSettings {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) return { ...DEFAULT_SETTINGS, ...JSON.parse(stored) };
  } catch {
    // Invalid or unavailable local storage falls back to safe defaults.
  }
  return DEFAULT_SETTINGS;
}

const Glyph: React.FC<{ name: GlyphName; size?: number; className?: string }> = ({
  name,
  size = 17,
  className = '',
}) => {
  const paths: Record<GlyphName, React.ReactNode> = {
    general: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.9 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1A2 2 0 1 1 4.3 17l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.9-2.8l.1.1a1.7 1.7 0 0 0 1.8.3 1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1A2 2 0 1 1 19.7 7l-.1.1a1.7 1.7 0 0 0-.3 1.8 1.7 1.7 0 0 0 1.5 1h.2a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" />
      </>
    ),
    privacy: (
      <>
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" />
        <path d="m9 12 2 2 4-4" />
      </>
    ),
    about: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 11v5M12 8h.01" />
      </>
    ),
    sun: (
      <>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </>
    ),
    moon: <path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z" />,
  };

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      {paths[name]}
    </svg>
  );
};

interface SegmentOption<T extends string> {
  value: T;
  label: string;
  icon?: React.ReactNode;
}

function SegmentedControl<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
}: {
  value: T;
  options: SegmentOption<T>[];
  onChange: (value: T) => void;
  ariaLabel: string;
}) {
  return (
    <div
      className="grid gap-1 rounded-lg border border-border-subtle-01 bg-field-01 p-1"
      style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))` }}
      role="group"
      aria-label={ariaLabel}
    >
      {options.map((option) => {
        const active = value === option.value;
        return (
          <button
            key={option.value}
            type="button"
            onClick={() => onChange(option.value)}
            aria-pressed={active}
            className={`flex h-8 min-w-0 items-center justify-center gap-1.5 rounded-md border px-2 text-caption-01 font-medium transition-colors ${
              active
                ? 'border-border-strong-01/70 bg-layer-selected-01 text-text-primary shadow-elevation-1'
                : 'border-transparent text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary'
            }`}
          >
            {option.icon}
            <span className="truncate">{option.label}</span>
          </button>
        );
      })}
    </div>
  );
}

const SettingsGroup: React.FC<{
  title: string;
  description?: string;
  children: React.ReactNode;
}> = ({ title, description, children }) => (
  <section className="space-y-2.5">
    <div>
      <h3 className="text-body-short-01 font-semibold text-text-primary">{title}</h3>
      {description && <p className="mt-0.5 text-caption-01 text-text-helper">{description}</p>}
    </div>
    <div className="overflow-hidden rounded-xl border border-border-subtle-01 bg-layer-01 divide-y divide-border-subtle-01">
      {children}
    </div>
  </section>
);

const SettingRow: React.FC<{
  title: string;
  description?: string;
  children: React.ReactNode;
  alignStart?: boolean;
}> = ({ title, description, children, alignStart = false }) => (
  <div className={`flex flex-col gap-3 px-4 py-3.5 sm:flex-row sm:justify-between ${alignStart ? 'sm:items-start' : 'sm:items-center'}`}>
    <div className="min-w-0 sm:max-w-[290px]">
      <div className="text-body-short-01 text-text-primary">{title}</div>
      {description && <div className="mt-0.5 text-caption-01 leading-5 text-text-helper">{description}</div>}
    </div>
    <div className="w-full flex-shrink-0 sm:w-[300px]">{children}</div>
  </div>
);

function SettingsScreen({ open, onClose }: SettingsScreenProps) {
  const { language, setLanguage } = useI18n();
  const { t } = useTranslation('settings');
  const [settings, setSettings] = useState<AppSettings>(loadSettings);
  const [activeSection, setActiveSection] = useState<SettingsSectionId>('general');
  const [saved, setSaved] = useState(false);
  const [resetConfirming, setResetConfirming] = useState(false);
  const savedTimerRef = useRef<number | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const initialFocusRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();

  useDialogA11y({ open, dialogRef, onClose, initialFocusRef });

  useEffect(() => {
    if (settings.language !== language) setLanguage(settings.language);
  }, [settings.language, language, setLanguage]);

  useEffect(() => {
    applyAppearance({ font: settings.font, theme: settings.theme });
  }, [settings.font, settings.theme]);

  useEffect(() => {
    if (!open) return;
    setActiveSection('general');
    setResetConfirming(false);
  }, [open]);

  useEffect(
    () => () => {
      if (savedTimerRef.current) window.clearTimeout(savedTimerRef.current);
    },
    [],
  );

  const showSaved = () => {
    setSaved(true);
    if (savedTimerRef.current) window.clearTimeout(savedTimerRef.current);
    savedTimerRef.current = window.setTimeout(() => setSaved(false), 1800);
  };

  const persist = (next: AppSettings) => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      showSaved();
    } catch {
      // The current session still reflects the choice when storage is unavailable.
    }
  };

  const update = <K extends keyof AppSettings>(key: K, value: AppSettings[K]) => {
    const next = { ...settings, [key]: value };
    setSettings(next);
    persist(next);
    if (key === 'language') setLanguage(value as AppSettings['language']);
  };

  const handleReset = async () => {
    setResetConfirming(false);

    // ── 런타임 데이터 삭제 (온라인일 때만) ──────────────────────────────
    if (api.isEnabled) {
      // 각 카테고리를 병렬 조회 후 개별 삭제 — 일부 실패해도 계속 진행
      const [sessions, skills, plugins, memories, connectors, localConnectors] = await Promise.allSettled([
        api.listSessions(),
        api.getSkills(),
        api.getPlugins(),
        api.getMemories(),
        api.getConnectors(),
        api.getLocalConnectors(),
      ]);

      const deletions: Promise<unknown>[] = [];

      if (sessions.status === 'fulfilled' && sessions.value.success) {
        const list = sessions.value.data as { id: string }[] | undefined ?? [];
        list.forEach((s) => deletions.push(api.deleteSession(s.id)));
      }
      if (skills.status === 'fulfilled' && skills.value.success) {
        const list = skills.value.data as { id: string; source?: string | null }[] | undefined ?? [];
        list.filter((s) => s.source !== 'builtin').forEach((s) => deletions.push(api.deleteSkill(s.id)));
      }
      if (plugins.status === 'fulfilled' && plugins.value.success) {
        const list = plugins.value.data as { id: string; builtin?: boolean }[] | undefined ?? [];
        list.filter((p) => !p.builtin).forEach((p) => deletions.push(api.deletePlugin(p.id)));
      }
      if (memories.status === 'fulfilled' && memories.value.success) {
        type MemItem = { id: string };
        const raw = memories.value.data as { memories?: MemItem[] } | MemItem[] | undefined;
        const list: MemItem[] = Array.isArray(raw) ? raw : (raw?.memories ?? []);
        list.forEach((m) => deletions.push(api.deleteMemory(m.id)));
      }
      if (connectors.status === 'fulfilled' && connectors.value.success) {
        const list = connectors.value.data as { id: string }[] | undefined ?? [];
        list.forEach((c) => deletions.push(api.deleteConnector(c.id)));
      }
      if (localConnectors.status === 'fulfilled' && localConnectors.value.success) {
        const list = localConnectors.value.data as { name: string }[] | undefined ?? [];
        list.forEach((c) => deletions.push(api.deleteLocalConnector(c.name)));
      }

      await Promise.allSettled(deletions);

      // 런타임 설정 리셋 → 재시작 시 온보딩 재트리거
      await api.updateSettings({ provider: { configured: false }, registered_models: [] });
    }

    // ── localStorage 초기화 ────────────────────────────────────────────
    localStorage.removeItem(TOUR_DONE_KEY);
    setSettings(DEFAULT_SETTINGS);
    setLanguage(DEFAULT_SETTINGS.language);
    persist(DEFAULT_SETTINGS);

    // ── 인메모리 상태 리셋 + 온보딩 재트리거 ──────────────────────────
    window.dispatchEvent(new CustomEvent(RESET_ALL_DATA_EVENT));
  };

  const launchTutorial = (action: () => void) => {
    onClose();
    window.setTimeout(action, 120);
  };

  if (!open) return null;

  const sections: Array<{
    id: SettingsSectionId;
    title: string;
    description: string;
    icon: GlyphName;
  }> = [
    { id: 'general', title: t('nav.general'), description: t('nav.generalDesc'), icon: 'general' },
    { id: 'privacy', title: t('nav.privacy'), description: t('nav.privacyDesc'), icon: 'privacy' },
    { id: 'about', title: t('nav.about'), description: t('nav.aboutDesc'), icon: 'about' },
  ];
  const currentSection = sections.find((section) => section.id === activeSection) ?? sections[0];

  const content = {
    general: (
      <div className="space-y-6">
        <SettingsGroup title={t('appearance.title')} description={t('appearance.description')}>
          <SettingRow title={t('appearance.theme')}>
            <SegmentedControl
              value={settings.theme}
              ariaLabel={t('appearance.theme')}
              onChange={(value) => update('theme', value)}
              options={[
                { value: 'dark', label: t('appearance.themeDark'), icon: <Glyph name="moon" size={14} /> },
                { value: 'light', label: t('appearance.themeLight'), icon: <Glyph name="sun" size={14} /> },
              ]}
            />
          </SettingRow>
          <SettingRow title={t('appearance.language')}>
            <SegmentedControl
              value={settings.language}
              ariaLabel={t('appearance.language')}
              onChange={(value) => update('language', value)}
              options={[
                { value: 'en', label: 'English' },
                { value: 'ko', label: '한국어' },
              ]}
            />
          </SettingRow>
          <SettingRow title={t('appearance.font')}>
            <SegmentedControl
              value={settings.font}
              ariaLabel={t('appearance.font')}
              onChange={(value) => update('font', value)}
              options={[
                { value: 'system', label: t('appearance.fontSystem') },
                { value: 'mono', label: t('appearance.fontMono') },
                { value: 'sans', label: t('appearance.fontSans') },
              ]}
            />
          </SettingRow>
        </SettingsGroup>

        <SettingsGroup title={t('apiRuntime.title')} description={t('apiRuntime.description')}>
          <div className="flex items-start gap-3 bg-blue-60/5 px-4 py-3.5">
            <span className="mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg bg-blue-60/12 text-blue-40">
              <Icon name="model" size={15} />
            </span>
            <div>
              <div className="text-body-short-01 font-medium text-text-primary">{t('apiRuntime.modelSettingsTitle')}</div>
              <p className="mt-0.5 text-caption-01 leading-5 text-text-helper">{t('apiRuntime.claudeManagedInChat')}</p>
            </div>
          </div>
          <SettingRow
            title={t('apiRuntime.ollamaEndpoint')}
            description={t('apiRuntime.ollamaHelper')}
            alignStart
          >
            <Input
              type="text"
              size="sm"
              value={settings.ollamaEndpoint}
              onChange={(event) => update('ollamaEndpoint', event.target.value)}
              placeholder={t('apiRuntime.ollamaPlaceholder')}
            />
          </SettingRow>
          <div className="px-4 py-3.5">
            <Toggle
              checked={settings.runtimeAutoStart}
              onChange={(checked) => update('runtimeAutoStart', checked)}
              label={t('apiRuntime.runtimeAutoStart')}
              description={t('apiRuntime.runtimeAutoStartDesc')}
            />
          </div>
        </SettingsGroup>
      </div>
    ),
    privacy: (
      <div className="space-y-6">
        <div className="relative overflow-hidden rounded-xl border border-green-40/25 bg-support-success/5 p-4">
          <div className="absolute inset-y-0 left-0 w-0.5 bg-support-success" aria-hidden="true" />
          <div className="flex items-start gap-3">
            <span className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-support-success/12 text-green-30">
              <Glyph name="privacy" size={17} />
            </span>
            <div>
              <h3 className="text-body-short-01 font-semibold text-text-primary">{t('learningPrivacy.localFirstTitle')}</h3>
              <p className="mt-1 text-caption-01 leading-5 text-text-secondary">{t('learningPrivacy.localFirstDesc')}</p>
            </div>
          </div>
        </div>

        <SettingsGroup title={t('learningPrivacy.title')} description={t('learningPrivacy.description')}>
          <div className="px-4 py-3.5">
            <Toggle
              checked={settings.userModelingEnabled}
              onChange={(checked) => update('userModelingEnabled', checked)}
              label={t('learningPrivacy.userModeling')}
              description={t('learningPrivacy.userModelingDesc')}
              tone="purple"
            />
          </div>
          <div className="px-4 py-3.5">
            <Toggle
              checked={settings.localStorageOnly}
              onChange={(checked) => update('localStorageOnly', checked)}
              disabled={!settings.userModelingEnabled}
              label={t('learningPrivacy.localStorage')}
              description={t('learningPrivacy.localStorageDesc')}
              tone="green"
            />
          </div>
          <div className="px-4 py-3.5">
            <Toggle
              checked={settings.telemetryEnabled}
              onChange={(checked) => update('telemetryEnabled', checked)}
              label={t('learningPrivacy.telemetry')}
              description={t('learningPrivacy.telemetryDesc')}
            />
          </div>
        </SettingsGroup>
      </div>
    ),
    about: (
      <div className="space-y-6">
        <div className="gradient-brand relative overflow-hidden rounded-xl border border-white/10 p-5 text-white">
          <div className="absolute -right-8 -top-10 h-32 w-32 rounded-full bg-white/10 blur-2xl" aria-hidden="true" />
          <div className="relative flex items-center gap-4">
            <div className="flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-2xl border border-white/20 bg-white/15 font-mono text-heading-03 font-bold shadow-elevation-1">
              R
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <h3 className="text-heading-03 font-semibold">{t('about.appName')}</h3>
                <span className="rounded-full bg-white/15 px-2 py-0.5 text-label-micro font-semibold">{t('about.beta')}</span>
              </div>
              <p className="mt-0.5 text-caption-01 text-white/75">{t('about.appDescription')}</p>
            </div>
          </div>
        </div>

        <SettingsGroup title={t('tutorial.title')} description={t('tutorial.description')}>
          <div className="flex items-center gap-3 px-4 py-3.5">
            <span className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-orange-40/12 text-orange-30">
              <Icon name="model" size={16} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-body-short-01 text-text-primary">{t('tutorial.replayOnboarding')}</div>
              <div className="mt-0.5 text-caption-01 text-text-helper">{t('tutorial.replayOnboardingDesc')}</div>
            </div>
            <Button kind="secondary" size="sm" onClick={() => launchTutorial(replayOnboarding)}>
              {t('tutorial.replay')}
            </Button>
          </div>
          <div className="flex items-center gap-3 px-4 py-3.5">
            <span className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-purple-60/12 text-purple-40">
              <Icon name="idea" size={16} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-body-short-01 text-text-primary">{t('tutorial.restartTour')}</div>
              <div className="mt-0.5 text-caption-01 text-text-helper">{t('tutorial.restartTourDesc')}</div>
            </div>
            <Button kind="secondary" size="sm" onClick={() => launchTutorial(replayTour)}>
              {t('tutorial.start')}
            </Button>
          </div>
        </SettingsGroup>

        <section className="space-y-2.5">
          <div>
            <h3 className="text-body-short-01 font-semibold text-text-error">{t('danger.title')}</h3>
            <p className="mt-0.5 text-caption-01 text-text-helper">{t('danger.description')}</p>
          </div>

          <div className="overflow-hidden rounded-xl border border-support-error/45 bg-support-error/5">
            <div className="flex items-start gap-3 border-b border-support-error/25 bg-support-error/10 px-4 py-3.5">
              <span className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-support-error/15 text-red-40">
                <Icon name="warning" size={17} />
              </span>
              <div className="min-w-0">
                <div className="text-body-short-01 font-semibold text-text-error">{t('danger.irreversible')}</div>
                <p className="mt-1 text-caption-01 leading-5 text-text-secondary">{t('danger.resetDesc')}</p>
              </div>
            </div>

            <div className={`flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center ${resetConfirming ? 'bg-support-error/10' : ''}`}>
              <div className="min-w-0 flex-1">
                <div className="text-body-short-01 font-semibold text-text-primary">{t('danger.reset')}</div>
                {resetConfirming && (
                  <div className="mt-1 text-caption-01 font-medium leading-5 text-text-error" role="alert">
                    {t('danger.resetConfirm')}
                  </div>
                )}
              </div>
              {resetConfirming ? (
                <div className="flex flex-wrap items-center justify-end gap-1.5">
                  <Button kind="ghost" size="sm" onClick={() => setResetConfirming(false)}>
                    {t('danger.cancel')}
                  </Button>
                  <Button
                    kind="danger"
                    size="sm"
                    icon={<Icon name="trash" size={14} />}
                    onClick={handleReset}
                  >
                    {t('danger.confirmReset')}
                  </Button>
                </div>
              ) : (
                <Button
                  kind="danger"
                  size="sm"
                  icon={<Icon name="trash" size={14} />}
                  onClick={() => setResetConfirming(true)}
                >
                  {t('danger.resetButton')}
                </Button>
              )}
            </div>
          </div>
        </section>
      </div>
    ),
  }[activeSection];

  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div className={dialogBackdropCls} onClick={onClose} aria-hidden="true" />

      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`relative flex h-[min(700px,calc(100vh-32px))] min-h-0 w-[min(920px,calc(100vw-32px))] flex-col overflow-hidden sm:flex-row ${dialogSurfaceCls}`}
      >
        <aside className="flex w-full flex-shrink-0 flex-col border-b border-border-subtle-01 bg-field-01 sm:w-[210px] sm:border-b-0 sm:border-r">
          <div className="flex h-[58px] items-center gap-2.5 border-b border-border-subtle-01 px-4 sm:h-[70px] sm:border-b-0">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-blue-60 to-purple-60 font-mono text-body-short-01 font-bold text-white shadow-elevation-1">
              R
            </div>
            <div className="min-w-0">
              <div className="text-body-short-01 font-semibold text-text-primary">RICE</div>
              <div className="text-label-micro text-text-helper">{t('about.beta')} · v0.1.0</div>
            </div>
          </div>

          <nav className="flex gap-1 overflow-x-auto p-2 sm:flex-col sm:overflow-visible sm:p-3" aria-label={t('title')}>
            {sections.map((section, index) => {
              const active = section.id === activeSection;
              return (
                <button
                  key={section.id}
                  ref={index === 0 ? initialFocusRef : undefined}
                  type="button"
                  onClick={() => {
                    setActiveSection(section.id);
                    setResetConfirming(false);
                  }}
                  aria-current={active ? 'page' : undefined}
                  className={`relative flex min-w-max items-center gap-2.5 rounded-lg px-3 py-2 text-left transition-colors sm:min-w-0 ${
                    active
                      ? 'bg-layer-selected-01 text-text-primary'
                      : 'text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary'
                  }`}
                >
                  <span className={`flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg ${active ? 'bg-interactive/12 text-blue-40' : 'text-icon-secondary'}`}>
                    <Glyph name={section.icon} size={15} />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-body-short-01 font-medium">{section.title}</span>
                    <span className="hidden truncate text-label-micro text-text-helper sm:block">{section.description}</span>
                  </span>
                  {active && <span className="absolute inset-y-2 left-0 w-0.5 rounded-full bg-interactive" aria-hidden="true" />}
                </button>
              );
            })}
          </nav>

          <div className="mt-auto hidden border-t border-border-subtle-01 p-4 text-label-micro leading-5 text-text-helper sm:block">
            {t('autoSave')}
          </div>
        </aside>

        <div className="flex min-h-0 min-w-0 flex-1 flex-col bg-background">
          <header className="flex h-[70px] flex-shrink-0 items-center gap-3 border-b border-border-subtle-01 bg-layer-01 px-5 sm:px-6">
            <div className="min-w-0 flex-1">
              <h2 id={titleId} className="text-heading-02 font-semibold text-text-primary">{currentSection.title}</h2>
              <p className="mt-0.5 truncate text-caption-01 text-text-helper">{currentSection.description}</p>
            </div>
            <div
              className={`hidden items-center gap-1.5 text-caption-01 transition-colors sm:flex ${saved ? 'text-green-30' : 'text-text-helper'}`}
              role="status"
              aria-live="polite"
            >
              {saved ? <Icon name="check-circle" size={14} /> : <span className="h-1.5 w-1.5 rounded-full bg-support-success" />}
              {saved ? t('saved') : t('autoSaveShort')}
            </div>
            <button
              type="button"
              onClick={onClose}
              className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg text-icon-secondary transition-colors hover:bg-layer-hover-01 hover:text-icon-primary"
              aria-label={t('close')}
            >
              <Icon name="close" size={16} />
            </button>
          </header>

          <div className="flex-1 overflow-y-auto px-5 py-5 sm:px-7 sm:py-6">
            <div className="mx-auto max-w-[640px]">{content}</div>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}

export default SettingsScreen;

// Made with Bob
