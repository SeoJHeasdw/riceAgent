import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Icon, Modal } from '../ui';

/**
 * CustomConnectorModal — "커넥터 추가" → "커스텀 추가".
 *
 * 임의의 원격 MCP 서버 URL 하나로 OAuth 설정(authorize/token endpoint, scope,
 * client 자동 등록 가능 여부)을 서버가 자동으로 알아낸다(RFC 9728 + RFC 8414).
 * 찾은 값은 그대로 쓰거나 고급 설정에서 직접 고칠 수 있고, 못 찾은 값은 직접
 * 채워야 "추가"가 활성화된다. 실제 client 등록(RFC 7591)과 OAuth 연결은
 * "추가"를 눌러 확정한 뒤에 일어난다(검사 단계는 부작용 없음).
 *
 * 이 컴포넌트는 API 를 직접 부르지 않는다 — ConnectorsScreen 이 onDiscover/onAdd 로
 * 백엔드 호출을 주입한다(ConnectorDirectoryModal·McpRegisterModal 과 같은 관례).
 */

export interface DiscoveredOAuthConfig {
  ok: boolean;
  auth_url: string | null;
  token_url: string | null;
  registration_endpoint: string | null;
  scopes: string[];
  issuer: string | null;
}

export interface CustomConnectorAddPayload {
  display_name: string;
  mcp_endpoint: string;
  auth_url: string;
  token_url: string;
  registration_endpoint?: string;
  scopes?: string[];
  client_id?: string;
  client_secret?: string;
}

export interface CustomConnectorModalProps {
  open: boolean;
  onClose: () => void;
  /** 부작용 없음(RFC 9728+8414 조회만) — null 이면 요청 자체가 실패한 것 */
  onDiscover: (mcpEndpoint: string) => Promise<DiscoveredOAuthConfig | null>;
  /**
   * 여기서부터 부작용 발생(동적 client 등록 + OAuth 시작).
   * null=성공(모달 닫힘), string=백엔드가 준 실패 사유(모달에 그대로 표시 — 예: 동적
   * client 등록 미지원이라 client ID 수동 입력 필요).
   */
  onAdd: (payload: CustomConnectorAddPayload) => Promise<string | null>;
}

type DiscoverState = 'idle' | 'checking' | 'done';

const inputCls =
  'w-full h-9 px-3 bg-field-01 text-text-primary font-mono text-body-short-01 border border-border-strong-01 rounded-lg outline-none transition-colors focus:border-focus focus:ring-1 focus:ring-focus placeholder:text-text-placeholder placeholder:font-sans';

