import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { apiErrorMessage } from '../lib/i18n/apiError';
import { Button, Icon, Tag, ResizeHandle } from '../components/ui';
import type { IconName } from '../components/ui/Icon';
import {
  McpRegisterModal,
  ConnectorDirectoryModal,
  CustomConnectorModal,
  type CustomConnectorAddPayload,
  type DiscoveredOAuthConfig,
} from '../components/workspace';
import { useResizable } from '../hooks/useResizable';
import api from '../lib/api';
import type { LocalMcpTransportPayload } from '../lib/api';
import { openExternal } from '../lib/openExternal';
import { RuntimeConnectionNotice } from '../components/workspace/RuntimeConnectionNotice';
import { useToast } from '../lib/toast/ToastContext';
import i18n from '../lib/i18n';
import {
  connectorKind,
  type CatalogConnector,
  type Connector,
  type ConnectorScope,
  type ConnectorUpdate,
  type McpTransport,
  type ScopePermission,
} from '../lib/learningData';

type Kind = 'remote' | 'local-mcp';

// ── 백엔드(runtime) 연동 — 런타임이 온라인일 때 'remote' 커넥터를 실데이터로 대체 ──

type WirePermission = 'allow' | 'approval' | 'block';

/** 백엔드 도구 정책 ↔ UI 3단 토글 매핑 (wire === ScopePermission이므로 단순 identity). */
const WIRE_TO_UI_PERM: Record<WirePermission, ScopePermission> = {
  allow: 'allow',
  approval: 'approval',
  block: 'block',
};
const UI_TO_WIRE_PERM: Record<ScopePermission, WirePermission> = {
  allow: 'allow',
  approval: 'approval',
  block: 'block',
};

/** 백엔드가 돌려주는 도구 카탈로그 항목(원격 MCP tools/list 로 받아 저장한 것). */
interface ConnectorToolWire {
  name: string;
  /** 원격 MCP 서버가 tools/list 로 제공한 설명 — 무슨 도구인지 사용자가 판단하는 근거. */
  description?: string;
  requires_approval?: boolean;
  permission?: WirePermission; // 저장된(또는 휴리스틱 기본) 호출 정책
}

/** 백엔드가 계산한 대기 중 도구 카탈로그 업데이트 diff. */
interface ConnectorUpdateWire {
  added: string[];
  removed: string[];
  added_tools?: ConnectorToolWire[];
}

/** GET /connectors 가 돌려주는 ConnectorInfo wire 형태 (CONTRACTS §4). */
interface ConnectorInfoWire {
  id: string;
  connector_type: string;
  display_name: string;
  status: 'connected' | 'disconnected' | 'verifying' | 'error';
  scopes?: string[];
  tools?: ConnectorToolWire[];
  update?: ConnectorUpdateWire | null;
  connected_at?: string | null;
  last_verified_at?: string | null;
  error_code?: string | null;
  error_detail?: string | null;
  setup_url?: string | null;
}

/** GET /connectors/local 이 돌려주는 로컬 MCP 서버 wire 형태 (.mcp.json 파생). */
interface LocalMcpServerWire {
  connector_id: string;
  name: string;
  transport: 'stdio' | 'http';
  command: string;
  args: string[];
  url: string;
  running: boolean;
  tools: ConnectorToolWire[];
}

// 도구 이름 접두사로 읽기/쓰기를 추정(그룹 헤더용 표시 힌트 — 실제 승인은 런타임이 강제).
const READ_PREFIXES = ['get', 'list', 'search', 'read', 'fetch', 'find', 'view'];

/** 백엔드 도구 → UI ConnectorScope. 이름으로 읽기/쓰기를 가르고(표시용), 저장된 정책을 반영. */
const toolToScope = (t: ConnectorToolWire): ConnectorScope => {
  const readOnly = READ_PREFIXES.some((p) => t.name.toLowerCase().startsWith(p));
  return {
    name: t.name,
    mode: readOnly ? 'read' : 'write',
    permission: t.permission ? WIRE_TO_UI_PERM[t.permission] : undefined,
    description: t.description || undefined,
  };
};

/** 백엔드 update diff → UI ConnectorUpdate(적용 시 addsTools 를 도구 목록에 반영). */
const wireToUpdate = (u: ConnectorUpdateWire | null | undefined): ConnectorUpdate | undefined => {
  if (!u || (u.added.length === 0 && u.removed.length === 0)) return undefined;
  return {
    addedCount: u.added.length,
    removedCount: u.removed.length,
    addsTools: (u.added_tools ?? []).map(toolToScope),
  };
};

/**
 * 원격 커넥터 타입별 표시 메타(아이콘·분류·제공사·문구) — 백엔드 registry.py 의
 * connector_type 과 1:1 대응. 새 Google-family 커넥터(예: docs)를 백엔드에 추가하면
 * 여기 항목 하나만 더하면 된다. 미등록 타입은 DEFAULT_TYPE_META 로 대충이라도 표시.
 */
const CONNECTOR_TYPE_META: Record<
  string,
  {
    displayName: string;
    icon: IconName;
    category: string;
    provider: string;
    connectedSummary: string;
    connectableSummary: string;
    examples: string[];
  }
> = {
  gmail: {
    displayName: 'Gmail',
    icon: 'send',
    get category() { return i18n.t('connectors:meta.gmail.category'); },
    provider: 'Google',
    get connectedSummary() { return i18n.t('connectors:meta.gmail.connectedSummary'); },
    get connectableSummary() { return i18n.t('connectors:meta.gmail.connectableSummary'); },
    get examples(): string[] { return i18n.t('connectors:meta.gmail.examples', { returnObjects: true }) as string[]; },
  },
  calendar: {
    displayName: 'Google Calendar',
    icon: 'calendar',
    get category() { return i18n.t('connectors:meta.calendar.category'); },
    provider: 'Google',
    get connectedSummary() { return i18n.t('connectors:meta.calendar.connectedSummary'); },
    get connectableSummary() { return i18n.t('connectors:meta.calendar.connectableSummary'); },
    get examples(): string[] { return i18n.t('connectors:meta.calendar.examples', { returnObjects: true }) as string[]; },
  },
  drive: {
    displayName: 'Google Drive',
    icon: 'cloud',
    get category() { return i18n.t('connectors:meta.drive.category'); },
    provider: 'Google',
    get connectedSummary() { return i18n.t('connectors:meta.drive.connectedSummary'); },
    get connectableSummary() { return i18n.t('connectors:meta.drive.connectableSummary'); },
    get examples(): string[] { return i18n.t('connectors:meta.drive.examples', { returnObjects: true }) as string[]; },
  },
};
const DEFAULT_TYPE_META = {
  displayName: '',
  icon: 'plug' as IconName,
  category: '',
  provider: '',
  connectedSummary: '',
  connectableSummary: '',
  examples: [] as string[],
};
const typeMeta = (connectorType: string) => CONNECTOR_TYPE_META[connectorType] ?? DEFAULT_TYPE_META;
/** 화면 기본 노출용 비개발자 요약. IAM/스코프/서비스명 같은 운영 용어는 여기 절대 넣지
 *  않는다 — 원문 진단은 errorTechnical(접힌 "기술 정보")로만 내려간다. */
const connectorErrorSummary = (info: ConnectorInfoWire): string | undefined => {
  switch (info.error_code) {
    case 'connectors.mcp_service_permission_denied':
      return i18n.t('connectors:detail.mcpServicePermissionDenied');
    case 'connectors.mcp_service_disabled':
      return i18n.t('connectors:detail.mcpServiceDisabled');
    case 'connectors.mcp_scope_insufficient':
      return i18n.t('connectors:detail.mcpScopeInsufficient');
    default:
      return info.error_detail ? i18n.t('connectors:detail.errorGenericSummary') : undefined;
  }
};

/** 접힌 "기술 정보"에 보여줄 원문 진단 — 런타임 error_detail(영문) + error_code. */
const connectorErrorTechnical = (info: ConnectorInfoWire): string | undefined => {
  if (!info.error_detail && !info.error_code) return undefined;
  return [info.error_code, info.error_detail].filter(Boolean).join(' — ');
};

/** 오류의 복구 동작 분류. "재연결 필요"는 OAuth 자격증명 문제에만 붙인다 —
 *  제공자 승인 대기(approval)에 재연결을 시키면 무의미한 OAuth 왕복만 생긴다. */
const connectorErrorKind = (info: ConnectorInfoWire): NonNullable<Connector['errorKind']> => {
  const code = info.error_code ?? '';
  if (code.startsWith('connectors.oauth_') || code === 'connectors.mcp_scope_insufficient') return 'reconnect';
  if (code === 'connectors.mcp_service_permission_denied' || code === 'connectors.mcp_service_disabled') return 'approval';
  return 'check';
};

