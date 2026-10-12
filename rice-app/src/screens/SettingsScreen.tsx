import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Button, CompanionCloudIcon, ConfirmDialog, Icon, Input, Toggle } from '../components/ui';
import { dialogBackdropCls, dialogSurfaceCls } from '../components/ui/surfaces';
import { useDialogA11y } from '../hooks/useDialogA11y';
import { useI18n } from '../lib/i18n/I18nContext';
import { replayOnboarding, replayTour, RESET_ALL_DATA_EVENT } from '../lib/tutorial';
import { applyAppearance } from '../lib/appearance';
import { platform } from '../lib/api';

interface AppSettings {
  theme: 'dark' | 'light';
  font: 'system' | 'mono' | 'sans';
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
  font: 'system',
  ollamaEndpoint: 'http://localhost:11434',
  runtimeAutoStart: true,
  telemetryEnabled: false,
  userModelingEnabled: true,
  localStorageOnly: true,
};

const STORAGE_KEY = 'rice.settings';
const RICE_STORAGE_PREFIX = 'rice.';

type FactoryResetStatus = {
  running: boolean;
  port?: number;
  token?: string;
};

type RuntimeFactoryResetBridge = {
  factoryReset: () => Promise<FactoryResetStatus>;
};

function loadSettings(): AppSettings {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      const storedSettings = JSON.parse(stored) as Record<string, unknown>;
      // Only current settings are restored so removed fields are not retained
      // or written back to local storage.
      const currentSettings = Object.fromEntries(
        Object.keys(DEFAULT_SETTINGS)
          .filter((key) => key in storedSettings)
          .map((key) => [key, storedSettings[key]]),
      );
      return { ...DEFAULT_SETTINGS, ...currentSettings } as AppSettings;
    }
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
  const [resetDialogOpen, setResetDialogOpen] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  const [resetError, setResetError] = useState(false);
  const savedTimerRef = useRef<number | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const resetButtonRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();

  // ConfirmDialog owns Escape/focus trapping while it is open. Suspending the
  // settings trap prevents one Escape press from closing both stacked dialogs.
  useDialogA11y({
    open: open && !resetDialogOpen,
    dialogRef,
    onClose,
    initialFocusRef: resetButtonRef,
  });

  useEffect(() => {
    applyAppearance({ font: settings.font, theme: settings.theme });
  }, [settings.font, settings.theme]);

  useEffect(() => {
    if (!open) return;
    setActiveSection('general');
    setResetDialogOpen(false);
    setResetBusy(false);
    setResetError(false);
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
  };

  const updateLanguage = (value: typeof language) => {
    setLanguage(value);
    showSaved();
  };

  const handleReset = async () => {
    if (resetBusy) return;
    setResetBusy(true);
    setResetError(false);

    try {
      const runtime = (
        window as { electronAPI?: { runtime?: RuntimeFactoryResetBridge } }
      ).electronAPI?.runtime;
      if (!runtime?.factoryReset) {
        throw new Error('factory reset requires the RICE desktop runtime supervisor');
      }

      // 로컬 앱이 게시한 마켓 artifact도 함께 제거한다. 기본 제공/외부 artifact는
      // managed_by 경계로 제외한다. Skill뿐 아니라 Plugin 게시본도 같은 사용자
      // 행위이므로 둘 다 내린다(platform이 실행 중인 경우).
      const catalogs = await Promise.all([
        platform.getCatalog({ type: 'skill' }),
        platform.getCatalog({ type: 'plugin' }),
      ]);
      const publishedIds = new Set(
        catalogs.flatMap((catalog) => (
          catalog.success && catalog.data
            ? catalog.data
              .filter((artifact) => artifact.managed_by === 'rice-app')
              .map((artifact) => artifact.id)
            : []
        )),
      );
      const unpublishResults = await Promise.all(
        [...publishedIds].map((id) => platform.unpublish(id)),
      );
      if (unpublishResults.some((result) => !result.success)) {
        throw new Error('published artifact reset failed');
      }

      // Supervisor가 keychain 정리 → 런타임 종료 → RICE_HOME 전체 삭제 → 새
      // 런타임 시작을 한 작업으로 수행한다.
      const status = await runtime.factoryReset();
      if (!status.running || !status.port) {
        throw new Error('runtime did not restart after factory reset');
      }

      // ── localStorage 초기화 ──────────────────────────────────────────
      // Settings뿐 아니라 채팅 승인 모드, 패널 폭, 표시 옵션, 온보딩/투어 등
      // RICE의 renderer 캐시를 모두 제거한다.
      try {
        const riceKeys = Array.from(
          { length: localStorage.length },
          (_, index) => localStorage.key(index),
        ).filter((key): key is string => !!key && key.startsWith(RICE_STORAGE_PREFIX));
        riceKeys.forEach((key) => localStorage.removeItem(key));
      } catch {
        // Hardened browser contexts may not expose localStorage. Runtime reset is
        // still authoritative, and the current in-memory state resets below.
      }
      setSettings(DEFAULT_SETTINGS);
      const browserLanguage = navigator.language.toLowerCase();
      const firstInstallLanguage: typeof language = browserLanguage.startsWith('ko')
        ? 'ko'
        : browserLanguage.startsWith('ja')
          ? 'ja'
          : 'en';
      setLanguage(firstInstallLanguage);
      // setLanguage는 현재 화면 동기화를 위해 rice.language를 한 번 기록한다.
      // 공장 초기화 결과는 첫 설치와 같아야 하므로 그 캐시도 다시 제거한다.
      try {
        localStorage.removeItem('rice.language');
      } catch {
        // Runtime data reset is authoritative.
      }

      // Settings is route-backed. Close it before opening onboarding so the two
      // modal surfaces never remain stacked after a successful reset.
      setResetDialogOpen(false);
      onClose();
      window.dispatchEvent(new CustomEvent(RESET_ALL_DATA_EVENT));
    } catch (error) {
      console.error('RICE data reset failed', error);
      setResetError(true);
    } finally {
      setResetBusy(false);
    }
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
              value={language}
              ariaLabel={t('appearance.language')}
              onChange={updateLanguage}
              options={[
                { value: 'en', label: 'English' },
                { value: 'ko', label: '한국어' },
                { value: 'ja', label: '日本語' },
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
        <div className="gradient-brand relative overflow-hidden rounded-xl border border-white/15 p-5 text-white shadow-elevation-1">
          <div className="absolute inset-0 bg-black/[0.08]" aria-hidden="true" />
          <div className="absolute -right-8 -top-10 h-32 w-32 rounded-full bg-white/10 blur-2xl" aria-hidden="true" />
          <div className="absolute inset-x-0 bottom-0 h-px bg-white/10" aria-hidden="true" />
          <div className="relative flex items-center gap-4">
            <div className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-xl border border-white/20 bg-white/10 shadow-elevation-1">
              <CompanionCloudIcon className="h-7 w-8 text-white" />
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

            <div className="flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center">
              <div className="min-w-0 flex-1">
                <div className="text-body-short-01 font-semibold text-text-primary">{t('danger.reset')}</div>
              </div>
              <Button
                ref={resetButtonRef}
                kind="danger"
                size="sm"
                icon={<Icon name="trash" size={14} />}
                onClick={() => {
                  setResetError(false);
                  setResetDialogOpen(true);
                }}
              >
                {t('danger.resetButton')}
              </Button>
            </div>
          </div>
        </section>
      </div>
    ),
  }[activeSection];

  return createPortal(
    <>
      <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
        <div className={dialogBackdropCls} onClick={onClose} aria-hidden="true" />

        <div
          ref={dialogRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          aria-hidden={resetDialogOpen || undefined}
          tabIndex={-1}
          className={`relative flex h-[min(700px,calc(100vh-32px))] min-h-0 w-[min(920px,calc(100vw-32px))] flex-col overflow-hidden sm:flex-row ${dialogSurfaceCls}`}
        >
        <aside className="flex w-full flex-shrink-0 flex-col border-b border-border-subtle-01 bg-field-01 sm:w-[210px] sm:border-b-0 sm:border-r">
          <div className="flex h-[58px] items-center gap-2.5 border-b border-border-subtle-01 px-4 sm:h-[70px]">
            <CompanionCloudIcon className="h-7 w-8 flex-shrink-0 text-text-secondary" />
            <div className="min-w-0">
              <div className="text-body-short-01 font-semibold text-text-primary">RICE</div>
              <div className="text-label-micro text-text-helper">{t('about.beta')} · v0.1.0</div>
            </div>
          </div>

          <nav className="flex gap-1 overflow-x-auto p-2 sm:flex-col sm:overflow-visible sm:p-3 sm:pt-4" aria-label={t('title')}>
            {sections.map((section) => {
              const active = section.id === activeSection;
              return (
                <button
                  key={section.id}
                  type="button"
                  onClick={() => {
                    setActiveSection(section.id);
                    setResetDialogOpen(false);
                    setResetError(false);
                  }}
                  aria-current={active ? 'page' : undefined}
                  className={`relative flex min-w-max items-center gap-2.5 rounded-lg border px-3 py-2 text-left transition-colors duration-150 sm:min-w-0 ${
                    active
                      ? 'border-blue-50/15 bg-layer-selected-01 text-text-primary'
                      : 'border-transparent text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary'
                  }`}
                >
                  <span className={`flex h-7 w-7 flex-shrink-0 items-center justify-center ${active ? 'text-blue-40' : 'text-icon-secondary'}`}>
                    <Glyph name={section.icon} size={15} />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-body-short-01 font-medium">{section.title}</span>
                    <span className="hidden truncate text-label-micro text-text-helper sm:block">{section.description}</span>
                  </span>
                  {active && <span className="absolute inset-y-2.5 left-0 w-0.5 rounded-full bg-interactive" aria-hidden="true" />}
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
      </div>
      <ConfirmDialog
        open={resetDialogOpen}
        title={t(resetError ? 'danger.resetFailedTitle' : 'danger.reset')}
        description={(
          <p className={resetError ? 'font-medium text-text-error' : undefined} role={resetError ? 'alert' : undefined}>
            {t(resetError ? 'danger.resetFailed' : 'danger.resetConfirm')}
          </p>
        )}
        confirmLabel={t(resetError ? 'danger.retryReset' : 'danger.confirmReset')}
        cancelLabel={t('danger.cancel')}
        confirmIcon={resetError ? undefined : 'trash'}
        tone="danger"
        busy={resetBusy}
        stackingClassName="z-[110]"
        onConfirm={() => void handleReset()}
        onClose={() => {
          setResetError(false);
          setResetDialogOpen(false);
        }}
      />
    </>,
    document.body,
  );
}

export default SettingsScreen;
