import React, { useEffect, useRef, useState } from 'react';
import { useTranslation, Trans } from 'react-i18next';
import { Button, Icon, Input, Tag, Toggle } from '../ui';
import { ModelRegisterModal, RegisterPayload } from './ModelRegisterModal';
import { useToast } from '../../lib/toast/ToastContext';
import { api } from '../../lib/api';

/**
 * ModelPicker — 모델 선택의 공용 본문.
 *
 * "실행 위치(SaaS/Local) → 모델 선택 → 파라미터"를 한 덩어리로 캡슐화한다.
 * 모델 설정 슬라이드 패널(ModelSettingsPanel)과 최초 실행 온보딩 화면
 * (ModelOnboarding) 양쪽에서 동일하게 사용한다 (중복 제거).
 *
 * - SaaS: 내장 카탈로그 없음. 사용자가 ModelRegisterModal로 (모델, API 키)를
 *         직접 등록한다 — Local에서 ollama 모델을 불러오는 것과 동일한 흐름.
 * - Local: Ollama 엔드포인트에서 설치된 모델을 라이브로 불러온다.
 *
 * 내부 draft 상태를 직접 들고, 변경될 때마다 onChange로 상위에 올려보낸다.
 * (저장 가능 여부 canSave 포함 → 상위가 저장/시작 버튼을 게이팅)
 */

export interface RegisteredModel {
  /** User-facing model identifier, e.g. "claude-opus-4-8" */
  modelId: string;
  /** Masked / stored API key */
  apiKey: string;
  /** Provider id (anthropic | openai | watsonx | custom) */
  provider?: string;
  /** Provider display label, e.g. "Anthropic" */
  providerLabel?: string;
  /** OpenAI-compatible custom endpoint / watsonx region endpoint */
  baseUrl?: string;
  /** watsonx project id */
  projectId?: string;
}

export interface ModelSettings {
  temperature: number;
  /** N개의 (모델, API 키) 쌍 */
  registeredModels: RegisteredModel[];
  endpoint?: string;
  userModelingEnabled: boolean;
  localStorageOnly: boolean;
  /** Local LLM이 보내는 thinking trace를 대화 화면에 표시(스트리밍)할지 여부.
   * 백엔드 provider config의 show_thinking과 짝(카멜/스네이크). */
  showThinking: boolean;
}

/** 모델 식별용 경량 타입 (현재는 SaaS 등록 모델 렌더에만 사용) */
export interface Model {
  id: string;
  name: string;
  description: string;
  badge?: string;
}

export interface ModelDraft {
  modelId: string;
  settings: ModelSettings;
  mode: 'saas' | 'local';
  /** 선택한 모델을 실제로 사용할 수 있는 상태인가 (SaaS=키 등록, Local=설치 확인) */
  canSave: boolean;
}

export interface ModelPickerProps {
  currentModel: string;
  modelMode: 'saas' | 'local';
  settings: ModelSettings;
  /** 온보딩 모드: 이미 활성 모델이라는 이유만으로 저장을 허용하지 않는다 */
  onboarding?: boolean;
  /** draft가 바뀔 때마다(저장 가능 여부 포함) 호출 */
  onChange: (draft: ModelDraft) => void;
}

/** Ollama 모델 정보 (GET /ollama/models 응답 항목) */
interface OllamaModel {
  name: string;
  parameter_size: string;
  quantization_level: string;
  context_length: number | null;
  size_bytes: number;
  capabilities: string[];
}

/** bytes → 사람이 읽기 쉬운 크기 문자열 */
const fmtBytes = (b: number): string => {
  if (b >= 1e9) return `${(b / 1e9).toFixed(1)} GB`;
  if (b >= 1e6) return `${(b / 1e6).toFixed(0)} MB`;
  return `${b} B`;
};

const maskKey = (k: string) => (k.length > 12 ? `${k.slice(0, 7)}…${k.slice(-4)}` : '••••••');

