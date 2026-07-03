import { useEffect, useState } from 'react';
import { Button, Card, Input, Tag, Toggle } from '../components/ui';
import { useI18n } from '../lib/i18n/I18nContext';
import { replayOnboarding, replayTour } from '../lib/tutorial';

/**
 * SettingsScreen - Application preferences
 * Sections: Appearance, API & Runtime, Learning & Privacy, About, Danger zone
 */

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
    // fall through to defaults
  }
  return DEFAULT_SETTINGS;
}

/** Section wrapper: tinted icon chip + title + description + card body */
const SettingsSection: React.FC<{
  title: string;
  description?: string;
  icon: React.ReactNode;
  iconClass?: string;
  children: React.ReactNode;
}> = ({ title, description, icon, iconClass = 'bg-interactive/15 text-blue-40', children }) => (
  <section>
    <div className="flex items-center gap-2.5 mb-1">
      <div
        className={`flex items-center justify-center w-7 h-7 rounded-lg flex-shrink-0 ${iconClass}`}
        aria-hidden="true"
      >
        {icon}
      </div>
      <h2 className="text-heading-02 font-semibold text-text-primary">{title}</h2>
    </div>
    {description && (
      <p className="text-body-short-01 text-text-helper mb-3 ml-[38px]">{description}</p>
    )}
    {!description && <div className="mb-3" />}
    <Card padding="lg" className="space-y-5">
      {children}
    </Card>
  </section>
);

const SectionIcon: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <svg
    width="15"
    height="15"
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

