import React, { useEffect, useMemo, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { Button, Icon, Modal } from '../ui';

/**
 * ModelRegisterModal — provider-agnostic (모델, API 키) 등록 모달.
 *
 * ModelSettingsPanel에서 분리된 독립 컴포넌트. 폼 상태(provider/모델명/키)는
 * 이 컴포넌트의 로컬 상태로 캡슐화하고, 결과만 onRegister 콜백으로 올려보낸다.
 *
 * 등록 흐름:
 *   1. provider 선택 → 모델명 · API 키 입력
 *   2. 필수 입력을 채우면 즉시 등록 가능
 *   3. 실제 연결 오류는 첫 채팅 요청에서 provider 오류 코드로 안내
 */

export interface RegisterPayload {
  provider: string;
  providerLabel: string;
  modelId: string;
  apiKey: string;
  /** OpenAI 호환 커스텀 엔드포인트 / watsonx 지역 엔드포인트 */
  baseUrl?: string;
  /** watsonx project id */
  projectId?: string;
}

interface ProviderSpec {
  id: string;
  /** 브랜드명 라벨 (custom 제외 번역 대상 아님) */
  label: string;
  /** 빠른 선택용 모델명 제안 */
  suggestions: string[];
  /** Base URL 필드 노출 여부 */
  baseUrl?: 'none' | 'optional' | 'required';
  /** watsonx project id 필요 여부 */
  needsProjectId?: boolean;
}

const PROVIDERS: ProviderSpec[] = [
  {
    id: 'anthropic',
    label: 'Anthropic',
    suggestions: ['claude-opus-4-8', 'claude-sonnet-4-6', 'claude-haiku-4-5'],
    baseUrl: 'none',
  },
  {
    id: 'openai',
    label: 'OpenAI',
    suggestions: ['gpt-4o', 'gpt-4o-mini', 'o3-mini'],
    baseUrl: 'optional',
  },
  {
    id: 'watsonx',
    label: 'IBM watsonx',
    suggestions: ['ibm/granite-3-8b-instruct', 'meta-llama/llama-3-3-70b-instruct'],
    baseUrl: 'required',
    needsProjectId: true,
  },
  {
    id: 'custom',
    label: 'OpenAI-compatible (Custom)',
    suggestions: [],
    baseUrl: 'required',
  },
];

export interface ModelRegisterModalProps {
  open: boolean;
  onClose: () => void;
  /** 필수 입력을 채운 뒤 등록 확정 시 호출 */
  onRegister: (payload: RegisterPayload) => void;
  /** 특정 모델 카드의 "연결"에서 열 때, 제공자·모델명을 미리 채운다 */
  defaults?: { provider?: string; modelId?: string };
}

export const ModelRegisterModal: React.FC<ModelRegisterModalProps> = ({ open, onClose, onRegister, defaults }) => {
  const { t } = useTranslation('settings');
  const [providerId, setProviderId] = useState<string>('anthropic');
  const [modelId, setModelId] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [projectId, setProjectId] = useState('');
  const [showKey, setShowKey] = useState(false);

  const provider = useMemo(
    () => PROVIDERS.find((p) => p.id === providerId) ?? PROVIDERS[0],
    [providerId]
  );

  // 모달이 열릴 때마다 폼 초기화 (defaults가 있으면 그 값으로 시드)
  useEffect(() => {
    if (open) {
      setProviderId(defaults?.provider ?? 'anthropic');
      setModelId(defaults?.modelId ?? '');
      setApiKey('');
      setBaseUrl('');
      setProjectId('');
      setShowKey(false);
    }
    // defaults는 열림 시점의 스냅샷만 사용 — open 토글에만 반응
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const trimmedKey = apiKey.trim();
  const trimmedId = modelId.trim();
  const trimmedBaseUrl = baseUrl.trim();
  const trimmedProjectId = projectId.trim();
  const needsBaseUrl = provider.baseUrl === 'required';
  const needsProjectId = !!provider.needsProjectId;
  const fieldsReady =
    trimmedKey.length > 0 &&
    trimmedId.length > 0 &&
    (!needsBaseUrl || trimmedBaseUrl.length > 0) &&
    (!needsProjectId || trimmedProjectId.length > 0);
  const canRegister = fieldsReady;

  const handleProvider = (id: string) => {
    setProviderId(id);
    setBaseUrl('');
    setProjectId('');
  };

  const handleRegister = () => {
    if (!canRegister) return;
    onRegister({
      provider: provider.id,
      providerLabel: provider.label,
      modelId: trimmedId,
      apiKey: trimmedKey,
      ...(provider.baseUrl !== 'none' && trimmedBaseUrl ? { baseUrl: trimmedBaseUrl } : {}),
      ...(needsProjectId && trimmedProjectId ? { projectId: trimmedProjectId } : {}),
    });
  };

  // 런타임에 provider id 기반으로 카탈로그에서 조회
  // 동적 키라 타입 지정 t로는 직접 접근 어려움 — 좁은 범위에서만 widened t 사용
  const pt = t as unknown as (key: string) => string;
  const providerKeyLabel = pt(`modelRegister.providers.${provider.id}.keyLabel`);
  const providerKeyPlaceholder = pt(`modelRegister.providers.${provider.id}.keyPlaceholder`);
  const providerBaseUrlLabel = provider.baseUrl !== 'none'
    ? pt(`modelRegister.providers.${provider.id}.baseUrlLabel`)
    : '';
  const providerBaseUrlPlaceholder = provider.baseUrl !== 'none'
    ? pt(`modelRegister.providers.${provider.id}.baseUrlPlaceholder`)
    : '';

  const footer = (
    <div className="flex items-center justify-between w-full">
      <Button kind="ghost" size="sm" onClick={onClose}>
        {t('modelRegister.cancel')}
      </Button>
      <Button kind="primary" size="sm" onClick={handleRegister} disabled={!canRegister} icon={<Icon name="check" size={14} />}>
        {t('modelRegister.register')}
      </Button>
    </div>
  );

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t('modelRegister.title')}
      size="sm"
      footer={footer}
    >
      <div className="space-y-4">
        {/* Provider 선택 */}
        <div>
          <label className="text-label-01 text-text-secondary mb-2 block">
            {t('modelRegister.providerLabel')}
          </label>
          <div className="grid grid-cols-2 gap-1.5">
            {PROVIDERS.map((p) => {
              const active = p.id === providerId;
              return (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => handleProvider(p.id)}
                  aria-pressed={active}
                  className={`px-3 py-1.5 rounded-lg border text-body-short-01 text-left transition-colors duration-150 ${
                    active
                      ? 'border-interactive bg-layer-selected-01 text-text-primary font-semibold'
                      : 'border-border-subtle-01 bg-background text-text-secondary hover:bg-layer-hover-01 hover:border-border-strong-01'
                  }`}
                >
                  {p.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* 모델명 */}
        <div>
          <label className="text-label-01 text-text-secondary mb-1.5 block">
            {t('modelRegister.modelIdLabel')}
          </label>
          <input
            type="text"
            value={modelId}
            onChange={(e) => setModelId(e.target.value)}
            placeholder={provider.suggestions[0] ?? t('modelRegister.modelIdPlaceholder')}
            className="w-full h-9 px-3 bg-field-01 text-text-primary font-mono text-body-short-01 border border-border-strong-01 rounded-lg outline-none transition-colors focus:border-focus focus:ring-1 focus:ring-focus placeholder:text-text-placeholder placeholder:font-sans"
          />
          {provider.suggestions.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {provider.suggestions.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setModelId(s)}
                  className={`px-2 py-1 rounded-md font-mono text-caption-01 border transition-colors ${
                    modelId === s
                      ? 'border-interactive bg-layer-selected-01 text-text-primary'
                      : 'border-border-subtle-01 bg-layer-02 text-text-secondary hover:bg-layer-hover-02 hover:text-text-primary'
                  }`}
                >
                  {s}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* watsonx project id */}
        {needsProjectId && (
          <div>
            <label className="text-label-01 text-text-secondary mb-1.5 block">
              {t('modelRegister.projectIdLabel')}
            </label>
            <input
              type="text"
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
              placeholder={t('modelRegister.projectIdPlaceholder')}
              autoComplete="off"
              spellCheck={false}
              className="w-full h-9 px-3 bg-field-01 text-text-primary font-mono text-body-short-01 border border-border-strong-01 rounded-lg outline-none transition-colors focus:border-focus focus:ring-1 focus:ring-focus placeholder:text-text-placeholder placeholder:font-sans"
            />
          </div>
        )}

        {/* Base URL (openai 선택 / 커스텀·watsonx 필수) */}
        {provider.baseUrl !== 'none' && (
          <div>
            <label className="text-label-01 text-text-secondary mb-1.5 block">
              {providerBaseUrlLabel}
            </label>
            <input
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder={providerBaseUrlPlaceholder}
              autoComplete="off"
              spellCheck={false}
              className="w-full h-9 px-3 bg-field-01 text-text-primary font-mono text-body-short-01 border border-border-strong-01 rounded-lg outline-none transition-colors focus:border-focus focus:ring-1 focus:ring-focus placeholder:text-text-placeholder placeholder:font-sans"
            />
          </div>
        )}

        {/* API 키 — 모노스페이스 + show/hide 토글 */}
        <div>
          <label className="text-label-01 text-text-secondary mb-1.5 block">
            {providerKeyLabel}
          </label>
          <div className="relative">
            <input
              type={showKey ? 'text' : 'password'}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={providerKeyPlaceholder}
              autoComplete="off"
              spellCheck={false}
              className="w-full h-9 pl-3 pr-10 bg-field-01 text-text-primary font-mono text-body-short-01 border border-border-strong-01 rounded-lg outline-none transition-colors focus:border-focus focus:ring-1 focus:ring-focus placeholder:text-text-placeholder placeholder:font-sans"
            />
            <button
              type="button"
              onClick={() => setShowKey((v) => !v)}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 p-1.5 rounded-md text-icon-secondary hover:text-icon-primary hover:bg-layer-hover-01 transition-colors"
              title={showKey ? t('modelRegister.hideKey') : t('modelRegister.showKey')}
              aria-label={showKey ? t('modelRegister.hideKey') : t('modelRegister.showKey')}
            >
              <Icon name={showKey ? 'view-off' : 'view'} size={16} />
            </button>
          </div>
        </div>

        {/* 저장 위치 안내 */}
        <div className="flex items-start gap-2.5 px-3 py-2.5 rounded-lg bg-layer-02 border border-border-subtle-01">
          <Icon name="lock" size={15} className="text-icon-secondary flex-shrink-0 mt-0.5" />
          <p className="text-caption-01 text-text-helper leading-relaxed">
            <Trans
              i18nKey="modelRegister.storageNote"
              ns="settings"
              components={{
                device: <span className="text-text-secondary font-medium" />,
              }}
            />
            <br />
            <span className="text-text-placeholder">
              {t('modelRegister.storageNoteDb')}
            </span>
          </p>
        </div>
      </div>
    </Modal>
  );
};

// Made with Bob