export const ModelPicker: React.FC<ModelPickerProps> = ({
  currentModel,
  modelMode,
  settings,
  onboarding = false,
  onChange,
}) => {
  const { t } = useTranslation('settings');
  const [localSettings, setLocalSettings] = useState(settings);
  const [draftModel, setDraftModel] = useState(currentModel);
  const [draftMode, setDraftMode] = useState(modelMode);
  const [connTest, setConnTest] = useState<'idle' | 'testing' | 'ok' | 'fail'>('idle');
  const toast = useToast();

  // 모델 등록 모달 (SaaS) — 폼 상태는 ModelRegisterModal 내부로 캡슐화
  const [registerModalOpen, setRegisterModalOpen] = useState(false);

  // Local: Ollama 모델 목록 fetch 상태
  // null = 아직 확인 전(가림), [] = 확인했으나 없음, [...] = 목록 있음
  const [ollamaModels, setOllamaModels] = useState<OllamaModel[] | null>(null);
  const [ollamaFetching, setOllamaFetching] = useState(false);

  // SaaS 모델 = 사용자가 등록한 (모델, 키) 목록. 내장 카탈로그 없음.
  const saasModels = localSettings.registeredModels;

  // 사용 가능 여부: SaaS=키 등록, Local=설치 확인.
  // (이미 활성화된 모델은 일반 모드에서 재등록 없이 파라미터만 저장 허용)
  const draftHasKey = saasModels.some((r) => r.modelId === draftModel);
  const localUsable = !!ollamaModels?.some((m) => m.name === draftModel);
  const canSave =
    draftMode === 'local' ? localUsable : draftHasKey || (!onboarding && draftModel === currentModel);

  // draft 변경을 상위로 전달 (onChange는 ref로 고정 → effect 루프 방지)
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  useEffect(() => {
    onChangeRef.current({ modelId: draftModel, settings: localSettings, mode: draftMode, canSave });
  }, [draftModel, draftMode, localSettings, canSave]);

  // Ollama 로컬 모델 목록 fetch
  const fetchOllamaModels = async () => {
    setOllamaFetching(true);
    const job = toast.progress({
      label: t('modelPicker.toast.fetchingModels'),
      sublabel: localSettings.endpoint || 'http://localhost:11434',
    });
    try {
      const res = await api.getOllamaModels();
      if (res.success && Array.isArray(res.data)) {
        const list = res.data as OllamaModel[];
        setOllamaModels(list);
        job.done('success', t('modelPicker.toast.modelsFound', { count: list.length }));
        if (list.length > 0 && !list.some((m) => m.name === draftModel)) {
          setDraftModel(list[0].name);
        }
      } else {
        setOllamaModels([]);
        job.done('error', t('modelPicker.toast.ollamaConnectFail'));
      }
    } catch {
      setOllamaModels([]);
      job.done('error', t('modelPicker.toast.connFail'));
    } finally {
      setOllamaFetching(false);
    }
  };

  const testConnection = async () => {
    setConnTest('testing');
    const endpoint = (localSettings.endpoint || '').trim();
    const job = toast.progress({
      label: t('modelPicker.toast.connTestLabel'),
      sublabel: endpoint || t('modelPicker.toast.connTestEndpoint'),
    });
    await new Promise<void>((resolve) => window.setTimeout(resolve, 200));
    const reachable = /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?\/?$/.test(endpoint);
    setConnTest(reachable ? 'ok' : 'fail');
    job.done(
      reachable ? 'success' : 'error',
      reachable ? t('modelPicker.toast.connOk') : t('modelPicker.toast.connFail'),
    );
  };

  // 모델 등록: 테스트를 통과한 (provider, modelId, apiKey)를 목록에 반영하고 선택
  const handleRegister = (payload: RegisterPayload) => {
    setLocalSettings((s) => ({
      ...s,
      registeredModels: [
        ...s.registeredModels.filter((r) => r.modelId !== payload.modelId),
        {
          modelId: payload.modelId,
          apiKey: payload.apiKey,
          provider: payload.provider,
          providerLabel: payload.providerLabel,
          ...(payload.baseUrl ? { baseUrl: payload.baseUrl } : {}),
          ...(payload.projectId ? { projectId: payload.projectId } : {}),
        },
      ],
    }));
    setDraftModel(payload.modelId);
    setRegisterModalOpen(false);
  };

  const removeRegisteredModel = (modelId: string) => {
    setLocalSettings((s) => ({
      ...s,
      registeredModels: s.registeredModels.filter((r) => r.modelId !== modelId),
    }));
    if (draftModel === modelId) setDraftModel('');
  };

  const handleModeChange = (mode: 'saas' | 'local') => {
    if (mode === 'local') {
      setOllamaModels(null);
      setConnTest('idle');
    }
    setDraftMode(mode);
  };

  return (
    <div className="rice-model-picker space-y-4">
      {/* ── 실행 위치 ── */}
      <div>
        <div className="text-label-01 text-text-secondary mb-2">{t('modelPicker.runLocation')}</div>
        <div className="relative grid grid-cols-2 p-0.5 bg-field-01 border border-border-subtle-01 rounded-lg">
          {/* sliding indicator — 채운 파랑 대신 neutral 선택 표면 + 1px blue 테두리 */}
          <span
            aria-hidden
            className={`absolute left-0.5 top-0.5 bottom-0.5 w-[calc(50%-2px)] rounded-md bg-layer-selected-01 border border-interactive transition-transform duration-200 ease-out ${
              draftMode === 'local' ? 'translate-x-full' : ''
            }`}
          />
          {([
            { mode: 'saas' as const, title: 'SaaS', caption: t('modelPicker.saasCaption') },
            { mode: 'local' as const, title: 'Local', caption: t('modelPicker.localCaption') },
          ]).map(({ mode, title, caption }) => {
            const isActive = draftMode === mode;
            return (
              <button
                key={mode}
                onClick={() => handleModeChange(mode)}
                aria-pressed={isActive}
                className={`relative z-[1] flex items-baseline gap-1.5 px-3 py-1 rounded-md transition-colors duration-200 ${
                  isActive
                    ? 'text-text-primary'
                    : 'text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary'
                }`}
              >
                <span className="text-body-short-01 font-semibold">{title}</span>
                <span className={`text-caption-01 truncate transition-colors duration-200 ${isActive ? 'text-text-secondary' : 'text-text-helper'}`}>
                  {caption}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* ════════ SaaS ════════ */}
      {draftMode === 'saas' && (
        <>
          <div>
            <div className="flex items-center justify-between mb-2">
              <div className="text-label-01 text-text-secondary">{t('modelPicker.sectionModel')}</div>
              <Button
                kind="ghost"
                size="xs"
                onClick={() => setRegisterModalOpen(true)}
                icon={<Icon name="add" size={12} />}
              >
                {t('modelPicker.addModel')}
              </Button>
            </div>

            {saasModels.length === 0 ? (
              <EmptyState
                icon={<Icon name="lock" size={22} className="text-icon-secondary" />}
                title={t('modelPicker.emptyTitle')}
                desc={t('modelPicker.emptyDesc')}
                action={
                  <Button
                    kind="primary"
                    size="sm"
                    icon={<Icon name="add" size={14} />}
                    onClick={() => setRegisterModalOpen(true)}
                  >
                    {t('modelPicker.addModel')}
                  </Button>
                }
              />
            ) : (
              <div className="flex flex-col gap-1.5">
                {saasModels.map((r) => {
                  const isSelected = r.modelId === draftModel;
                  return (
                    <button
                      key={r.modelId}
                      onClick={() => setDraftModel(r.modelId)}
                      aria-pressed={isSelected}
                      className={`group flex items-center gap-2.5 px-3 py-2 text-left rounded-lg border transition-colors duration-150 w-full ${
                        isSelected
                          ? 'border-interactive bg-layer-selected-01'
                          : 'border-border-subtle-01 bg-background hover:bg-layer-hover-01 hover:border-border-strong-01'
                      }`}
                    >
                      <RadioDot selected={isSelected} />
                      <ProviderAvatar provider={r.provider} label={r.providerLabel} />
                      <div className="flex-1 min-w-0">
                        <div className="text-body-short-01 font-mono text-text-primary truncate">{r.modelId}</div>
                        <div className="text-caption-01 text-text-helper truncate">
                          {`${r.providerLabel ?? 'API'} · ${maskKey(r.apiKey)}`}
                        </div>
                      </div>
                      <span
                        role="button"
                        tabIndex={0}
                        onClick={(e) => { e.stopPropagation(); removeRegisteredModel(r.modelId); }}
                        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.stopPropagation(); removeRegisteredModel(r.modelId); } }}
                        className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100 p-1 rounded-md text-icon-secondary hover:text-support-error hover:bg-support-error/10 transition-colors duration-150 cursor-pointer flex-shrink-0"
                        title={t('modelPicker.unregister')}
                      >
                        <Icon name="trash" size={14} />
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {saasModels.length > 0 && <Params localSettings={localSettings} setLocalSettings={setLocalSettings} />}
        </>
      )}

      {/* ════════ Local ════════ */}
      {draftMode === 'local' && (
        <>
          <div>
            <label className="text-label-01 text-text-secondary mb-1.5 block">{t('modelPicker.ollamaEndpointLabel')}</label>
            <div className="flex gap-2">
              <Input
                type="text"
                size="sm"
                className="flex-1"
                value={localSettings.endpoint || 'http://localhost:11434'}
                onChange={(e) => {
                  setLocalSettings({ ...localSettings, endpoint: e.target.value });
                  setConnTest('idle');
                  setOllamaModels(null);
                }}
                placeholder="http://localhost:11434"
              />
              <Button
                kind="secondary"
                size="md"
                onClick={testConnection}
                loading={connTest === 'testing'}
                className="flex-shrink-0 !bg-layer-02 hover:!bg-layer-hover-02"
              >
                {t('modelPicker.btnTestConn')}
              </Button>
            </div>
            {connTest !== 'idle' && connTest !== 'testing' && (
              <div className={`flex items-center gap-1.5 mt-1.5 text-caption-01 ${connTest === 'ok' ? 'text-support-success' : 'text-support-error'}`}>
                {connTest === 'ok' ? (
                  <>
                    <Icon name="check" size={13} className="flex-shrink-0" />
                    {t('modelPicker.connOk')}
                  </>
                ) : (
                  <>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" className="flex-shrink-0">
                      <circle cx="12" cy="12" r="9" />
                      <path d="M12 8v5M12 16.5h.01" />
                    </svg>
                    {t('modelPicker.connFail')}
                  </>
                )}
              </div>
            )}
          </div>

          {ollamaModels === null ? (
            <EmptyState
              icon={<Icon name="server" size={22} className="text-icon-secondary" />}
              title={t('modelPicker.localPreTitle')}
              desc={t('modelPicker.localPreDesc')}
              action={
                <Button
                  kind="primary"
                  size="sm"
                  icon={<Icon name="search" size={14} />}
                  onClick={fetchOllamaModels}
                  loading={ollamaFetching}
                >
                  {t('modelPicker.btnFetchModels')}
                </Button>
              }
            />
          ) : ollamaModels.length === 0 ? (
            <EmptyState
              icon={<Icon name="download" size={22} className="text-icon-secondary" />}
              title={t('modelPicker.localEmptyTitle')}
              desc={
                <Trans
                  i18nKey="modelPicker.localEmptyDesc_pre"
                  ns="settings"
                  components={{ code: <code className="font-mono text-text-secondary" /> }}
                />
              }
              action={
                <Button kind="secondary" size="sm" onClick={fetchOllamaModels} loading={ollamaFetching}>
                  {t('modelPicker.btnRefetch')}
                </Button>
              }
            />
          ) : (
            <>
              <div>
                <div className="flex items-center justify-between mb-2">
                  <div className="text-label-01 text-text-secondary">{t('modelPicker.installedModels', { count: ollamaModels.length })}</div>
                  <Button
                    kind="ghost"
                    size="xs"
                    onClick={fetchOllamaModels}
                    disabled={ollamaFetching}
                    title={t('modelPicker.titleRefresh')}
                    icon={<Icon name="renew" size={12} className={ollamaFetching ? 'animate-spin' : ''} />}
                  >
                    {t('modelPicker.btnRefresh')}
                  </Button>
                </div>
                <div className="flex flex-col gap-1.5">
                  {ollamaModels.map((m) => {
                    const isSelected = m.name === draftModel;
                    const ctxK = m.context_length ? `${Math.round(m.context_length / 1024)}K` : null;
                    return (
                      <button
                        key={m.name}
                        onClick={() => setDraftModel(m.name)}
                        aria-pressed={isSelected}
                        className={`flex items-center gap-2.5 px-3 py-2 text-left rounded-lg border transition-colors duration-150 w-full ${
                          isSelected
                            ? 'border-interactive bg-layer-selected-01'
                            : 'border-border-subtle-01 bg-background hover:bg-layer-hover-01 hover:border-border-strong-01'
                        }`}
                      >
                        <RadioDot selected={isSelected} />
                        <div className="flex-1 min-w-0">
                          <div className={`text-body-short-01 font-mono text-text-primary ${isSelected ? 'font-semibold' : ''}`}>
                            {m.name}
                          </div>
                          <div className="text-caption-01 text-text-helper truncate">
                            {[
                              m.parameter_size && m.parameter_size,
                              m.quantization_level && m.quantization_level,
                              ctxK && `ctx ${ctxK}`,
                              fmtBytes(m.size_bytes),
                            ].filter(Boolean).join(' · ')}
                          </div>
                        </div>
                        <div className="flex flex-wrap gap-1 flex-shrink-0 max-w-[80px] justify-end">
                          {m.capabilities.includes('thinking') && <Tag type="teal" size="sm">thinking</Tag>}
                          {m.capabilities.includes('tools') && <Tag type="blue" size="sm">tools</Tag>}
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>

              <Params localSettings={localSettings} setLocalSettings={setLocalSettings} local />
            </>
          )}
        </>
      )}

      <ModelRegisterModal
        open={registerModalOpen}
        onClose={() => setRegisterModalOpen(false)}
        onRegister={handleRegister}
      />

      <style>{`
        /* rice-slider — 커스텀 thumb (트랙 fill은 인라인 gradient) */
        .rice-slider::-webkit-slider-thumb {
          -webkit-appearance: none;
          appearance: none;
          width: 14px;
          height: 14px;
          border-radius: 9999px;
          background: var(--cds-text-primary);
          border: 2px solid var(--cds-interactive);
          transition: background-color 150ms ease, border-color 150ms ease;
        }
        .rice-slider::-webkit-slider-thumb:hover {
          border-color: var(--cds-link-primary);
        }
        .rice-slider:active::-webkit-slider-thumb {
          border-color: var(--cds-border-interactive);
        }
        .rice-slider::-moz-range-thumb {
          width: 14px;
          height: 14px;
          border-radius: 9999px;
          background: var(--cds-text-primary);
          border: 2px solid var(--cds-interactive);
        }
      `}</style>
    </div>
  );
};

/* ── 프로바이더 레터 아바타 (외부 브랜드 컬러라 토큰 대상 아님) ── */
const PROVIDER_AVATARS: Record<string, { letter: string; color: string }> = {
  anthropic: { letter: 'A', color: '#d97757' },
  openai: { letter: 'O', color: '#10a37f' },
  watsonx: { letter: 'W', color: '#4589ff' },
  custom: { letter: 'C', color: '#a8a8a8' },
};

const ProviderAvatar: React.FC<{ provider?: string; label?: string }> = ({ provider, label }) => {
  const spec =
    PROVIDER_AVATARS[provider ?? ''] ??
    { letter: (label ?? provider ?? '?').charAt(0).toUpperCase(), color: '#a8a8a8' };
  return (
    <span
      className="flex-shrink-0 w-6 h-6 rounded-md flex items-center justify-center text-label-micro font-mono font-semibold"
      style={{
        color: spec.color,
        background: `color-mix(in srgb, ${spec.color} 14%, transparent)`,
        border: `1px solid color-mix(in srgb, ${spec.color} 32%, transparent)`,
      }}
    >
      {spec.letter}
    </span>
  );
};

/* ── 선택 라디오 점 ── */
const RadioDot: React.FC<{ selected: boolean }> = ({ selected }) => (
  <span
    className={`flex-shrink-0 w-3.5 h-3.5 rounded-full border-2 transition-colors flex items-center justify-center ${
      selected ? 'border-interactive' : 'border-border-strong-01'
    }`}
  >
    {selected && <span className="w-1.5 h-1.5 rounded-full bg-interactive" />}
  </span>
);

/* ── 컴팩트 빈 상태 (확인 전 / 없음) — icon은 완성된 엘리먼트(<Icon> 등)를 받는다 ── */
const EmptyState: React.FC<{
  icon: React.ReactNode;
  title: string;
  desc: React.ReactNode;
  action: React.ReactNode;
}> = ({ icon, title, desc, action }) => (
  <div className="flex flex-col items-center gap-2.5 px-4 py-4 rounded-lg border border-dashed border-border-subtle-01 bg-layer-02 text-center">
    {icon}
    <div>
      <div className="text-body-short-01 font-medium text-text-primary">{title}</div>
      <div className="text-caption-01 text-text-helper mt-0.5">{desc}</div>
    </div>
    {action}
  </div>
);

/* ── Local 응답 설정 (temperature + Show Thinking) ── */
const Params: React.FC<{
  localSettings: ModelSettings;
  setLocalSettings: React.Dispatch<React.SetStateAction<ModelSettings>>;
  /** Local 모드에서만 Show Thinking 토글을 노출한다(reasoning trace는 로컬 전용). */
  local?: boolean;
}> = ({ localSettings, setLocalSettings, local = false }) => {
  const { t } = useTranslation('settings');
  return (
    <div>
      <div className="text-label-01 text-text-secondary mb-2">{t('modelPicker.params.title')}</div>
      <div className="space-y-4">
        {/* 창의성 */}
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label className="text-label-01 text-text-secondary">{t('modelPicker.params.temperatureLabel')}</label>
            <span className="text-caption-01 font-mono text-text-primary bg-field-01 border border-border-subtle-01 rounded-md px-1.5 py-0.5">
              {localSettings.temperature.toFixed(1)}
            </span>
          </div>
          <input
            type="range"
            min="0"
            max="2"
            step="0.1"
            value={localSettings.temperature}
            onChange={(e) => setLocalSettings({ ...localSettings, temperature: parseFloat(e.target.value) })}
            className="rice-slider w-full h-1.5 rounded-full appearance-none cursor-pointer"
            style={{
              background: `linear-gradient(to right, var(--cds-interactive) ${(localSettings.temperature / 2) * 100}%, var(--cds-layer-03) ${(localSettings.temperature / 2) * 100}%)`,
            }}
          />
          <div className="flex justify-between text-caption-01 text-text-helper mt-1">
            <span>{t('modelPicker.params.temperatureLow')}</span>
            <span>{t('modelPicker.params.temperatureHigh')}</span>
          </div>
        </div>

        {/* Local 모델의 reasoning trace는 비용과 무관하지만 사용자가 화면 노출을 선택한다. */}
        {local && (
          <div className="border-t border-border-subtle-01 pt-3">
            <Toggle
              checked={localSettings.showThinking}
              onChange={(showThinking) => setLocalSettings({ ...localSettings, showThinking })}
              label={t('modelPicker.params.showThinkingLabel')}
              description={t('modelPicker.params.showThinkingDesc')}
            />
          </div>
        )}
      </div>
    </div>
  );
};