export const CustomConnectorModal: React.FC<CustomConnectorModalProps> = ({ open, onClose, onDiscover, onAdd }) => {
  const { t } = useTranslation('connectors');
  const [mcpEndpoint, setMcpEndpoint] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [discoverState, setDiscoverState] = useState<DiscoverState>('idle');
  const [discovered, setDiscovered] = useState<DiscoveredOAuthConfig | null>(null);
  const [authUrl, setAuthUrl] = useState('');
  const [tokenUrl, setTokenUrl] = useState('');
  const [registrationEndpoint, setRegistrationEndpoint] = useState('');
  const [scopesText, setScopesText] = useState('');
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setMcpEndpoint('');
    setDisplayName('');
    setDiscoverState('idle');
    setDiscovered(null);
    setAuthUrl('');
    setTokenUrl('');
    setRegistrationEndpoint('');
    setScopesText('');
    setClientId('');
    setClientSecret('');
    setAdvancedOpen(false);
    setAdding(false);
    setAddError(null);
  }, [open]);

  const urlReady = /^https?:\/\/.+/i.test(mcpEndpoint.trim()) && displayName.trim().length > 0;
  const canDiscover = urlReady && discoverState !== 'checking';
  const canAdd = authUrl.trim().length > 0 && tokenUrl.trim().length > 0 && displayName.trim().length > 0 && !adding;

  const runDiscover = async () => {
    if (!canDiscover) return;
    setDiscoverState('checking');
    setAddError(null);
    const cfg = await onDiscover(mcpEndpoint.trim());
    setDiscovered(cfg);
    setAuthUrl(cfg?.auth_url ?? '');
    setTokenUrl(cfg?.token_url ?? '');
    setRegistrationEndpoint(cfg?.registration_endpoint ?? '');
    setScopesText((cfg?.scopes ?? []).join(' '));
    setAdvancedOpen(!cfg?.ok);
    setDiscoverState('done');
  };

  const handleAdd = async () => {
    if (!canAdd) return;
    setAdding(true);
    setAddError(null);
    try {
      const err = await onAdd({
        display_name: displayName.trim(),
        mcp_endpoint: mcpEndpoint.trim(),
        auth_url: authUrl.trim(),
        token_url: tokenUrl.trim(),
        registration_endpoint: registrationEndpoint.trim() || undefined,
        scopes: scopesText.trim() ? scopesText.trim().split(/\s+/) : undefined,
        client_id: clientId.trim() || undefined,
        client_secret: clientSecret.trim() || undefined,
      });
      if (err) {
        setAddError(err);
        // client ID 수동 입력이 필요한 실패가 대부분이라 입력란이 있는 고급 설정을 펼쳐 준다.
        setAdvancedOpen(true);
      }
    } finally {
      setAdding(false);
    }
  };

  const discoverBtnLabel =
    discoverState === 'checking'
      ? t('custom.discoverBtn_checking')
      : discoverState === 'done'
      ? t('custom.discoverBtn_done')
      : t('custom.discoverBtn_idle');

  const footer = (
    <div className="flex items-center justify-between w-full">
      <Button kind="ghost" size="xs" onClick={onClose} disabled={adding}>
        {t('custom.cancel')}
      </Button>
      <div className="flex items-center gap-2">
        <Button
          kind="tertiary"
          size="xs"
          onClick={runDiscover}
          loading={discoverState === 'checking'}
          disabled={!canDiscover}
          icon={discoverState !== 'checking' ? <Icon name="search" size={12} /> : undefined}
        >
          {discoverBtnLabel}
        </Button>
        <div className="relative group">
          <Button kind="primary" size="xs" onClick={handleAdd} loading={adding} disabled={!canAdd}>
            <Icon name="connect" size={12} className="mr-1" />
            {t('custom.addBtn')}
          </Button>
          {!canAdd && !adding && (
            <div className="pointer-events-none absolute bottom-full right-0 mb-2 w-max max-w-[240px] px-2.5 py-1.5 rounded-md bg-text-primary text-text-inverse text-caption-01 shadow-elevation-2 opacity-0 translate-y-1 group-hover:opacity-100 group-hover:translate-y-0 transition-all duration-150">
              {t('custom.addBtnTooltip')}
              <span className="absolute top-full right-4 -mt-px border-4 border-transparent border-t-text-primary" />
            </div>
          )}
        </div>
      </div>
    </div>
  );

  return (
    <Modal
      open={open}
      onClose={() => {
        if (!adding) onClose();
      }}
      title={t('custom.title')}
      size="md"
      footer={footer}
      compactFooter
    >
      <div className="space-y-4">
        <p className="text-caption-01 text-text-helper leading-relaxed">
          {t('custom.description')}
        </p>

        <div>
          <label className="text-label-01 text-text-secondary mb-1.5 block">{t('custom.labelDisplayName')}</label>
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder={t('custom.placeholderDisplayName')}
            className={inputCls.replace('font-mono ', '')}
          />
        </div>

        <div>
          <label className="text-label-01 text-text-secondary mb-1.5 block">{t('custom.labelMcpUrl')}</label>
          <input
            type="text"
            value={mcpEndpoint}
            onChange={(e) => {
              setMcpEndpoint(e.target.value);
              setDiscoverState('idle');
            }}
            placeholder="https://mcp.example.com/mcp"
            autoComplete="off"
            spellCheck={false}
            className={inputCls}
          />
        </div>

        {discoverState === 'done' && (
          <div
            className={`flex items-start gap-2.5 px-3 py-2.5 rounded-lg border ${
              discovered?.ok
                ? 'bg-support-success/10 border-support-success/30'
                : 'bg-support-warning/10 border-support-warning/30'
            }`}
          >
            <Icon
              name={discovered?.ok ? 'check-circle' : 'warning'}
              size={15}
              filled
              className={`flex-shrink-0 mt-0.5 ${discovered?.ok ? 'text-support-success' : 'text-yellow-40'}`}
            />
            <p className="text-caption-01 text-text-helper leading-relaxed">
              {discovered?.ok ? t('custom.discoverOk') : t('custom.discoverFail')}
              {discovered?.registration_endpoint && (
                <span className="block mt-0.5">{t('custom.discoverDynamicReg')}</span>
              )}
            </p>
          </div>
        )}

        {discoverState === 'done' && (
          <>
            <div className="grid grid-cols-1 gap-4">
              <div>
                <label className="text-label-01 text-text-secondary mb-1.5 block">{t('custom.labelAuthorize')}</label>
                <input
                  type="text"
                  value={authUrl}
                  onChange={(e) => setAuthUrl(e.target.value)}
                  placeholder="https://mcp.example.com/oauth/authorize"
                  autoComplete="off"
                  spellCheck={false}
                  className={inputCls}
                />
              </div>
              <div>
                <label className="text-label-01 text-text-secondary mb-1.5 block">{t('custom.labelToken')}</label>
                <input
                  type="text"
                  value={tokenUrl}
                  onChange={(e) => setTokenUrl(e.target.value)}
                  placeholder="https://mcp.example.com/oauth/token"
                  autoComplete="off"
                  spellCheck={false}
                  className={inputCls}
                />
              </div>
            </div>

            <button
              type="button"
              onClick={() => setAdvancedOpen((v) => !v)}
              className="flex items-center gap-1 text-caption-01 text-link-primary hover:underline"
            >
              <Icon name={advancedOpen ? 'chevron-up' : 'chevron-down'} size={12} />
              {t('custom.advancedToggle')}
            </button>

            {advancedOpen && (
              <div className="space-y-4 pl-3 border-l-2 border-border-subtle-01">
                <div>
                  <label className="text-label-01 text-text-secondary mb-1.5 block">
                    {t('custom.labelScope')} <span className="text-text-placeholder font-normal">{t('custom.scopeHint')}</span>
                  </label>
                  <input
                    type="text"
                    value={scopesText}
                    onChange={(e) => setScopesText(e.target.value)}
                    placeholder="read write"
                    autoComplete="off"
                    spellCheck={false}
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="text-label-01 text-text-secondary mb-1.5 block">
                    {t('custom.labelDynReg')} <span className="text-text-placeholder font-normal">{t('custom.dynRegHint')}</span>
                  </label>
                  <input
                    type="text"
                    value={registrationEndpoint}
                    onChange={(e) => setRegistrationEndpoint(e.target.value)}
                    placeholder="https://mcp.example.com/oauth/register"
                    autoComplete="off"
                    spellCheck={false}
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="text-label-01 text-text-secondary mb-1.5 block">
                    {t('custom.labelClientId')} <span className="text-text-placeholder font-normal">{t('custom.clientIdHint')}</span>
                  </label>
                  <input
                    type="text"
                    value={clientId}
                    onChange={(e) => setClientId(e.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="text-label-01 text-text-secondary mb-1.5 block">{t('custom.labelClientSecret')}</label>
                  <input
                    type="password"
                    value={clientSecret}
                    onChange={(e) => setClientSecret(e.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                    className={inputCls}
                  />
                </div>
              </div>
            )}
          </>
        )}

        {addError && (
          <div className="flex items-center gap-1.5 text-caption-01 text-support-error">
            <Icon name="warning" size={14} filled />
            {addError}
          </div>
        )}

        <div className="flex items-start gap-2.5 px-3 py-2.5 rounded-lg bg-layer-02 border border-border-subtle-01">
          <Icon name="lock" size={15} className="text-icon-secondary flex-shrink-0 mt-0.5" />
          <p className="text-caption-01 text-text-helper leading-relaxed">
            {t('custom.securityNote_pre')} <span className="text-text-secondary font-medium">{t('custom.securityNote_strong')}</span> {t('custom.securityNote_post')}
          </p>
        </div>
      </div>
    </Modal>
  );
};

// Made with Bob
