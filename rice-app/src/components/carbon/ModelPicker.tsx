import React, { useEffect, useRef, useState } from 'react';
import { Button, Input, Tag } from '../ui';
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
  maxTokens: number;
  contextLength: string;
  /** N개의 (모델, API 키) 쌍 */
  registeredModels: RegisteredModel[];
  endpoint?: string;
  userModelingEnabled: boolean;
  localStorageOnly: boolean;
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
    const job = toast.progress({ label: '로컬 모델 확인 중', sublabel: localSettings.endpoint || 'http://localhost:11434' });
    try {
      const res = await api.getOllamaModels();
      if (res.success && Array.isArray(res.data)) {
        const list = res.data as OllamaModel[];
        setOllamaModels(list);
        job.done('success', `모델 ${list.length}개 발견`);
        if (list.length > 0 && !list.some((m) => m.name === draftModel)) {
          setDraftModel(list[0].name);
        }
      } else {
        setOllamaModels([]);
        job.done('error', res.error ?? 'Ollama에 연결할 수 없어요');
      }
    } catch {
      setOllamaModels([]);
      job.done('error', '연결 실패');
    } finally {
      setOllamaFetching(false);
    }
  };

  const testConnection = async () => {
    setConnTest('testing');
    const endpoint = (localSettings.endpoint || '').trim();
    const job = toast.progress({ label: '연결 확인 중', sublabel: endpoint || '엔드포인트' });
    await new Promise<void>((resolve) => window.setTimeout(resolve, 200));
    const reachable = /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?\/?$/.test(endpoint);
    setConnTest(reachable ? 'ok' : 'fail');
    job.done(reachable ? 'success' : 'error', reachable ? '연결 성공' : '연결 실패');
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
    <div className="space-y-5">
      {/* ── 실행 위치 ── */}
      <div>
        <div className="text-label-01 text-text-secondary mb-2">실행 위치</div>
        <div className="relative grid grid-cols-2 p-0.5 bg-field-01 border border-border-subtle-01 rounded-lg">
          {/* sliding pill — 활성 모드 아래에서 미끄러지는 인디케이터 */}
          <span
            aria-hidden
            className={`absolute left-0.5 top-0.5 bottom-0.5 w-[calc(50%-2px)] rounded-md bg-interactive shadow-elevation-1 transition-transform duration-200 ease-out ${
              draftMode === 'local' ? 'translate-x-full' : ''
            }`}
          />
          {([
            { mode: 'saas' as const, title: 'SaaS', caption: '클라우드 API' },
            { mode: 'local' as const, title: 'Local', caption: 'Ollama · 온디바이스' },
          ]).map(({ mode, title, caption }) => {
            const isActive = draftMode === mode;
            return (
              <button
                key={mode}
                onClick={() => handleModeChange(mode)}
                aria-pressed={isActive}
                className={`relative z-[1] flex items-baseline gap-1.5 px-3 py-1.5 rounded-md transition-colors duration-200 ${
                  isActive
                    ? 'text-white'
                    : 'text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary'
                }`}
              >
                <span className="text-body-short-01 font-semibold">{title}</span>
                <span className={`text-caption-01 truncate transition-colors duration-200 ${isActive ? 'text-white/80' : 'text-text-helper'}`}>
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
              <div className="text-label-01 text-text-secondary">모델</div>
              <button
                onClick={() => setRegisterModalOpen(true)}
                className="flex items-center gap-1 text-caption-01 text-interactive hover:underline"
              >
                <svg width="12" height="12" viewBox="0 0 32 32" fill="currentColor">
                  <path d="M17 15V8h-2v7H8v2h7v7h2v-7h7v-2z" />
                </svg>
                모델 추가
              </button>
            </div>

            {saasModels.length === 0 ? (
              <EmptyState
                icon={
                  <path d="M16 4a5 5 0 015 5v3h1a3 3 0 013 3v9a3 3 0 01-3 3H10a3 3 0 01-3-3v-9a3 3 0 013-3h1V9a5 5 0 015-5zm0 2a3 3 0 00-3 3v3h6V9a3 3 0 00-3-3z" />
                }
                title="등록된 모델 없음"
                desc="모델 추가로 API 모델과 키를 등록하세요"
                action={
                  <Button kind="primary" size="sm" onClick={() => setRegisterModalOpen(true)}>
                    <svg width="14" height="14" viewBox="0 0 32 32" fill="currentColor" className="mr-1">
                      <path d="M17 15V8h-2v7H8v2h7v7h2v-7h7v-2z" />
                    </svg>
                    모델 추가
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
                      className={`group flex items-center gap-2.5 px-3 py-2.5 text-left rounded-lg border transition-all duration-150 w-full ${
                        isSelected
                          ? 'border-interactive bg-interactive/10 shadow-glow-interactive'
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
                        className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100 p-1 rounded-md text-icon-secondary hover:text-support-error hover:bg-support-error/10 transition-all cursor-pointer flex-shrink-0"
                        title="등록 해제"
                      >
                        <svg width="14" height="14" viewBox="0 0 32 32" fill="currentColor">
                          <path d="M12 12h2v12h-2zM18 12h2v12h-2z" />
                          <path d="M4 6v2h2v20a2 2 0 002 2h16a2 2 0 002-2V8h2V6zm4 22V8h16v20zM12 2h8v2h-8z" />
                        </svg>
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
            <label className="text-label-01 text-text-secondary mb-1.5 block">Ollama 엔드포인트</label>
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
                size="sm"
                onClick={testConnection}
                loading={connTest === 'testing'}
                className="flex-shrink-0 !bg-layer-02 hover:!bg-layer-hover-02"
              >
                연결 테스트
              </Button>
            </div>
            {connTest !== 'idle' && connTest !== 'testing' && (
              <div className={`flex items-center gap-1.5 mt-1.5 text-caption-01 ${connTest === 'ok' ? 'text-support-success' : 'text-support-error'}`}>
                {connTest === 'ok' ? (
                  <>
                    <svg width="13" height="13" viewBox="0 0 32 32" fill="currentColor" className="flex-shrink-0">
                      <path d="M13 24l-9-9 1.414-1.414L13 21.171 26.586 7.586 28 9 13 24z" />
                    </svg>
                    연결됨 · Ollama 응답 정상
                  </>
                ) : (
                  <>
                    <svg width="13" height="13" viewBox="0 0 32 32" fill="currentColor" className="flex-shrink-0">
                      <path d="M16 2a14 14 0 1014 14A14 14 0 0016 2zm-1.1 6h2.2v11h-2.2zm1.1 17.5a1.6 1.6 0 111.6-1.6 1.6 1.6 0 01-1.6 1.6z" />
                    </svg>
                    연결 실패 · 엔드포인트를 확인하세요
                  </>
                )}
              </div>
            )}
          </div>

          {ollamaModels === null ? (
            <EmptyState
              icon={<path d="M16 2a14 14 0 1014 14A14 14 0 0016 2zm-1 6h2v8h-2zm1 12.5a1.5 1.5 0 111.5-1.5A1.5 1.5 0 0116 20.5z" />}
              title="설치된 모델 확인 전"
              desc="로컬 Ollama에서 모델 목록을 불러옵니다"
              action={
                <Button kind="primary" size="sm" onClick={fetchOllamaModels} loading={ollamaFetching}>
                  <svg width="14" height="14" viewBox="0 0 32 32" fill="currentColor" className="mr-1">
                    <path d="M22 16a6 6 0 11-6-6 6 6 0 016 6zM4 16A12 12 0 1016 4 12 12 0 004 16zm2 0a10 10 0 1010-10A10 10 0 006 16z" opacity=".3" />
                    <path d="M14 10h2v12h-2zM10 14h12v2H10z" />
                  </svg>
                  로컬 모델 확인
                </Button>
              }
            />
          ) : ollamaModels.length === 0 ? (
            <EmptyState
              icon={<path d="M26 14h-2V8a2 2 0 00-2-2H10L8 4H4a2 2 0 00-2 2v18a2 2 0 002 2h20.142a2 2 0 001.992-1.819L28 14zM4 6h3.172l2 2H22v4H6a2 2 0 00-1.99 1.819L4 18.06z" />}
              title="설치된 모델 없음"
              desc={
                <>
                  <code className="font-mono text-text-secondary">ollama pull &lt;모델명&gt;</code> 으로 설치 후 다시 확인하세요
                </>
              }
              action={
                <Button kind="secondary" size="sm" onClick={fetchOllamaModels} loading={ollamaFetching}>
                  다시 확인
                </Button>
              }
            />
          ) : (
            <>
              <div>
                <div className="flex items-center justify-between mb-2">
                  <div className="text-label-01 text-text-secondary">설치된 모델 ({ollamaModels.length})</div>
                  <button
                    onClick={fetchOllamaModels}
                    disabled={ollamaFetching}
                    className="flex items-center gap-1 text-caption-01 text-interactive hover:underline disabled:opacity-40"
                    title="새로고침"
                  >
                    <svg width="12" height="12" viewBox="0 0 32 32" fill="currentColor" className={ollamaFetching ? 'animate-spin' : ''}>
                      <path d="M27.1 14.3A11 11 0 1016 27v-2a9 9 0 109-9.7z" />
                      <path d="M27 4v8h-8l3.4-3.4A9 9 0 107 24.6l-1.4 1.4A11 11 0 1027.1 14.3z" opacity=".4" />
                    </svg>
                    새로고침
                  </button>
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
                        className={`flex items-center gap-2.5 px-3 py-2.5 text-left rounded-lg border transition-all duration-150 w-full ${
                          isSelected
                            ? 'border-interactive bg-interactive/10 shadow-glow-interactive'
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

              <Params localSettings={localSettings} setLocalSettings={setLocalSettings} contextLength />
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
        @keyframes spin { to { transform: rotate(360deg); } }
        .animate-spin { animation: spin 1s linear infinite; }

        /* rice-slider — 커스텀 thumb (트랙 fill은 인라인 gradient) */
        .rice-slider::-webkit-slider-thumb {
          -webkit-appearance: none;
          appearance: none;
          width: 14px;
          height: 14px;
          border-radius: 9999px;
          background: #fff;
          border: 2px solid var(--cds-interactive);
          box-shadow: 0 1px 4px rgba(0, 0, 0, 0.4);
          transition: transform 120ms ease, box-shadow 120ms ease;
        }
        .rice-slider::-webkit-slider-thumb:hover {
          transform: scale(1.2);
          box-shadow: 0 0 0 4px rgb(var(--cds-interactive-rgb) / 0.25);
        }
        .rice-slider:active::-webkit-slider-thumb {
          transform: scale(1.1);
          box-shadow: 0 0 0 6px rgb(var(--cds-interactive-rgb) / 0.2);
        }
        .rice-slider::-moz-range-thumb {
          width: 14px;
          height: 14px;
          border-radius: 9999px;
          background: #fff;
          border: 2px solid var(--cds-interactive);
          box-shadow: 0 1px 4px rgba(0, 0, 0, 0.4);
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
      className="flex-shrink-0 w-6 h-6 rounded-md flex items-center justify-center text-[11px] font-mono font-semibold"
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

/* ── 컴팩트 빈 상태 (확인 전 / 없음) ── */
const EmptyState: React.FC<{
  icon: React.ReactNode;
  title: string;
  desc: React.ReactNode;
  action: React.ReactNode;
}> = ({ icon, title, desc, action }) => (
  <div className="flex flex-col items-center gap-2.5 px-4 py-5 rounded-lg border border-dashed border-border-subtle-01 bg-layer-02 text-center">
    <svg width="22" height="22" viewBox="0 0 32 32" fill="currentColor" className="text-icon-secondary">
      {icon}
    </svg>
    <div>
      <div className="text-body-short-01 font-medium text-text-primary">{title}</div>
      <div className="text-caption-01 text-text-helper mt-0.5">{desc}</div>
    </div>
    {action}
  </div>
);

/* ── 파라미터 (temperature, max tokens, [context length]) ── */
const Params: React.FC<{
  localSettings: ModelSettings;
  setLocalSettings: React.Dispatch<React.SetStateAction<ModelSettings>>;
  contextLength?: boolean;
}> = ({ localSettings, setLocalSettings, contextLength = false }) => (
  <div>
    <div className="text-label-01 text-text-secondary mb-2">파라미터</div>
    <div className="space-y-4">
      {/* 창의성 */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-label-01 text-text-secondary">창의성 (Temperature)</label>
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
          <span>정확하게</span>
          <span>창의적으로</span>
        </div>
      </div>

      {/* 최대 토큰 */}
      <div>
        <label className="text-label-01 text-text-secondary mb-1.5 block">최대 토큰</label>
        <Input
          type="number"
          size="sm"
          value={localSettings.maxTokens}
          onChange={(e) => setLocalSettings({ ...localSettings, maxTokens: parseInt(e.target.value) || 0 })}
          min={1}
          max={128000}
        />
      </div>

      {/* 컨텍스트 길이 (Local 전용) */}
      {contextLength && (
        <div>
          <label className="text-label-01 text-text-secondary mb-1.5 block">컨텍스트 길이</label>
          <div className="relative">
            <select
              value={localSettings.contextLength}
              onChange={(e) => setLocalSettings({ ...localSettings, contextLength: e.target.value })}
              className="w-full h-8 bg-field-01 border border-border-strong-01 rounded-lg pl-3 pr-9 text-body-short-01 text-text-primary outline-none transition-colors focus:border-focus focus:ring-1 focus:ring-focus appearance-none cursor-pointer"
            >
              <option value="8K">8K 토큰</option>
              <option value="32K">32K 토큰</option>
              <option value="128K">128K 토큰</option>
              <option value="256K">256K 토큰</option>
            </select>
            <svg
              width="12"
              height="12"
              viewBox="0 0 32 32"
              fill="currentColor"
              className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-icon-secondary"
            >
              <path d="M16 22L6 12l1.4-1.4 8.6 8.6 8.6-8.6L26 12z" />
            </svg>
          </div>
        </div>
      )}
    </div>
  </div>
);

// Made with Bob
