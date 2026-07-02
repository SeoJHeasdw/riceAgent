import React, { useEffect, useState } from 'react';
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
  'w-full h-10 px-3.5 bg-field-01 text-text-primary font-mono text-body-short-01 border border-border-strong-01 rounded-lg outline-none transition-colors focus:border-focus focus:ring-1 focus:ring-focus placeholder:text-text-placeholder placeholder:font-sans';

export const CustomConnectorModal: React.FC<CustomConnectorModalProps> = ({ open, onClose, onDiscover, onAdd }) => {
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

  const footer = (
    <div className="flex items-center justify-between w-full">
      <Button kind="ghost" size="xs" onClick={onClose} disabled={adding}>
        취소
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
          {discoverState === 'checking' ? '검사 중…' : discoverState === 'done' ? '다시 검사' : 'OAuth 설정 검사'}
        </Button>
        <div className="relative group">
          <Button kind="primary" size="xs" onClick={handleAdd} loading={adding} disabled={!canAdd}>
            <Icon name="connect" size={12} className="mr-1" />
            추가하고 연결
          </Button>
          {!canAdd && !adding && (
            <div className="pointer-events-none absolute bottom-full right-0 mb-2 w-max max-w-[240px] px-2.5 py-1.5 rounded-md bg-text-primary text-text-inverse text-caption-01 shadow-overlay opacity-0 translate-y-1 group-hover:opacity-100 group-hover:translate-y-0 transition-all duration-150">
              이름과 authorize/token 엔드포인트가 필요해요 — 검사로 자동 채우거나 직접
              입력하세요
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
      title="커스텀 커넥터 추가"
      size="md"
      footer={footer}
      compactFooter
    >
      <div className="space-y-5">
        <p className="text-caption-01 text-text-helper leading-relaxed">
          OAuth 로 인증하는 원격 MCP 서버 URL 을 입력하면, authorize/token 엔드포인트와 client
          등록 가능 여부를 자동으로 찾아봐요. 표준을 따르지 않는 서버는 아래에서 직접 입력하면
          돼요.
        </p>

        <div>
          <label className="text-label-01 text-text-secondary mb-1.5 block">표시 이름</label>
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="예: Notion, Linear, Sentry"
            className={inputCls.replace('font-mono ', '')}
          />
        </div>

        <div>
          <label className="text-label-01 text-text-secondary mb-1.5 block">MCP 서버 URL</label>
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
                : 'bg-[#f1c21b]/10 border-[#f1c21b]/30'
            }`}
          >
            <Icon
              name={discovered?.ok ? 'check-circle' : 'warning'}
              size={15}
              filled
              className={`flex-shrink-0 mt-0.5 ${discovered?.ok ? 'text-support-success' : 'text-[#d2a106]'}`}
            />
            <p className="text-caption-01 text-text-helper leading-relaxed">
              {discovered?.ok
                ? '이 서버의 OAuth 설정을 자동으로 찾았어요. 그대로 추가하거나 아래에서 고칠 수 있어요.'
                : '자동으로 찾지 못했어요 — authorize/token 엔드포인트를 직접 입력해주세요.'}
              {discovered?.registration_endpoint && (
                <span className="block mt-0.5">동적 client 등록을 지원해요 — client ID/secret 을 직접 입력하지 않아도 돼요.</span>
              )}
            </p>
          </div>
        )}

        {discoverState === 'done' && (
          <>
            <div className="grid grid-cols-1 gap-4">
              <div>
                <label className="text-label-01 text-text-secondary mb-1.5 block">Authorize 엔드포인트</label>
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
                <label className="text-label-01 text-text-secondary mb-1.5 block">Token 엔드포인트</label>
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
              고급 설정 · scope · client ID/secret 직접 입력
            </button>

            {advancedOpen && (
              <div className="space-y-4 pl-3 border-l-2 border-border-subtle-01">
                <div>
                  <label className="text-label-01 text-text-secondary mb-1.5 block">
                    Scope <span className="text-text-placeholder font-normal">· 공백으로 구분 · 선택</span>
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
                    Dynamic registration 엔드포인트 <span className="text-text-placeholder font-normal">· 선택</span>
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
                    Client ID <span className="text-text-placeholder font-normal">· 직접 등록한 client 가 있을 때만</span>
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
                  <label className="text-label-01 text-text-secondary mb-1.5 block">Client secret</label>
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
            Client secret 은 <span className="text-text-secondary font-medium">OS 키체인에만</span> 저장돼요. "추가하고
            연결"을 누르면 시스템 브라우저에서 로그인 화면이 열려요.
          </p>
        </div>
      </div>
    </Modal>
  );
};

// Made with Bob