/** 백엔드 ConnectorInfo → UI Connector 매핑 (remote 전용). */
const infoToConnector = (info: ConnectorInfoWire): Connector => {
  const connected = info.status === 'connected';
  const errored = info.status === 'error';
  const meta = typeMeta(info.connector_type);
  // 도구는 하드코딩이 아니라 백엔드가 연결 시점에 원격 MCP tools/list 로 받아 저장한 실제 카탈로그.
  // error(실행 검증 실패) 상태에서도 이미 동기화된 카탈로그는 보여준다 — 사용자는 "무엇을
  // 할 수 있게 되는지"를 봐야 하고, 실행 가능 여부는 오류 배너가 따로 알린다.
  const scopes: ConnectorScope[] = connected || errored ? (info.tools ?? []).map(toolToScope) : [];
  return {
    id: info.id,
    name: info.display_name || info.connector_type,
    icon: meta.icon,
    category: meta.category || info.connector_type,
    provider: meta.provider || info.connector_type,
    connectorType: info.connector_type,
    kind: 'remote',
    // 백엔드 'error'는 UI 상 미연결로 취급하되 errored 플래그 + errorKind 로 복구 경로를 노출.
    status: connected ? 'connected' : 'disconnected',
    errored,
    errorKind: errored ? connectorErrorKind(info) : undefined,
    errorDetail: connectorErrorSummary(info),
    errorTechnical: connectorErrorTechnical(info),
    setupUrl: info.setup_url ?? undefined,
    // 백엔드 레코드가 실재 — 미연결이어도 "제거" 가능(연결 실패한 커스텀 커넥터 정리 경로).
    persisted: true,
    account: connected ? info.display_name || i18n.t('connectors:detail.tagConnected') : undefined,
    lastSync: info.last_verified_at ?? undefined,
    summary: meta.connectedSummary || i18n.t('connectors:meta.fallbackSummary', { type: info.connector_type }),
    scopes,
    usedBy: [],
    examples: meta.examples,
    update: wireToUpdate(info.update),
  };
};

/**
 * 백엔드가 지원하지만(registry.py) 아직 연결 레코드가 없는 타입에 노출하는 "연결 가능" 카드.
 * connectorType 으로 실제(온라인) 목록과 매칭 — 이미 연결/생성된 타입이면 mergeRemote 가 뺀다.
 */
const CONNECTABLE_PRESETS: Connector[] = (['gmail', 'calendar', 'drive'] as const).map((connectorType) => {
  const meta = typeMeta(connectorType);
  return {
    id: connectorType,
    name: meta.displayName,
    icon: meta.icon,
    category: meta.category,
    provider: meta.provider,
    connectorType,
    kind: 'remote',
    status: 'disconnected',
    summary: meta.connectableSummary,
    scopes: [],
    usedBy: [],
    examples: meta.examples,
  };
});

/** 백엔드 LocalMcpServerWire(.mcp.json 파생) → UI Connector 매핑 (local-mcp 전용). */
const wireToLocalConnector = (s: LocalMcpServerWire): Connector => ({
  id: s.connector_id,
  name: s.name,
  icon: s.transport === 'http' ? 'cloud' : 'server',
  category: s.transport === 'http'
    ? i18n.t('connectors:meta.localHttpCategory')
    : i18n.t('connectors:meta.localStdioCategory'),
  provider: s.transport === 'http'
    ? i18n.t('connectors:meta.localHttpProvider')
    : i18n.t('connectors:meta.localStdioProvider'),
  kind: 'local-mcp',
  // running = 마지막 새로고침(spawn+tools/list)이 도구를 캐시했는지 — 호출마다 spawn 이라
  // 지속 프로세스는 없다. 그래서 "실행 중"은 곧 "캐시된 도구가 있어 바로 쓸 수 있음"이다.
  status: s.running ? 'connected' : 'disconnected',
  account: s.transport === 'http' ? s.url : [s.command, ...s.args].join(' '),
  lastSync: s.running ? i18n.t('connectors:meta.justNow') : undefined,
  summary: s.running
    ? i18n.t('connectors:meta.localSummaryRunning', { count: s.tools.length })
    : i18n.t('connectors:meta.localSummaryIdle'),
  transport: s.transport === 'http' ? { type: 'http', endpoint: s.url } : { type: 'stdio', command: s.command, args: s.args },
  scopes: s.tools.map(toolToScope),
  usedBy: [],
  examples: [],
});

/** stdio transport를 보기 좋은 한 줄 명령으로 렌더 */
const transportLine = (t: McpTransport): string =>
  t.type === 'stdio' ? [t.command, ...t.args].join(' ') : t.endpoint;

/**
 * ConnectorsScreen ("커넥터") — the home for linked external accounts / data
 * sources (databases, Slack, Outlook, Jira, …). Master-detail: connect/disconnect,
 * see each connector's access scope, which skills/plugins use it, and example
 * actions. Also the destination for "접근 권한 관리" links elsewhere.
 */

const FILTERS = ['all', 'connected', 'needsConnection', 'hasUpdate'] as const;
type Filter = (typeof FILTERS)[number];

const SectionLabel: React.FC<{ children: React.ReactNode; right?: React.ReactNode }> = ({ children, right }) => (
  <div className="flex items-center mb-2.5">
    <span className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">{children}</span>
    {right && <span className="ml-auto">{right}</span>}
  </div>
);

const StatusDot: React.FC<{ connected: boolean; attention?: boolean }> = ({ connected, attention }) => (
  <span
    className={`w-2 h-2 rounded-full flex-shrink-0 ${
      connected ? 'bg-support-success' : attention ? 'bg-support-warning' : 'bg-layer-03'
    }`}
  />
);

/**
 * ToolDescription — 원격 MCP 서버가 제공한 도구 설명. 기본 2줄로 잘라 보여주고,
 * 실제로 잘렸을 때만(scrollHeight > clientHeight) "더보기/접기"를 노출한다.
 */
const ToolDescription: React.FC<{ text: string; blocked?: boolean }> = ({ text, blocked }) => {
  const { t } = useTranslation('connectors');
  const ref = useRef<HTMLParagraphElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [clamped, setClamped] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setClamped(el.scrollHeight > el.clientHeight + 1);
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [text, expanded]);

  return (
    <>
      <p
        ref={ref}
        title={expanded ? undefined : text}
        className={`mt-0.5 text-caption-01 leading-snug ${expanded ? '' : 'line-clamp-2'} ${
          blocked ? 'text-text-helper opacity-60' : 'text-text-helper'
        }`}
      >
        {text}
      </p>
      {(clamped || expanded) && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-0.5 text-caption-01 font-medium text-link-primary hover:underline"
        >
          {expanded ? t('detail.toolCollapse') : t('detail.toolExpand')}
        </button>
      )}
    </>
  );
};

/** 도구를 읽기 전용 / 쓰기·실행 둘로 가른다 (그룹 헤더·기본 권한 결정용) */
const isReadOnly = (s: ConnectorScope) => s.mode === 'read';
/** 도구 권한을 명시하지 않았으면 성격으로 추정: read → allow, 쓰기·실행 → approval */
const permOf = (s: ConnectorScope): ScopePermission => s.permission ?? (isReadOnly(s) ? 'allow' : 'approval');

type PermLabelKey = 'detail.permAllow' | 'detail.permApproval' | 'detail.permBlock';
const PERMS: { id: ScopePermission; labelKey: PermLabelKey; icon: IconName; active: string }[] = [
  // status-chip(allow/ask/block)과 동일한 색 언어 — support 토큰 기반
  { id: 'allow',    labelKey: 'detail.permAllow',    icon: 'check',    active: 'bg-support-success/[0.13] text-support-success' },
  { id: 'approval', labelKey: 'detail.permApproval', icon: 'user',     active: 'bg-support-warning/[0.13] text-support-warning' },
  { id: 'block',    labelKey: 'detail.permBlock',    icon: 'view-off', active: 'bg-support-error/15 text-text-error' },
];

/**
 * PermissionControl — 한 도구를 에이전트가 언제 호출할 수 있는지 정하는 3단 토글.
 * 도구를 "추가"하는 게 아니라 제공된 도구의 사용 시점(항상 허용/승인 필요/차단)을 고른다.
 */