function SettingsScreen() {
  const { language, setLanguage, t } = useI18n();
  const [settings, setSettings] = useState<AppSettings>(loadSettings);
  const [saved, setSaved] = useState(false);
  const [hasChanges, setHasChanges] = useState(false);

  // Sync stored language into the i18n context (no-op once they match)
  useEffect(() => {
    if (settings.language !== language) {
      setLanguage(settings.language);
    }
  }, [settings.language, language, setLanguage]);

  const update = <K extends keyof AppSettings>(key: K, value: AppSettings[K]) => {
    setSettings((prev) => ({ ...prev, [key]: value }));
    setHasChanges(true);

    // If language changes, update i18n context immediately
    if (key === 'language') {
      setLanguage(value as 'en' | 'ko');
    }
  };

  const handleSave = () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    setHasChanges(false);
    setSaved(true);
    window.setTimeout(() => setSaved(false), 2000);
  };

  const handleReset = () => {
    setSettings(DEFAULT_SETTINGS);
    setLanguage(DEFAULT_SETTINGS.language);
    setHasChanges(true);
  };

  return (
    <div className="flex flex-col h-full bg-background">
      {/* Header */}
      {/* 채팅·사이드바와 같은 52px 헤더 라인 */}
      <header className="h-[52px] flex-shrink-0 bg-layer-01 border-b border-border-subtle-01 flex items-center gap-3 px-5">
        <h1 className="text-body-short-02 font-semibold text-text-primary">{t.settings.title}</h1>
        {saved && (
          <Tag type="green" size="sm" className="animate-fade-in">
            {t.settings.saved}
          </Tag>
        )}
        <div className="flex-1" />
        <Button
          kind="primary"
          size="sm"
          onClick={handleSave}
          disabled={!hasChanges}
        >
          {t.settings.saveButton}
        </Button>
      </header>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-[720px] mx-auto px-8 py-8 space-y-8">
          {/* About — hero card */}
          <div className="relative overflow-hidden rounded-xl gradient-brand p-5 text-white">
            <div className="relative flex items-center gap-4">
              <div
                className="
                  flex items-center justify-center w-12 h-12 rounded-2xl
                  bg-white/15 backdrop-blur-sm border border-white/20
                  text-white font-mono font-bold text-heading-03 select-none
                "
                aria-hidden="true"
              >
                R
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-heading-03 font-semibold leading-tight">
                    {t.settings.about.appName}
                  </span>
                  <span className="px-2 py-0.5 rounded-full bg-white/15 text-caption-01 font-medium">
                    {t.settings.about.beta}
                  </span>
                </div>
                <div className="text-body-short-01 text-white/75 truncate">
                  {t.settings.about.appDescription}
                </div>
              </div>
            </div>
          </div>

          {/* Appearance */}
          <SettingsSection
            title={t.settings.appearance.title}
            description={t.settings.appearance.description}
            iconClass="bg-purple-60/15 text-purple-30"
            icon={
              <SectionIcon>
                <circle cx="12" cy="12" r="4" />
                <path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
              </SectionIcon>
            }
          >
            <div>
              <div className="text-label-01 text-text-secondary mb-2">{t.settings.appearance.theme}</div>
              <div className="grid grid-cols-2 gap-1 p-1 bg-field-01 border border-border-subtle-01 rounded-xl max-w-[320px]">
                {([
                  { value: 'dark' as const, label: t.settings.appearance.themeDark, enabled: true },
                  { value: 'light' as const, label: t.settings.appearance.themeLight, enabled: false },
                ]).map(({ value, label, enabled }) => {
                  const isActive = settings.theme === value;
                  return (
                    <button
                      key={value}
                      disabled={!enabled}
                      onClick={() => update('theme', value)}
                      aria-pressed={isActive}
                      className={`flex items-center justify-center gap-1.5 py-2 text-body-short-01 font-semibold rounded-lg border transition-colors duration-150 ${
                        isActive
                          ? 'bg-layer-selected-01 text-text-primary border-interactive'
                          : enabled
                          ? 'bg-transparent text-text-secondary border-transparent hover:bg-layer-hover-01'
                          : 'bg-transparent text-text-placeholder border-transparent cursor-not-allowed'
                      }`}
                    >
                      {value === 'dark' ? (
                        <SectionIcon>
                          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
                        </SectionIcon>
                      ) : (
                        <SectionIcon>
                          <circle cx="12" cy="12" r="4" />
                          <path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
                        </SectionIcon>
                      )}
                      {label}
                    </button>
                  );
                })}
              </div>
            </div>

            <div>
              <div className="text-label-01 text-text-secondary mb-2">
                {t.settings.appearance.language}
              </div>
              <div className="grid grid-cols-2 gap-1 p-1 bg-field-01 border border-border-subtle-01 rounded-xl max-w-[320px]">
                {([
                  { value: 'en' as const, label: 'English' },
                  { value: 'ko' as const, label: '한국어' },
                ]).map(({ value, label }) => {
                  const isActive = settings.language === value;
                  return (
                    <button
                      key={value}
                      onClick={() => update('language', value)}
                      aria-pressed={isActive}
                      className={`py-2 text-body-short-01 font-semibold rounded-lg border transition-colors duration-150 ${
                        isActive
                          ? 'bg-layer-selected-01 text-text-primary border-interactive'
                          : 'bg-transparent text-text-secondary border-transparent hover:bg-layer-hover-01'
                      }`}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
            </div>
            <div>
              <div className="text-label-01 text-text-secondary mb-2">
                {t.settings.appearance.font}
              </div>
              <div className="grid grid-cols-3 gap-1 p-1 bg-field-01 border border-border-subtle-01 rounded-xl max-w-[320px]">
                {([
                  { value: 'system' as const, label: t.settings.appearance.fontSystem },
                  { value: 'mono'   as const, label: t.settings.appearance.fontMono },
                  { value: 'sans'   as const, label: t.settings.appearance.fontSans },
                ]).map(({ value, label }) => {
                  const isActive = settings.font === value;
                  return (
                    <button
                      key={value}
                      onClick={() => update('font', value)}
                      aria-pressed={isActive}
                      className={`py-2 text-body-short-01 font-semibold rounded-lg border transition-colors duration-150 ${
                        isActive
                          ? 'bg-layer-selected-01 text-text-primary border-interactive'
                          : 'bg-transparent text-text-secondary border-transparent hover:bg-layer-hover-01'
                      }`}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
            </div>
          </SettingsSection>

          {/* API & Runtime */}
          <SettingsSection
            title={t.settings.apiRuntime.title}
            description={t.settings.apiRuntime.description}
            iconClass="bg-interactive/15 text-blue-40"
            icon={
              <SectionIcon>
                <rect x="2" y="2" width="20" height="8" rx="2" />
                <rect x="2" y="14" width="20" height="8" rx="2" />
                <path d="M6 6h.01M6 18h.01" />
              </SectionIcon>
            }
          >
            <p className="text-caption-01 text-text-helper -mt-1">
              {language === 'ko'
                ? 'Claude(SaaS) API 키는 채팅의 모델 설정에서 등록·관리합니다.'
                : 'The Claude (SaaS) API key is managed in the chat model settings.'}
            </p>

            <div>
              <label className="text-label-01 text-text-secondary mb-1.5 block">
                {t.settings.apiRuntime.ollamaEndpoint}
              </label>
              <Input
                type="text"
                value={settings.ollamaEndpoint}
                onChange={(e) => update('ollamaEndpoint', e.target.value)}
                placeholder={t.settings.apiRuntime.ollamaPlaceholder}
              />
            </div>

            <Toggle
              checked={settings.runtimeAutoStart}
              onChange={(checked) => update('runtimeAutoStart', checked)}
              label={t.settings.apiRuntime.runtimeAutoStart}
              description={t.settings.apiRuntime.runtimeAutoStartDesc}
            />
          </SettingsSection>

          {/* Learning & Privacy */}
          <SettingsSection
            title={t.settings.learningPrivacy.title}
            description={t.settings.learningPrivacy.description}
            iconClass="bg-support-success/15 text-green-30"
            icon={
              <SectionIcon>
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
              </SectionIcon>
            }
          >
            <Toggle
              checked={settings.userModelingEnabled}
              onChange={(checked) => update('userModelingEnabled', checked)}
              label={t.settings.learningPrivacy.userModeling}
              description={t.settings.learningPrivacy.userModelingDesc}
            />
            <Toggle
              checked={settings.localStorageOnly}
              onChange={(checked) => update('localStorageOnly', checked)}
              disabled={!settings.userModelingEnabled}
              label={t.settings.learningPrivacy.localStorage}
              description={t.settings.learningPrivacy.localStorageDesc}
            />
            <Toggle
              checked={settings.telemetryEnabled}
              onChange={(checked) => update('telemetryEnabled', checked)}
              label={t.settings.learningPrivacy.telemetry}
              description={t.settings.learningPrivacy.telemetryDesc}
            />
          </SettingsSection>

          {/* Tutorial — 온보딩/투어 다시 보기 */}
          <SettingsSection
            title={language === 'ko' ? '튜토리얼' : 'Tutorial'}
            description={
              language === 'ko'
                ? '처음 안내를 놓쳤거나 다시 보고 싶을 때 언제든 재실행할 수 있어요.'
                : 'Replay the first-run guidance anytime you want to revisit it.'
            }
            iconClass="bg-orange-40/15 text-orange-30"
            icon={
              <SectionIcon>
                <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
                <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
              </SectionIcon>
            }
          >
            <div className="flex items-center gap-3">
              <div className="flex-1 min-w-0">
                <div className="text-body-short-02 text-text-primary">
                  {language === 'ko' ? '소개 다시 보기' : 'Replay onboarding'}
                </div>
                <div className="text-caption-01 text-text-helper">
                  {language === 'ko'
                    ? 'RICE 개념과 모델 연결을 단계별로 다시 안내해요.'
                    : 'Walk through the RICE concepts and model setup again.'}
                </div>
              </div>
              <Button kind="secondary" size="sm" onClick={replayOnboarding}>
                {language === 'ko' ? '다시 보기' : 'Replay'}
              </Button>
            </div>
            <div className="flex items-center gap-3">
              <div className="flex-1 min-w-0">
                <div className="text-body-short-02 text-text-primary">
                  {language === 'ko' ? '둘러보기 다시 시작' : 'Restart tour'}
                </div>
                <div className="text-caption-01 text-text-helper">
                  {language === 'ko'
                    ? '화면 곳곳의 기능을 말풍선으로 다시 짚어줘요.'
                    : 'Highlight the key features around the app once more.'}
                </div>
              </div>
              <Button kind="secondary" size="sm" onClick={replayTour}>
                {language === 'ko' ? '다시 시작' : 'Start'}
              </Button>
            </div>
          </SettingsSection>

          {/* Danger zone */}
          <SettingsSection
            title={t.settings.danger.title}
            description={t.settings.danger.description}
            iconClass="bg-red-60/15 text-red-40"
            icon={
              <SectionIcon>
                <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
                <path d="M12 9v4m0 4h.01" />
              </SectionIcon>
            }
          >
            <div className="flex items-center gap-3">
              <div className="flex-1">
                <div className="text-body-short-02 text-text-primary">
                  {t.settings.danger.reset}
                </div>
                <div className="text-caption-01 text-text-helper">
                  {t.settings.danger.resetDesc}
                </div>
              </div>
              <Button kind="danger" size="sm" onClick={handleReset}>
                {t.settings.danger.resetButton}
              </Button>
            </div>
          </SettingsSection>
        </div>
      </div>
    </div>
  );
}

export default SettingsScreen;

// Made with Bob