const PermissionControl: React.FC<{
  value: ScopePermission;
  disabled?: boolean;
  onChange: (p: ScopePermission) => void;
}> = ({ value, disabled, onChange }) => {
  const { t } = useTranslation('connectors');
  return (
    <div
      className={`inline-flex items-center gap-0.5 p-0.5 rounded-lg bg-field-01 border border-border-subtle-01 flex-shrink-0 ${
        disabled ? 'opacity-50 pointer-events-none' : ''
      }`}
      role="radiogroup"
    >
      {PERMS.map((p) => {
        const on = p.id === value;
        return (
          <button
            key={p.id}
            type="button"
            role="radio"
            aria-checked={on}
            title={p.id}
            onClick={() => onChange(p.id)}
            className={`inline-flex items-center gap-1 px-2 py-1 rounded-md text-caption-01 font-medium transition-colors ${
              on ? p.active : 'text-text-helper hover:text-text-primary hover:bg-layer-hover-01'
            }`}
          >
            <Icon name={p.icon} size={12} />
            <span>{t(p.labelKey)}</span>
          </button>
        );
      })}
    </div>
  );
};

/**
 * GroupPermissionMenu — 그룹(읽기/쓰기) 전체 도구의 권한을 한 번에 설정하는 드롭다운.
 * 도구가 많은 커넥터에서 일일이 토글하지 않도록 벤치마크의 그룹 헤더 컨트롤을 따른다.
 */
const GroupPermissionMenu: React.FC<{ disabled?: boolean; onPick: (p: ScopePermission) => void }> = ({
  disabled,
  onPick,
}) => {
  const { t } = useTranslation('connectors');
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className={`inline-flex items-center gap-1 px-2 py-1 rounded-md text-caption-01 font-medium transition-colors ${
          disabled
            ? 'text-text-helper opacity-50 cursor-not-allowed'
            : 'text-text-helper hover:text-text-primary hover:bg-layer-hover-01'
        }`}
      >
        {t('detail.permSetAll')} <Icon name={open ? 'chevron-up' : 'chevron-down'} size={11} />
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-10 min-w-[120px] py-1 rounded-lg bg-layer-02 border border-border-subtle-01 shadow-elevation-2">
          {PERMS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => {
                onPick(p.id);
                setOpen(false);
              }}
              className="flex items-center gap-2 w-full px-3 py-1.5 text-caption-01 text-text-primary hover:bg-layer-hover-01 transition-colors"
            >
              <Icon name={p.icon} size={12} className="flex-shrink-0" />
              <span>{t(p.labelKey)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

/**
 * AddConnectorMenu — 원격 탭의 "추가" 드롭다운: 큐레이팅된 카탈로그를 둘러보거나,
 * 임의의 MCP 서버 URL 로 커스텀 커넥터를 추가한다(OAuth 자동 디스커버리).
 */
const AddConnectorMenu: React.FC<{ onBrowse: () => void; onCustom: () => void }> = ({ onBrowse, onCustom }) => {
  const { t } = useTranslation('connectors');
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1 text-caption-01 text-link-primary hover:underline"
      >
        <Icon name="add" size={13} /> {t('list.add')}
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-10 min-w-[170px] py-1 rounded-lg bg-layer-02 border border-border-subtle-01 shadow-elevation-2">
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onBrowse();
            }}
            className="flex items-center gap-2 w-full px-3 py-1.5 text-caption-01 text-text-primary hover:bg-layer-hover-01 transition-colors"
          >
            <Icon name="search" size={12} className="flex-shrink-0" /> {t('list.browse')}
          </button>
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onCustom();
            }}
            className="flex items-center gap-2 w-full px-3 py-1.5 text-caption-01 text-text-primary hover:bg-layer-hover-01 transition-colors"
          >
            <Icon name="tools" size={12} className="flex-shrink-0" /> {t('list.addCustom')}
          </button>
        </div>
      )}
    </div>
  );
};

const ConnectorDetailView: React.FC<{
  connectorId: string | null;
  connectors: Connector[];
  setConnectors: React.Dispatch<React.SetStateAction<Connector[]>>;
  online: boolean;
  busyId: string | null;
  onRealConnect: (c: Connector) => void;
  onRealDisconnect: (c: Connector) => void;
  onResyncTools: (c: Connector) => void;
  onVerify: (c: Connector) => void;
  onRefreshLocal: (c: Connector) => void;
  onRemoveLocal: (c: Connector) => void;
  onApplyUpdate: (c: Connector) => void;
  onSetToolPermission: (connectorId: string, toolName: string, perm: ScopePermission) => void;
}> = ({
  connectorId,
  connectors,
  setConnectors,
  online,
  busyId,
  onRealConnect,
  onRealDisconnect,
  onResyncTools,
  onVerify,
  onRefreshLocal,
  onRemoveLocal,
  onApplyUpdate,
  onSetToolPermission,
}) => {
  const { t } = useTranslation('connectors');
  const c = connectors.find((x) => x.id === connectorId);
  const [confirmingRemove, setConfirmingRemove] = useState(false);
  const [showTechInfo, setShowTechInfo] = useState(false);
  // 다른 커넥터로 넘어가면 "정말 제거할까요?"/기술 정보 펼침 상태가 안 따라오게 초기화.
  useEffect(() => {
    setConfirmingRemove(false);
    setShowTechInfo(false);
  }, [connectorId]);

  if (!c || !connectorId) {
    return <div className="flex-1 flex items-center justify-center text-body-short-02 text-text-helper">{t('detail.noSelection')}</div>;
  }

  const connected = c.status === 'connected';
  const errored = !!c.errored;
  const isMcp = connectorKind(c) === 'local-mcp';
  const busy = busyId === c.id;
  // 승인 대기/원인 미상 오류의 주 복구 동작은 재검증 — OAuth 재연결은 자격증명 오류에만.
  const verifyRecovery = errored && !isMcp && online && c.errorKind !== 'reconnect';
  // 제거 가능: 로컬 MCP(.mcp.json 항목 삭제) 또는 미연결이지만 백엔드 레코드가 남아 있는
  // 원격 커넥터(연결 실패·중도 취소된 커스텀 커넥터 정리 경로 — DELETE /connectors/{id}).
  // 연결된 원격은 아래 "연결 해제" 버튼이 같은 삭제를 수행하므로 중복 노출하지 않는다.
  const removable = online && (isMcp || (!!c.persisted && !connected));
  const patch = (fn: (c: Connector) => Connector) => setConnectors((a) => a.map((x) => (x.id === connectorId ? fn(x) : x)));
  const toggleConnect = () => {
    if (!online) return;
    // 런타임 온라인 + 원격 커넥터: 실제 OAuth 연결/해제 경로(백엔드 /connectors).
    if (online && !isMcp) {
      if (connected) onRealDisconnect(c);
      else onRealConnect(c);
      return;
    }
    // 런타임 온라인 + 로컬 MCP: 호출마다 spawn 이라 "중지"할 프로세스가 없다 — 다시 spawn 해
    // 도구 카탈로그를 새로고침하는 것만 실제 동작이다.
    if (online && isMcp) {
      onRefreshLocal(c);
      return;
    }
  };
  // 도구는 제공자가 정의하므로 추가/삭제하지 않고 "언제 호출할 수 있는가"만 바꾼다.
  // 실제(온라인) 커넥터(원격이든 로컬 MCP 든)면 백엔드에 정책을 저장한다 — dispatcher 가 강제.
  const persistPerm = (toolName: string, perm: ScopePermission) => {
    if (online) onSetToolPermission(c.id, toolName, perm);
  };
  const setScopePermission = (idx: number, perm: ScopePermission) => {
    if (!online) return;
    const tool = c.scopes[idx];
    patch((x) => ({ ...x, scopes: x.scopes.map((s, i) => (i === idx ? { ...s, permission: perm } : s)) }));
    if (tool) persistPerm(tool.name, perm);
  };
  // 그룹(읽기/쓰기) 전체를 한 번에 설정 — 도구가 많은 커넥터(Gmail 등)에서 필수
  const setGroupPermission = (predicate: (s: ConnectorScope) => boolean, perm: ScopePermission) => {
    if (!online) return;
    const targets = c.scopes.filter(predicate);
    patch((x) => ({ ...x, scopes: x.scopes.map((s) => (predicate(s) ? { ...s, permission: perm } : s)) }));
    targets.forEach((t) => persistPerm(t.name, perm));
  };
  // Updates are applied by the runtime and reloaded from its persisted catalog.
  const applyUpdate = () => {
    if (online && !isMcp && c.update) onApplyUpdate(c);
  };

  const meta: [string, string][] = isMcp
    ? [
        [t('detail.metaTransport'), c.transport?.type === 'http' ? t('detail.metaTransportHttp') : t('detail.metaTransportStdio')],
        [t('detail.metaCategory'), c.category],
        [t('detail.metaEndpoint'), c.transport ? transportLine(c.transport) : '—'],
        [t('detail.metaLastVerified'), c.lastSync ?? '—'],
      ]
    : [
        [t('detail.metaProvider'), c.provider],
        [t('detail.metaCategory'), c.category],
        [t('detail.metaAccount'), c.account ?? '—'],
        [t('detail.metaLastSync'), c.lastSync ?? '—'],
      ];

  return (
    <div className="flex-1 min-w-0 overflow-y-auto">
      {/* header */}
      <div className="px-7 pt-6 pb-5 border-b border-border-subtle-01">
        <div className="flex items-start gap-3.5">
          <div className={`w-11 h-11 flex-shrink-0 flex items-center justify-center rounded-lg border ${
            connected
              ? 'bg-teal-60/14 border-teal-40/30 text-teal-30'
              : 'bg-layer-03 border-border-subtle-02 text-text-helper'
          }`}>
            <Icon name={c.icon} size={22} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-heading-03 font-semibold text-text-primary">{c.name}</h2>
              {errored ? (
                // Tag red는 어두운 적색 배경이 뭉개져서 명도 대비가 있는 chip으로.
                // 승인 대기(approval)는 실패가 아니라 대기 상태 — 재연결 문구·적색을 쓰지 않는다.
                c.errorKind === 'reconnect' ? (
                  <span className="status-chip status-chip--block">{t('detail.tagReconnectNeeded')}</span>
                ) : (
                  <span className="status-chip status-chip--ask">
                    {t(c.errorKind === 'approval' ? 'detail.tagAwaitingApproval' : 'detail.tagCheckNeeded')}
                  </span>
                )
              ) : (
                <Tag type={connected ? 'green' : 'gray'} size="sm">
                  {connected ? t('detail.tagConnected') : t('detail.tagNeedsConnection')}
                </Tag>
              )}
            </div>
            <p className="mt-1.5 text-body-short-01 leading-5 text-text-secondary">{c.summary}</p>
          </div>
          <div className="flex-shrink-0 flex items-center gap-2">
            {/* 제거 — 로컬 MCP 는 .mcp.json 에서, 미연결 원격은 백엔드 레코드가 실제로
                지워지는 파괴적 동작이라 인라인 확인을 거친다. */}
            {removable && (
              confirmingRemove ? (
                <div className="flex items-center gap-1.5">
                  <span className="text-caption-01 text-text-helper">{t('detail.confirmRemovePrompt')}</span>
                  <Button kind="ghost" size="sm" disabled={busy} onClick={() => setConfirmingRemove(false)}>
                    {t('detail.btnCancel')}
                  </Button>
                  <Button
                    kind="danger"
                    size="sm"
                    disabled={busy}
                    onClick={() => {
                      setConfirmingRemove(false);
                      if (isMcp) onRemoveLocal(c);
                      else onRealDisconnect(c);
                    }}
                  >
                    {t('detail.btnConfirmRemove')}
                  </Button>
                </div>
              ) : (
                <Button
                  kind="danger-ghost"
                  size="sm"
                  disabled={busy}
                  icon={<Icon name="trash" size={14} />}
                  onClick={() => setConfirmingRemove(true)}
                >
                  {t('detail.btnRemove')}
                </Button>
              )
            )}
            <Button
              kind={connected ? 'secondary' : 'primary'}
              size="sm"
              disabled={busy || !online}
              icon={<Icon name={busy ? 'renew' : isMcp && online ? 'renew' : connected ? 'close' : verifyRecovery ? 'renew' : 'connect'} size={15} />}
              onClick={verifyRecovery ? () => onVerify(c) : toggleConnect}
            >
              {busy
                ? verifyRecovery
                  ? t('detail.btnVerifying')
                  : isMcp && online
                  ? t('detail.btnRefreshing')
                  : online && !connected
                  ? t('detail.btnConnecting')
                  : t('detail.btnProcessing')
                : isMcp
                ? online
                  ? t('detail.btnRefresh')
                  : connected
                  ? t('detail.btnStop')
                  : t('detail.btnRun')
                : connected
                ? t('detail.btnDisconnect')
                : errored
                ? verifyRecovery
                  ? t('detail.btnVerifyAgain')
                  : t('detail.btnReconnect')
                : t('detail.btnConnect')}
            </Button>
          </div>
        </div>

        {/* update available — 제공사/서버가 도구를 갱신했을 때 설명 바로 아래 표시 */}
        {c.update && (
          <div className="mt-4 flex items-center gap-3 rounded-xl border border-blue-50/30 bg-gradient-to-r from-blue-60/12 to-blue-60/[0.04] pl-3 pr-3 py-2.5">
            <div className="w-8 h-8 flex-shrink-0 flex items-center justify-center rounded-lg bg-blue-60/15">
              <Icon name="renew" size={16} className="text-link-primary" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1.5 text-body-short-01 leading-5 text-text-primary">
                <span className="font-semibold">{isMcp ? t('detail.update.newServerVersion') : t('detail.update.newVersion')}</span>
                <span className="font-mono font-semibold text-link-primary">
                  {t('detail.update.versionLabel', { count: (c.update.addedCount ?? 0) + (c.update.removedCount ?? 0) })}
                </span>
                <span className="text-text-secondary">{t('detail.update.available')}</span>
              </div>
              <div className="text-caption-01 leading-4 text-text-helper truncate mt-0.5">
                {(() => {
                  const parts: string[] = [];
                  if (c.update.addedCount) parts.push(t('detail.update.added', { count: c.update.addedCount }));
                  if (c.update.removedCount) parts.push(t('detail.update.removed', { count: c.update.removedCount }));
                  return t('detail.update.note', { summary: parts.join(' · ') });
                })()}
              </div>
            </div>
            <Button kind="primary" size="sm" icon={<Icon name="renew" size={14} />} onClick={applyUpdate} className="flex-shrink-0">
              {t('detail.update.btnApply')}
            </Button>
          </div>
        )}

        {errored && c.errorDetail && (
          <div className="mt-4 rounded-xl border border-support-error/35 bg-support-error/10 px-3.5 py-3">
            <div className="flex items-start gap-3">
              <Icon name="warning" size={17} className="mt-0.5 flex-shrink-0 text-text-error" />
              <div className="min-w-0 flex-1">
                <div className="text-body-short-01 font-semibold text-text-primary">{t('detail.connectionCheckFailed')}</div>
                <p className="mt-1 text-caption-01 leading-5 text-text-secondary">{c.errorDetail}</p>
              </div>
              <Button kind="secondary" size="sm" disabled={busy} onClick={() => onVerify(c)}>
                {busy ? t('detail.btnVerifying') : t('detail.btnVerifyAgain')}
              </Button>
            </div>
            {/* 운영/개발자용 원문 진단은 기본 접힘 — 비개발자 화면을 IAM·스코프 용어로 오염시키지 않는다. */}
            {(c.errorTechnical || c.setupUrl) && (
              <div className="mt-2 pl-8">
                <button
                  onClick={() => setShowTechInfo((v) => !v)}
                  className="text-caption-01 text-text-helper underline underline-offset-2 hover:text-text-secondary transition-colors"
                >
                  {showTechInfo ? t('detail.hideTechInfo') : t('detail.showTechInfo')}
                </button>
                {showTechInfo && (
                  <div className="mt-2 flex flex-col items-start gap-2">
                    {c.errorTechnical && (
                      <p className="text-caption-01 leading-5 text-text-helper font-mono break-all select-text">
                        {c.errorTechnical}
                      </p>
                    )}
                    {c.setupUrl && (
                      <Button kind="ghost" size="sm" onClick={() => void openExternal(c.setupUrl!)}>
                        {t('detail.openSetupGuide')}
                      </Button>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="px-7 pt-5 pb-10 flex flex-col gap-7">
        {/* local-mcp: 실행 신뢰 경고 */}
        {isMcp && (
          <div className="flex items-start gap-2.5 px-3.5 py-3 rounded-lg bg-red-60/10 border border-red-60/30">
            <Icon name="warning" size={16} filled className="text-red-40 flex-shrink-0 mt-0.5" />
            <p className="text-caption-01 text-text-helper leading-relaxed">
              {t('detail.localWarning_pre')}<span className="text-red-40 font-medium">{t('detail.localWarning_strong')}</span>{t('detail.localWarning_post')}
            </p>
          </div>
        )}

        {/* metadata */}
        <div>
          <SectionLabel
            right={
              connected ? (
                <button
                  type="button"
                  disabled={busy || !online}
                  title={isMcp ? t('detail.resyncTitleLocal') : t('detail.resyncTitleRemote')}
                  onClick={() => {
                    if (!online) return;
                    if (isMcp) onRefreshLocal(c);
                    else onResyncTools(c);
                  }}
                  className="inline-flex items-center gap-1 text-caption-01 text-link-primary hover:underline disabled:opacity-50 disabled:no-underline"
                >
                  <Icon name="renew" size={12} className={busy ? 'animate-spin' : undefined} />
                  {isMcp ? t('detail.resyncLabel') : busy ? t('detail.resyncLabelBusy') : t('detail.resyncLabelRemote')}
                </button>
              ) : undefined
            }
          >
            {t('detail.sectionInfo')}
          </SectionLabel>
          <div className="grid grid-cols-2 gap-x-6 gap-y-3.5">
            {meta.map(([k, v]) => (
              <div key={k}>
                <div className="text-caption-01 text-text-helper">{k}</div>
                <div className="text-body-short-01 text-text-primary mt-0.5 truncate">{v}</div>
              </div>
            ))}
          </div>
        </div>

        {/* tool permissions — 도구는 제공자가 정의하고, 사용자는 "언제 호출할지"만 정한다 */}
        <div data-tour="connectors-detail-tools">
          <SectionLabel>{t('detail.sectionTools', { count: c.scopes.length })}</SectionLabel>
          <p data-tour="connectors-detail-perm-control" className="text-caption-01 text-text-helper -mt-1 mb-3 leading-relaxed">
            {isMcp ? t('detail.toolsDesc_local') : t('detail.toolsDesc_remote', { provider: c.provider })}{' '}
            <span className="status-chip status-chip--allow">{t('detail.permAllowFull')}</span>{' '}
            <span className="status-chip status-chip--ask">{t('detail.permApprovalFull')}</span>{' '}
            <span className="status-chip status-chip--block">{t('detail.permBlockFull')}</span>
          </p>
          {c.scopes.length === 0 ? (
            <div className="flex items-center gap-2.5 text-caption-01 text-text-helper border border-dashed border-border-strong-01 rounded-lg px-3.5 py-3">
              <Icon name="tools" size={15} className="text-icon-secondary flex-shrink-0" />
              {connected
                ? isMcp
                  ? t('detail.toolsEmpty_connectedLocal')
                  : t('detail.toolsEmpty_connectedRemote')
                : isMcp
                ? t('detail.toolsEmpty_disconnectedLocal')
                : t('detail.toolsEmpty_disconnectedRemote')}
            </div>
          ) : (
            <div className="flex flex-col gap-4">
              {([
                { key: 'read', label: t('detail.toolGroupRead'), filter: (s: ConnectorScope) => isReadOnly(s) },
                { key: 'write', label: isMcp ? t('detail.toolGroupExec') : t('detail.toolGroupWrite'), filter: (s: ConnectorScope) => !isReadOnly(s) },
              ] as const)
                .map((g) => ({ ...g, items: c.scopes.map((s, idx) => ({ s, idx })).filter((e) => g.filter(e.s)) }))
                .filter((g) => g.items.length > 0)
                .map((g) => (
                  <div key={g.key}>
                    <div className="flex items-center gap-1.5 mb-2">
                      <Icon
                        name={g.key === 'read' ? 'view' : isMcp ? 'terminal' : 'edit'}
                        size={13}
                        className="text-icon-secondary flex-shrink-0"
                      />
                      <span className="text-caption-01 font-semibold text-text-secondary">{g.label}</span>
                      <span className="text-caption-01 text-text-helper">{g.items.length}</span>
                      <div className="ml-auto">
                        <GroupPermissionMenu disabled={!online || !connected} onPick={(p) => setGroupPermission(g.filter, p)} />
                      </div>
                    </div>
                    <div className="flex flex-col gap-px bg-border-subtle-01 rounded-lg overflow-hidden">
                      {g.items.map(({ s, idx }) => {
                        const blocked = permOf(s) === 'block';
                        return (
                          <div key={idx} className="flex items-start gap-2.5 bg-background px-3.5 py-2.5">
                            <div className="flex-1 min-w-0">
                              <span
                                title={s.name}
                                className={`block text-body-short-01 truncate ${isMcp ? 'font-mono' : ''} ${
                                  blocked ? 'text-text-helper line-through opacity-60' : 'text-text-primary'
                                }`}
                              >
                                {s.name}
                              </span>
                              {s.description && <ToolDescription text={s.description} blocked={blocked} />}
                            </div>
                            <PermissionControl
                              value={permOf(s)}
                              disabled={!online || !connected}
                              onChange={(p) => setScopePermission(idx, p)}
                            />
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))}
            </div>
          )}
        </div>

        {/* where used — 방향을 명확히: 이 커넥터의 도구를 호출하는 쪽 */}
        <div>
          <SectionLabel>{t('detail.sectionUsedBy', { count: c.usedBy.length })}</SectionLabel>
          <p className="text-caption-01 text-text-helper -mt-1 mb-2.5 leading-relaxed">
            {t('detail.usedByDesc')}
          </p>
          {c.usedBy.length === 0 ? (
            <div className="flex items-center gap-2.5 text-caption-01 text-text-helper border border-dashed border-border-strong-01 rounded-lg px-3.5 py-3">
              <Icon name="skill" size={15} className="text-icon-secondary flex-shrink-0" />
              {t('detail.usedByEmpty')}
            </div>
          ) : (
            <div className="flex flex-col gap-px bg-border-subtle-01 rounded-lg overflow-hidden">
              {c.usedBy.map((u, i) => (
                <div key={i} className="flex items-center gap-2.5 bg-background px-3.5 py-2.5">
                  <Icon name={u.icon} size={16} className="text-icon-secondary flex-shrink-0" />
                  <span className="text-body-short-01 text-text-primary flex-1 min-w-0 truncate">{u.name}</span>
                  <Tag type="gray" size="sm">
                    {u.kind}
                  </Tag>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

function ConnectorsScreen() {
  const { t } = useTranslation('connectors');
  const { id } = useParams<{ id?: string }>();
  const [connectors, setConnectors] = useState<Connector[]>(CONNECTABLE_PRESETS);
  const [online, setOnline] = useState<boolean>(api.isEnabled);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>('all');
  const initial = id ? CONNECTABLE_PRESETS.find((c) => c.id === id) : undefined;
  const [kind, setKind] = useState<Kind>(id?.startsWith('local:') ? 'local-mcp' : 'remote');
  const [mcpModalOpen, setMcpModalOpen] = useState(false);
  const [directoryOpen, setDirectoryOpen] = useState(false);
  const [customModalOpen, setCustomModalOpen] = useState(false);
  const [sel, setSel] = useState<string | null>(initial?.id ?? null);
  const toast = useToast();

  // 화면을 떠나면(다른 스크린으로 이동 등) 진행 중이던 폴링을 계속하지 않는다 — 안 그러면
  // 백그라운드에서 최대 2분간 낭비되는 요청이 나가고, 이미 의미 없어진 결과로 토스트를 띄우게 된다.
  const mountedRef = useRef(true);
  useEffect(() => {
    // StrictMode(dev)는 mount→cleanup→mount 로 이펙트를 두 번 돌린다 — 본문에서 true 로
    // 되돌리지 않으면 첫 cleanup 이 남긴 false 가 영구화되어, OAuth 폴링이 첫 tick 에
    // "화면을 떠났다"고 오판하고 조용히 중단된다(연결 완료가 화면에 안 보이는 버그).
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Resizable left rail
  const railPanel = useResizable({
    initial: 282,
    min: 184,
    max: 360,
    storageKey: 'rice.panel.connectors',
  });

  // Resizable connector list
  const listPanel = useResizable({
    initial: 312,
    min: 240,
    max: 480,
    storageKey: 'rice.panel.connectors-list',
  });

  // deep-link: selecting via /connectors/:id (e.g. from a tool scope chip).
  // Also switch the kind tab to wherever the linked connector lives.
  useEffect(() => {
    const target = id ? connectors.find((c) => c.id === id) : undefined;
    if (target) {
      setSel(target.id);
      setKind(connectorKind(target));
    }
  }, [id, connectors]);

  // ── 런타임 실연동: 온라인이면 'remote'/'local-mcp' 커넥터 둘 다 백엔드 실데이터로 대체(목업 제거) ──
  const mergeRemote = (real: Connector[]) => {
    setConnectors((prev) => {
      const localMcp = prev.filter((x) => connectorKind(x) === 'local-mcp');
      const presentTypes = new Set(real.map((x) => x.connectorType).filter(Boolean));
      const missingPresets = CONNECTABLE_PRESETS.filter((p) => !presentTypes.has(p.connectorType));
      return [...real, ...missingPresets, ...localMcp];
    });
  };

  const refresh = async () => {
    const res = await api.getConnectors();
    if (res.success && Array.isArray(res.data)) {
      mergeRemote((res.data as ConnectorInfoWire[]).map(infoToConnector));
    }
  };

  const mergeLocal = (real: Connector[]) => {
    setConnectors((prev) => [...prev.filter((x) => connectorKind(x) !== 'local-mcp'), ...real]);
  };

  /** `.mcp.json` 서버 + 캐시된 도구를 읽어온다(spawn 없음 — 그냥 GET). */
  const loadLocal = async () => {
    const res = await api.getLocalConnectors();
    if (res.success && Array.isArray(res.data)) {
      mergeLocal((res.data as LocalMcpServerWire[]).map(wireToLocalConnector));
    }
  };

  // 화면 진입 시(온디맨드) 연결된 커넥터의 원격 카탈로그 변경을 확인 → pending 업데이트가
  // 잡히면 배지가 뜬다. 부팅/백그라운드가 아니라 사용자가 커넥터 화면을 볼 때만 원격을 때린다.
  const runUpdateChecks = async (infos: ConnectorInfoWire[]) => {
    const connected = infos.filter((i) => i.status === 'connected');
    if (connected.length === 0) return;
    await Promise.all(connected.map((i) => api.checkConnectorUpdates(i.id).catch(() => undefined)));
    await refresh(); // pending update 가 payload 에 실려 오도록 한 번 다시 불러온다
  };

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      if (!api.isEnabled) return;
      setOnline(true);
      const res = await api.getConnectors();
      if (cancelled) return;
      if (res.success && Array.isArray(res.data)) {
        const infos = res.data as ConnectorInfoWire[];
        mergeRemote(infos.map(infoToConnector));
        void runUpdateChecks(infos);
      }
      void loadLocal();
    };
    void load();
    const unsub = api.onEnabledChange((en) => {
      setOnline(en);
      if (en) void load();
      else setConnectors(CONNECTABLE_PRESETS);
    });
    return () => {
      cancelled = true;
      unsub();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 브라우저 OAuth 완료까지 폴링(최대 ~2분) — connected/error 둘 다 terminal이다.
  // 신규 연결(connector_type 기준)과 커스텀 커넥터 등록 직후(connector_id 기준) 양쪽에 쓴다
  // (구 pollConnected/pollConnectedById 를 통합 — 로직이 거의 동일해 중복이었다).
  const waitForPollInterval = (signal: AbortSignal, delayMs: number): Promise<boolean> =>
    new Promise((resolve) => {
      if (signal.aborted) {
        resolve(false);
        return;
      }
      const timer = window.setTimeout(() => {
        signal.removeEventListener('abort', onAbort);
        resolve(true);
      }, delayMs);
      const onAbort = () => {
        window.clearTimeout(timer);
        resolve(false);
      };
      signal.addEventListener('abort', onAbort, { once: true });
    });

  const pollUntilTerminal = async (
    predicate: (info: ConnectorInfoWire) => boolean,
    signal: AbortSignal,
  ): Promise<ConnectorInfoWire | null> => {
    const deadline = Date.now() + 120_000;
    while (Date.now() < deadline) {
      if (!(await waitForPollInterval(signal, Math.min(2500, deadline - Date.now())))) return null;
      if (!mountedRef.current) return null;

      // 로컬 runtime 요청 하나가 멈춰도 전체 OAuth 대기가 무한정 늘어나지 않게 한다.
      // 사용자 AbortSignal도 전달해 토스트 X가 진행 중 fetch까지 즉시 끊는다.
      const requestController = new AbortController();
      const forwardAbort = () => requestController.abort();
      signal.addEventListener('abort', forwardAbort, { once: true });
      const requestTimeout = window.setTimeout(
        () => requestController.abort(),
        Math.min(5000, Math.max(1, deadline - Date.now())),
      );
      const res = await api.getConnectors(requestController.signal);
      window.clearTimeout(requestTimeout);
      signal.removeEventListener('abort', forwardAbort);
      if (!mountedRef.current || signal.aborted) return null;
      if (res.success && Array.isArray(res.data)) {
        const found = (res.data as ConnectorInfoWire[]).find(
          (info) => predicate(info) && (info.status === 'connected' || info.status === 'error'),
        );
        if (found) return found;
      }
    }
    return null;
  };

  const handleRealConnect = async (c: Connector) => {
    // placeholder 카드(아직 연결 안 됨)는 connectorType 이 곧 백엔드 타입. 이미 연결된 적 있는
    // (error 상태) 실제 커넥터는 infoToConnector 가 채운 connectorType 을 그대로 쓴다.
    const connectorType = c.connectorType ?? c.id;
    setBusyId(c.id);
    const controller = new AbortController();
    let connectorId: string | undefined;
    const cancel = () => {
      if (controller.signal.aborted) return;
      controller.abort();
      if (connectorId) void api.cancelConnectorOAuth(connectorId);
      toast.notify({ message: t('toast.loginCancelled'), variant: 'info' });
    };
    const job = toast.progress({
      label: t('toast.connectStart'),
      sublabel: c.name,
      onCancel: cancel,
    });
    try {
      const res = await api.connectConnector(connectorType);
      const data = res.data as { oauth_url?: string; connector_id?: string } | undefined;
      connectorId = data?.connector_id;
      if (controller.signal.aborted) {
        if (connectorId) await api.cancelConnectorOAuth(connectorId);
        return;
      }
      const url = data?.oauth_url;
      if (!res.success || !url || !data?.connector_id) {
        job.done('error', res.code ? apiErrorMessage(res) : t('toast.connectFail', { name: c.name }));
        return;
      }
      await openExternal(url); // 시스템 브라우저에서 구글 로그인
      job.update({
        label: t('toast.browserLoginWaiting'),
        sublabel: t('toast.browserLoginCancelHint', { name: c.name }),
      });
      const outcome = await pollUntilTerminal((x) => x.id === data.connector_id, controller.signal);
      if (controller.signal.aborted) return;
      if (!mountedRef.current) {
        job.dismiss(); // 화면을 이미 떠났다 — 결과를 알릴 대상이 없다.
        return;
      }
      if (!outcome) {
        await api.cancelConnectorOAuth(data.connector_id);
        job.done('error', t('toast.loginTimeout'));
        return;
      }
      const res2 = await api.getConnectors();
      if (res2.success && Array.isArray(res2.data)) {
        const infos = res2.data as ConnectorInfoWire[];
        mergeRemote(infos.map(infoToConnector));
        const found = infos.find((x) => x.connector_type === connectorType && x.status === 'connected');
        if (found) setSel(found.id);
      }
      if (outcome.status === 'error') {
        setSel(outcome.id);
        job.done(
          'error',
          outcome.error_code === 'connectors.oauth_cancelled'
            ? t('toast.loginCancelled')
            : connectorErrorSummary(outcome) ?? t('toast.verifyFail'),
        );
        return;
      }
      job.done('success', t('toast.connectSuccess', { name: c.name }));
    } finally {
      setBusyId(null);
    }
  };

  // 부작용 없음(RFC 9728+8414 조회만) — 모달의 "검사" 버튼.
  const handleDiscoverCustom = async (mcpEndpoint: string): Promise<DiscoveredOAuthConfig | null> => {
    const res = await api.discoverCustomConnector(mcpEndpoint);
    return res.success ? (res.data as DiscoveredOAuthConfig) ?? null : null;
  };

  // "추가하고 연결" — 여기서부터 부작용(동적 client 등록 + OAuth 시작).
  // 반환: null=성공, string=실패 사유(모달이 그대로 표시 — 백엔드의 fail-closed 메시지
  // 예: "동적 client 등록 미지원 — client ID를 직접 입력해주세요"를 사용자가 봐야 한다).
  const handleAddCustom = async (payload: CustomConnectorAddPayload): Promise<string | null> => {
    const res = await api.addCustomConnector(payload);
    const data = res.data as { oauth_url?: string; connector_id?: string } | undefined;
    if (!res.success || !data?.oauth_url || !data.connector_id) {
      return res.code ? apiErrorMessage(res) : t('toast.customConnectFail');
    }
    setCustomModalOpen(false);
    await openExternal(data.oauth_url); // 시스템 브라우저에서 로그인
    const connectorId = data.connector_id;
    const controller = new AbortController();
    const cancel = () => {
      if (controller.signal.aborted) return;
      controller.abort();
      void api.cancelConnectorOAuth(connectorId);
      toast.notify({ message: t('toast.loginCancelled'), variant: 'info' });
    };
    const job = toast.progress({
      label: t('toast.browserLoginWaiting'),
      sublabel: t('toast.browserLoginCancelHint', { name: payload.display_name }),
      onCancel: cancel,
    });
    const outcome = await pollUntilTerminal((x) => x.id === connectorId, controller.signal);
    if (controller.signal.aborted) return null;
    if (!mountedRef.current) {
      job.dismiss();
      return null;
    }
    if (outcome?.status === 'connected') {
      await refresh();
      setSel(connectorId);
      job.done('success', t('toast.connectSuccess', { name: payload.display_name }));
    } else if (outcome?.status === 'error') {
      await refresh();
      setSel(connectorId);
      job.done(
        'error',
        outcome.error_code === 'connectors.oauth_cancelled'
          ? t('toast.loginCancelled')
          : connectorErrorSummary(outcome) ?? t('toast.verifyFail'),
      );
    } else {
      await api.cancelConnectorOAuth(connectorId);
      job.done('error', t('toast.loginTimeout'));
    }
    return null;
  };

  const handleRealDisconnect = async (c: Connector) => {
    setBusyId(c.id);
    try {
      const res = await api.deleteConnector(c.id);
      if (!res.success) {
        toast.notify({ message: res.code ? apiErrorMessage(res) : t('toast.disconnectFail', { name: c.name }), variant: 'error' });
        return;
      }
      await refresh();
    } finally {
      setBusyId(null);
    }
  };

  const handleVerify = async (c: Connector) => {
    setBusyId(c.id);
    try {
      const res = await api.verifyConnector(c.id);
      await refresh();
      const data = res.data as { ok?: boolean; error?: string } | undefined;
      if (res.success && data?.ok) {
        toast.notify({ message: t('toast.verifySuccess', { name: c.name }), variant: 'success' });
      } else {
        toast.notify({ message: t('toast.verifyFail'), variant: 'error' });
      }
    } finally {
      setBusyId(null);
    }
  };

  // 도구 목록만 다시 불러온다(재인증 아님) — 원격 MCP tools/list 강제 재동기화 후 목록 갱신.
  const handleResyncTools = async (c: Connector) => {
    setBusyId(c.id);
    try {
      const res = await api.resyncConnectorTools(c.id);
      if (!res.success) {
        toast.notify({ message: res.code ? apiErrorMessage(res) : t('toast.resyncFail'), variant: 'error' });
        return;
      }
      await refresh();
    } finally {
      setBusyId(null);
    }
  };

  // 로컬 MCP 는 호출마다 spawn 이라 "중지"할 프로세스가 없다 — 다시 spawn 해 도구 카탈로그를
  // 새로고침하는 것만 실제 동작. 백엔드가 .mcp.json 의 서버 전체를 한 번에 다시 warm 한다.
  const handleRefreshLocal = async (c: Connector) => {
    setBusyId(c.id);
    try {
      const res = await api.refreshLocalConnectors();
      if (!res.success) {
        toast.notify({ message: res.code ? apiErrorMessage(res) : t('toast.refreshLocalFail'), variant: 'error' });
        return;
      }
      await loadLocal();
    } finally {
      setBusyId(null);
    }
  };

  // .mcp.json 에서 실제로 제거한다(파괴적 — 확인은 ConnectorDetailView 의 인라인 확인이 담당).
  const handleRemoveLocal = async (c: Connector) => {
    setBusyId(c.id);
    try {
      const res = await api.deleteLocalConnector(c.id.replace(/^local:/, ''));
      if (!res.success) {
        toast.notify({ message: res.code ? apiErrorMessage(res) : t('toast.removeLocalFail', { name: c.name }), variant: 'error' });
        return;
      }
      await loadLocal();
      setSel((prev) => (prev === c.id ? null : prev));
    } finally {
      setBusyId(null);
    }
  };

  // 대기 중 업데이트 적용 — 백엔드가 pending 카탈로그를 활성으로 승격.
  const handleApplyUpdate = async (c: Connector) => {
    setBusyId(c.id);
    try {
      const res = await api.applyConnectorToolsUpdate(c.id);
      if (!res.success) {
        toast.notify({ message: res.code ? apiErrorMessage(res) : t('toast.applyUpdateFail'), variant: 'error' });
        return;
      }
      await refresh();
    } finally {
      setBusyId(null);
    }
  };

  // 도구별 정책 저장(낙관적 — 로컬은 이미 갱신됨) → 백엔드가 dispatcher 강제에 사용.
  // 성공 시엔 토글 자체가 이미 결과를 보여주므로 토스트를 띄우지 않고, 실패(저장 안 됨 = UI와
  // 백엔드 상태가 어긋남)했을 때만 알린다.
  const handleSetToolPermission = (connectorId: string, toolName: string, perm: ScopePermission) => {
    void api
      .setConnectorToolPermission(connectorId, toolName, UI_TO_WIRE_PERM[perm])
      .then((res) => {
        if (!res.success) {
          toast.notify({ message: res.error ? apiErrorMessage(res) : t('toast.permSaveFail'), variant: 'error' });
        }
      })
      .catch(() => toast.notify({ message: t('toast.permSaveFail'), variant: 'error' }));
  };

  const isLocal = kind === 'local-mcp';
  const match = (c: Connector) =>
    filter === 'all' ||
    (filter === 'connected' && c.status === 'connected') ||
    (filter === 'needsConnection' && c.status === 'disconnected') ||
    (filter === 'hasUpdate' && !!c.update);
  const filterLabel = (f: Filter): string => ({
    all: t('filter.all'),
    connected: t('filter.connected'),
    needsConnection: t('filter.needsConnection'),
    hasUpdate: t('filter.hasUpdate'),
  }[f]);

  const kindList = connectors.filter((c) => connectorKind(c) === kind);
  const list = kindList.filter(match);
  const connectedCount = kindList.filter((c) => c.status === 'connected').length;
  const needsCount = kindList.length - connectedCount;
  const updateCount = kindList.filter((c) => !!c.update).length;

  const selectedInKind = sel && kindList.some((c) => c.id === sel);
  const effectiveSel = selectedInKind ? sel : list[0]?.id ?? kindList[0]?.id ?? null;

  // 연결 테스트 — 부작용 없음(.mcp.json 안 씀). 모달의 필수 게이트.
  const handleTestLocalMcp = async (payload: LocalMcpTransportPayload) => {
    const res = await api.testLocalConnector(payload);
    return res.success ? (res.data as { ok: boolean; error: string | null; tool_count: number; tools: string[] }) : null;
  };

  // 등록 확정 — 여기서부터 부작용(.mcp.json 에 실제로 씀 + warm).
  const handleRegisterLocalMcp = async (name: string, payload: LocalMcpTransportPayload): Promise<boolean> => {
    const res = await api.registerLocalConnector({ ...payload, name });
    if (!res.success) return false;
    await loadLocal();
    setKind('local-mcp');
    setSel(`local:${name}`);
    return true;
  };

  const handleAddFromDirectory = (entry: CatalogConnector) => {
    if (connectors.some((c) => c.id === entry.id)) return;
    const newConnector: Connector = {
      id: entry.id,
      name: entry.name,
      icon: entry.icon,
      category: entry.category,
      provider: entry.provider,
      kind: 'remote',
      status: 'disconnected',
      summary: entry.summary,
      scopes: [],
      usedBy: [],
      examples: entry.examples ?? [],
    };
    // 추가 후 바로 연결하도록 원격 탭에서 새 커넥터를 선택하고 모달을 닫는다.
    setConnectors((a) => [...a, newConnector]);
    setKind('remote');
    setSel(entry.id);
    setDirectoryOpen(false);
  };

  return (
    <div className="flex flex-1 min-w-0 flex-col overflow-hidden">
      {!online && <RuntimeConnectionNotice />}
      <div className="flex flex-1 min-h-0 min-w-0 bg-background overflow-hidden">
      {/* rail */}
      <div
        className="flex-shrink-0 bg-layer-01 border-r border-border-subtle-01 flex flex-col min-w-0 overflow-hidden"
        style={{ width: railPanel.width }}
      >
        {/* 구분: 원격 커넥터 / 로컬 MCP (개발자) */}
        <div className="px-4 pt-4">
          <div data-tour="connectors-kind-tabs" className="grid grid-cols-2 gap-0.5 p-0.5 bg-field-01 border border-border-subtle-01 rounded-lg">
            {([
              { id: 'remote' as const, label: t('kindTab.remote') },
              { id: 'local-mcp' as const, label: t('kindTab.localMcp') },
            ]).map((opt) => {
              const active = opt.id === kind;
              return (
                <button
                  key={opt.id}
                  onClick={() => setKind(opt.id)}
                  aria-pressed={active}
                  className={`h-6 px-2 text-caption-01 font-medium rounded-md transition-colors duration-150 ${
                    active
                      ? 'bg-interactive text-text-on-color'
                      : 'bg-transparent text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary'
                  }`}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
        </div>

        <div className="p-4">
          <div className="domain-summary domain-summary--connector rounded-xl p-4">
            <div className="text-caption-01 text-text-helper mb-1">
              {isLocal ? t('summary.runningMcpServers') : t('summary.connectedConnectors')}
            </div>
            <div className="text-heading-03 font-semibold text-text-primary leading-none tabular-nums">
              {connectedCount}
              {t('summary.unit') && <span className="text-body-short-01 font-normal text-text-helper ml-1">{t('summary.unit')}</span>}
            </div>
            {(needsCount > 0 || updateCount > 0) && (
              <div className="text-caption-01 text-text-secondary mt-1.5 flex items-center gap-1.5">
                {needsCount > 0 && <span>{needsCount} {isLocal ? t('summary.stopped') : t('summary.needsConnection')}</span>}
                {needsCount > 0 && updateCount > 0 && <span className="text-text-helper">·</span>}
                {updateCount > 0 && <span>{updateCount} {t('summary.updates')}</span>}
              </div>
            )}
          </div>
        </div>

        <div className="text-caption-01 text-text-helper px-4 pt-1 pb-1.5 tracking-wider">{t('filter.label')}</div>
        <div className="flex-1 overflow-y-auto">
          {FILTERS.map((c) => {
            const isActive = c === filter;
            const badge =
              c === 'needsConnection' && needsCount > 0
                ? needsCount
                : c === 'hasUpdate' && updateCount > 0
                ? updateCount
                : null;
            return (
              <button
                key={c}
                onClick={() => setFilter(c)}
                className={`flex items-center w-full text-left px-4 py-2.5 text-body-short-01 border-l-2 transition-colors ${
                  isActive
                    ? 'bg-layer-selected-01 border-l-teal-40 text-text-primary font-semibold'
                    : 'bg-transparent border-l-transparent text-text-secondary hover:bg-layer-hover-01'
                }`}
              >
                {filterLabel(c)}
                {badge && (
                  <span className="ml-auto inline-flex items-center justify-center min-w-[18px] h-[18px] px-1.5 rounded-full bg-teal-60/20 text-teal-30 text-label-micro">
                    {badge}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        <div className="border-t border-border-subtle-01 px-4 py-3 flex items-start gap-2">
          <Icon name={isLocal ? 'warning' : 'idea'} size={15} className="text-icon-secondary mt-0.5 flex-shrink-0" />
          <span className="text-caption-01 text-text-helper leading-4">
            {isLocal ? t('rail.tipLocal') : t('rail.tipRemote')}
          </span>
        </div>
      </div>

      {/* Rail resize handle */}
      <ResizeHandle
        onPointerDown={railPanel.startResize}
        onDoubleClick={railPanel.resetWidth}
        isResizing={railPanel.isResizing}
        value={railPanel.width} min={railPanel.min} max={railPanel.max} onResizeBy={railPanel.resizeBy}
        aria-label="Resize filters panel"
      />

      {/* connector list */}
      <div
        className="flex-shrink-0 bg-background border-r border-border-subtle-01 overflow-y-auto min-w-0"
        style={{ width: listPanel.width }}
      >
        <div
          data-tour="connectors-list-header"
          className="px-5 pt-5 pb-3 flex flex-wrap items-center gap-x-2 gap-y-2"
        >
          <div className="flex items-center gap-2 min-w-0 whitespace-nowrap">
            <h1 className="text-heading-03 font-semibold text-text-primary whitespace-nowrap">
              {isLocal ? t('localMcp') : t('title')}
            </h1>
            {isLocal && (
              <Tag type="gray" size="sm" className="flex-shrink-0">
                {t('developerTag')}
              </Tag>
            )}
          </div>
          <div className="ml-auto flex-shrink-0">
            {isLocal ? (
              <button
                onClick={() => setMcpModalOpen(true)}
                className="inline-flex items-center gap-1 whitespace-nowrap text-caption-01 text-link-primary hover:underline"
              >
                <Icon name="add" size={13} /> {t('list.addServer')}
              </button>
            ) : (
              <AddConnectorMenu onBrowse={() => setDirectoryOpen(true)} onCustom={() => setCustomModalOpen(true)} />
            )}
          </div>
        </div>
        {list.map((c) => {
          const on = c.id === effectiveSel;
          const connected = c.status === 'connected';
          // errored 를 "연결 필요"로 뭉뚱그리지 않는다 — 이미 로그인된 계정에 재연결을
          // 시키는 잘못된 안내가 된다. 복구 동작(errorKind)별 라벨로 나눈다.
          const statusLabel = connected
            ? isLocal
              ? t('list.statusRunning')
              : t('list.statusConnected')
            : c.errored
            ? t(
                c.errorKind === 'approval'
                  ? 'list.statusAwaitingApproval'
                  : c.errorKind === 'reconnect'
                  ? 'list.statusReconnectNeeded'
                  : 'list.statusCheckNeeded',
              )
            : isLocal
            ? t('list.statusStopped')
            : t('list.statusNeedsConnection');
          return (
            <button
              key={c.id}
              onClick={() => setSel(c.id)}
              className={`flex items-start gap-2.5 w-full text-left border-b border-border-subtle-01 border-l-2 px-[18px] py-3 transition-colors ${
                on ? 'bg-layer-selected-01 border-l-teal-40' : 'bg-transparent border-l-transparent hover:bg-layer-hover-01'
              } ${connected ? '' : 'opacity-70'}`}
            >
              <div className={`w-7 h-7 flex-shrink-0 flex items-center justify-center rounded border mt-0.5 ${
                connected
                  ? 'border-teal-40/25 bg-teal-60/14 text-teal-30'
                  : 'border-border-subtle-02 bg-layer-03 text-text-helper'
              }`}>
                <Icon name={c.icon} size={16} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="text-body-short-01 font-semibold text-text-primary truncate">{c.name}</span>
                  {c.update && (
                    <Tag type="blue" size="sm">
                      {t('list.updateBadge')}
                    </Tag>
                  )}
                </div>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <StatusDot connected={connected} attention={!!c.errored} />
                  <span className="text-caption-01 text-text-helper truncate">
                    {statusLabel} · {c.category}
                  </span>
                </div>
              </div>
            </button>
          );
        })}
        {list.length === 0 && (
          <div className="p-10 text-center text-body-short-01 text-text-helper">
            {isLocal ? (
              <>
                {t('list.noLocalEmpty')}
                <button onClick={() => setMcpModalOpen(true)} className="block mx-auto mt-2 text-link-primary hover:underline">
                  {t('list.registerServer')}
                </button>
              </>
            ) : (
              <>
                {t('list.noRemoteEmpty')}
                <button onClick={() => setDirectoryOpen(true)} className="block mx-auto mt-2 text-link-primary hover:underline">
                  {t('list.addConnector')}
                </button>
              </>
            )}
          </div>
        )}
      </div>

      {/* List resize handle */}
      <ResizeHandle
        onPointerDown={listPanel.startResize}
        onDoubleClick={listPanel.resetWidth}
        isResizing={listPanel.isResizing}
        value={listPanel.width} min={listPanel.min} max={listPanel.max} onResizeBy={listPanel.resizeBy}
        aria-label="Resize connectors list panel"
      />

      {/* detail */}
      <ConnectorDetailView
        connectorId={effectiveSel}
        connectors={connectors}
        setConnectors={setConnectors}
        online={online}
        busyId={busyId}
        onRealConnect={handleRealConnect}
        onRealDisconnect={handleRealDisconnect}
        onResyncTools={handleResyncTools}
        onVerify={handleVerify}
        onRefreshLocal={handleRefreshLocal}
        onRemoveLocal={handleRemoveLocal}
        onApplyUpdate={handleApplyUpdate}
        onSetToolPermission={handleSetToolPermission}
      />

      {/* 원격 커넥터 둘러보기 */}
      <ConnectorDirectoryModal
        open={directoryOpen}
        onClose={() => setDirectoryOpen(false)}
        existingIds={connectors.map((c) => c.id)}
        onAdd={handleAddFromDirectory}
      />

      {/* 커스텀 원격 커넥터 추가 (OAuth 자동 디스커버리) */}
      <CustomConnectorModal
        open={customModalOpen}
        onClose={() => setCustomModalOpen(false)}
        onDiscover={handleDiscoverCustom}
        onAdd={handleAddCustom}
      />

      {/* 로컬 MCP 등록 모달 */}
      <McpRegisterModal
        open={mcpModalOpen}
        onClose={() => setMcpModalOpen(false)}
        onTest={handleTestLocalMcp}
        onRegister={handleRegisterLocalMcp}
      />
      </div>
    </div>
  );
}

export default ConnectorsScreen;
