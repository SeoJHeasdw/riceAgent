import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Button, Icon, Tag, ResizeHandle } from '../components/ui';
import type { IconName } from '../components/ui/Icon';
import { McpRegisterModal, ConnectorDirectoryModal, type McpRegisterPayload } from '../components/carbon';
import { useResizable } from '../hooks/useResizable';
import {
  CONNECTORS,
  connectorKind,
  type CatalogConnector,
  type Connector,
  type ConnectorScope,
  type McpTransport,
  type ScopePermission,
} from '../lib/learningData';

type Kind = 'remote' | 'local-mcp';

/** stdio transport를 보기 좋은 한 줄 명령으로 렌더 */
const transportLine = (t: McpTransport): string =>
  t.type === 'stdio' ? [t.command, ...t.args].join(' ') : t.endpoint;

/**
 * ConnectorsScreen ("커넥터") — the home for linked external accounts / data
 * sources (Db2, Slack, Outlook, Jira, …). Master-detail: connect/disconnect,
 * see each connector's access scope, which skills/plugins use it, and example
 * actions. Also the destination for "접근 권한 관리" links elsewhere.
 */

const FILTERS = ['전체', '연결됨', '연결 필요', '업데이트 있음'] as const;
type Filter = (typeof FILTERS)[number];

const SectionLabel: React.FC<{ children: React.ReactNode; right?: React.ReactNode }> = ({ children, right }) => (
  <div className="flex items-center mb-2.5">
    <span className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">{children}</span>
    {right && <span className="ml-auto">{right}</span>}
  </div>
);

const StatusDot: React.FC<{ connected: boolean }> = ({ connected }) => (
  <span className={`w-2 h-2 rounded-full flex-shrink-0 ${connected ? 'bg-support-success' : 'bg-layer-03'}`} />
);

/** 도구를 읽기 전용 / 쓰기·실행 둘로 가른다 (그룹 헤더·기본 권한 결정용) */
const isReadOnly = (s: ConnectorScope) => s.mode === '읽기';
/** 도구 권한을 명시하지 않았으면 성격으로 추정: 읽기 → 항상 허용, 쓰기·실행 → 승인 필요 */
const permOf = (s: ConnectorScope): ScopePermission => s.permission ?? (isReadOnly(s) ? '항상 허용' : '승인 필요');

const PERMS: { id: ScopePermission; label: string; icon: IconName; active: string }[] = [
  { id: '항상 허용', label: '항상', icon: 'check', active: 'bg-support-success/15 text-support-success' },
  { id: '승인 필요', label: '승인', icon: 'user', active: 'bg-[#f1c21b]/15 text-[#d2a106]' },
  { id: '차단', label: '차단', icon: 'view-off', active: 'bg-[#da1e28]/12 text-[#da1e28]' },
];

/**
 * PermissionControl — 한 도구를 에이전트가 언제 호출할 수 있는지 정하는 3단 토글.
 * 도구를 "추가"하는 게 아니라 제공된 도구의 사용 시점(항상 허용/승인 필요/차단)을 고른다.
 */
const PermissionControl: React.FC<{
  value: ScopePermission;
  disabled?: boolean;
  onChange: (p: ScopePermission) => void;
}> = ({ value, disabled, onChange }) => (
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
          <span>{p.label}</span>
        </button>
      );
    })}
  </div>
);

/**
 * GroupPermissionMenu — 그룹(읽기/쓰기) 전체 도구의 권한을 한 번에 설정하는 드롭다운.
 * 도구가 많은 커넥터에서 일일이 토글하지 않도록 벤치마크의 그룹 헤더 컨트롤을 따른다.
 */
const GroupPermissionMenu: React.FC<{ disabled?: boolean; onPick: (p: ScopePermission) => void }> = ({
  disabled,
  onPick,
}) => {
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
        모두 설정 <Icon name={open ? 'chevron-up' : 'chevron-down'} size={11} />
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-10 min-w-[120px] py-1 rounded-lg bg-layer-02 border border-border-subtle-01 shadow-overlay">
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
              <span>{p.id}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

const ConnectorDetailView: React.FC<{
  connectorId: string | null;
  connectors: Connector[];
  setConnectors: React.Dispatch<React.SetStateAction<Connector[]>>;
}> = ({ connectorId, connectors, setConnectors }) => {
  const c = connectors.find((x) => x.id === connectorId);

  if (!c || !connectorId) {
    return <div className="flex-1 flex items-center justify-center text-body-short-02 text-text-helper">커넥터를 선택하세요.</div>;
  }

  const connected = c.status === 'connected';
  const isMcp = connectorKind(c) === 'local-mcp';
  const patch = (fn: (c: Connector) => Connector) => setConnectors((a) => a.map((x) => (x.id === connectorId ? fn(x) : x)));
  const toggleConnect = () =>
    patch((x) =>
      x.status === 'connected'
        ? { ...x, status: 'disconnected', account: undefined, lastSync: undefined }
        : {
            ...x,
            status: 'connected',
            account: x.transport ? transportLine(x.transport) : `${x.id}@example.com`,
            lastSync: '방금',
          },
    );
  // 도구는 제공자가 정의하므로 추가/삭제하지 않고 "언제 호출할 수 있는가"만 바꾼다.
  const setScopePermission = (idx: number, perm: ScopePermission) =>
    patch((x) => ({ ...x, scopes: x.scopes.map((s, i) => (i === idx ? { ...s, permission: perm } : s)) }));
  // 그룹(읽기/쓰기) 전체를 한 번에 설정 — 도구가 많은 커넥터(Gmail 등)에서 필수
  const setGroupPermission = (predicate: (s: ConnectorScope) => boolean, perm: ScopePermission) =>
    patch((x) => ({ ...x, scopes: x.scopes.map((s) => (predicate(s) ? { ...s, permission: perm } : s)) }));
  // 업데이트 적용 — 제공사/서버가 새로 노출한 도구를 목록에 합치고 배너를 정리
  const applyUpdate = () =>
    c.update &&
    patch((x) => ({
      ...x,
      scopes: [...x.scopes, ...(x.update?.addsTools ?? [])],
      lastSync: '방금',
      update: undefined,
    }));

  const meta: [string, string][] = isMcp
    ? [
        ['전송 방식', c.transport?.type === 'http' ? 'HTTP (로컬)' : 'stdio (로컬 실행)'],
        ['분류', c.category],
        ['실행/엔드포인트', c.transport ? transportLine(c.transport) : '—'],
        ['마지막 확인', c.lastSync ?? '—'],
      ]
    : [
        ['제공자', c.provider],
        ['분류', c.category],
        ['계정', c.account ?? '—'],
        ['마지막 동기화', c.lastSync ?? '—'],
      ];

  return (
    <div className="flex-1 min-w-0 overflow-y-auto">
      {/* header */}
      <div className="px-7 pt-6 pb-5 border-b border-border-subtle-01">
        <div className="flex items-start gap-3.5">
          <div className={`w-11 h-11 flex-shrink-0 flex items-center justify-center rounded-lg ${connected ? 'bg-blue-60' : 'bg-layer-03'}`}>
            <Icon name={c.icon} size={22} className="text-white" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-heading-03 font-semibold text-text-primary">{c.name}</h2>
              <Tag type={connected ? 'green' : 'gray'} size="sm">
                {connected ? '연결됨' : '연결 필요'}
              </Tag>
            </div>
            <p className="mt-1.5 text-body-short-01 leading-5 text-text-secondary">{c.summary}</p>
          </div>
          <div className="flex-shrink-0">
            <Button
              kind={connected ? 'secondary' : 'primary'}
              size="sm"
              icon={<Icon name={connected ? 'close' : 'connect'} size={15} />}
              onClick={toggleConnect}
            >
              {isMcp ? (connected ? '중지' : '실행') : connected ? '연결 해제' : '연결'}
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
                <span className="font-semibold">{isMcp ? '새 서버 버전' : '새 버전'}</span>
                <span className="font-mono font-semibold text-link-primary">{c.update.version}</span>
                <span className="text-text-secondary">사용 가능</span>
              </div>
              <div className="text-caption-01 leading-4 text-text-helper truncate mt-0.5">{c.update.note}</div>
            </div>
            <Button kind="primary" size="sm" icon={<Icon name="renew" size={14} />} onClick={applyUpdate} className="flex-shrink-0">
              업데이트
            </Button>
          </div>
        )}
      </div>

      <div className="px-7 pt-5 pb-10 flex flex-col gap-7">
        {/* local-mcp: 실행 신뢰 경고 */}
        {isMcp && (
          <div className="flex items-start gap-2.5 px-3.5 py-3 rounded-lg bg-[#da1e28]/10 border border-[#da1e28]/30">
            <Icon name="warning" size={16} filled className="text-[#ff8389] flex-shrink-0 mt-0.5" />
            <p className="text-caption-01 text-text-helper leading-relaxed">
              이 서버는 <span className="text-[#ff8389] font-medium">내 컴퓨터에서 직접 코드를 실행</span>합니다.
              신뢰할 수 있는 출처의 명령만 실행하세요.
            </p>
          </div>
        )}

        {/* metadata */}
        <div>
          <SectionLabel
            right={
              connected ? (
                <button className="inline-flex items-center gap-1 text-caption-01 text-link-primary hover:underline">
                  {isMcp ? '재시작' : '재인증'} <Icon name="renew" size={12} />
                </button>
              ) : undefined
            }
          >
            정보
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
        <div>
          <SectionLabel>도구 권한 · {c.scopes.length}</SectionLabel>
          <p className="text-caption-01 text-text-helper -mt-1 mb-3 leading-relaxed">
            {isMcp ? '이 서버가 노출하는 도구예요.' : `${c.provider}가 제공하는 도구예요.`} 에이전트가 각 도구를 언제
            호출할 수 있는지 정하세요 — <span className="text-support-success font-medium">항상</span> 허용 ·{' '}
            <span className="text-[#d2a106] font-medium">승인</span> 후 호출 ·{' '}
            <span className="text-[#da1e28] font-medium">차단</span>.
          </p>
          {c.scopes.length === 0 ? (
            <div className="text-caption-01 text-text-helper bg-layer-01 border border-border-subtle-01 rounded-lg px-3.5 py-3.5">
              {connected
                ? isMcp
                  ? '이 서버가 노출한 도구가 없어요.'
                  : '이 커넥터가 제공하는 도구가 없어요.'
                : isMcp
                ? '실행하면 노출 도구를 확인할 수 있어요.'
                : '연결하면 제공되는 도구가 표시돼요.'}
            </div>
          ) : (
            <div className="flex flex-col gap-4">
              {([
                { key: 'read', label: '읽기 전용 도구', filter: (s: ConnectorScope) => isReadOnly(s) },
                { key: 'write', label: isMcp ? '실행 도구' : '쓰기·삭제 도구', filter: (s: ConnectorScope) => !isReadOnly(s) },
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
                        <GroupPermissionMenu disabled={!connected} onPick={(p) => setGroupPermission(g.filter, p)} />
                      </div>
                    </div>
                    <div className="flex flex-col gap-px bg-border-subtle-01 rounded-lg overflow-hidden">
                      {g.items.map(({ s, idx }) => {
                        const blocked = permOf(s) === '차단';
                        return (
                          <div key={idx} className="flex items-center gap-2.5 bg-background px-3.5 py-2.5">
                            <span
                              title={s.name}
                              className={`flex-1 min-w-0 text-body-short-01 truncate ${isMcp ? 'font-mono' : ''} ${
                                blocked ? 'text-text-helper line-through opacity-60' : 'text-text-primary'
                              }`}
                            >
                              {s.name}
                            </span>
                            <PermissionControl
                              value={permOf(s)}
                              disabled={!connected}
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
          <SectionLabel>이 커넥터를 사용하는 항목 · {c.usedBy.length}</SectionLabel>
          <p className="text-caption-01 text-text-helper -mt-1 mb-2.5 leading-relaxed">
            이 커넥터의 도구를 호출하는 스킬 · 플러그인 · 자동화예요.
          </p>
          {c.usedBy.length === 0 ? (
            <div className="text-caption-01 text-text-helper bg-layer-01 border border-border-subtle-01 rounded-lg px-3.5 py-3.5">
              아직 이 커넥터의 도구를 쓰는 스킬·플러그인이 없어요.
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
  const { id } = useParams<{ id?: string }>();
  const [connectors, setConnectors] = useState<Connector[]>(CONNECTORS);
  const [filter, setFilter] = useState<Filter>('전체');
  const initial = id ? CONNECTORS.find((c) => c.id === id) : undefined;
  const [kind, setKind] = useState<Kind>(initial ? connectorKind(initial) : 'remote');
  const [mcpModalOpen, setMcpModalOpen] = useState(false);
  const [directoryOpen, setDirectoryOpen] = useState(false);
  const [sel, setSel] = useState<string | null>((initial?.id ?? CONNECTORS[0]?.id) ?? null);

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

  const isLocal = kind === 'local-mcp';
  const match = (c: Connector) =>
    filter === '전체' ||
    (filter === '연결됨' && c.status === 'connected') ||
    (filter === '연결 필요' && c.status === 'disconnected') ||
    (filter === '업데이트 있음' && !!c.update);

  const kindList = connectors.filter((c) => connectorKind(c) === kind);
  const list = kindList.filter(match);
  const connectedCount = kindList.filter((c) => c.status === 'connected').length;
  const needsCount = kindList.length - connectedCount;
  const updateCount = kindList.filter((c) => !!c.update).length;

  const selectedInKind = sel && kindList.some((c) => c.id === sel);
  const effectiveSel = selectedInKind ? sel : list[0]?.id ?? kindList[0]?.id ?? null;

  const handleRegisterMcp = (p: McpRegisterPayload) => {
    const id = `mcp-${Date.now()}`;
    const newConnector: Connector = {
      id,
      name: p.name,
      icon: p.transport.type === 'http' ? 'cloud' : 'server',
      category: p.transport.type === 'http' ? 'HTTP MCP' : '로컬 MCP',
      provider: p.transport.type === 'http' ? '로컬 MCP · http' : '로컬 MCP · stdio',
      kind: 'local-mcp',
      status: 'connected',
      account: transportLine(p.transport),
      lastSync: '방금',
      summary: `방금 등록한 로컬 MCP 서버예요. 연결 테스트에서 도구 ${p.toolCount}개를 발견했습니다.`,
      transport: p.transport,
      scopes: Array.from({ length: p.toolCount }, (_, i) => ({ name: `tool_${i + 1}`, mode: '실행' })),
      usedBy: [],
      examples: ['이 서버의 도구로 무엇을 할 수 있는지 알려줘'],
    };
    setConnectors((a) => [...a, newConnector]);
    setKind('local-mcp');
    setSel(id);
    setMcpModalOpen(false);
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
    // 추가 후 바로 연결하도록 원격 탭에서 새 커넥터를 선택 (모달은 다중 추가 위해 유지)
    setConnectors((a) => [...a, newConnector]);
    setKind('remote');
    setSel(entry.id);
  };

  return (
    <div className="flex flex-1 min-w-0 bg-background overflow-hidden">
      {/* rail */}
      <div
        className="flex-shrink-0 bg-layer-01 border-r border-border-subtle-01 flex flex-col min-w-0 overflow-hidden"
        style={{ width: railPanel.width }}
      >
        {/* 구분: 원격 커넥터 / 로컬 MCP (개발자) */}
        <div className="px-4 pt-4">
          <div className="grid grid-cols-2 gap-1 p-1 bg-field-01 border border-border-subtle-01 rounded-xl">
            {([
              { id: 'remote' as const, label: '원격' },
              { id: 'local-mcp' as const, label: '로컬 MCP' },
            ]).map((opt) => {
              const active = opt.id === kind;
              return (
                <button
                  key={opt.id}
                  onClick={() => setKind(opt.id)}
                  aria-pressed={active}
                  className={`py-1.5 text-caption-01 font-semibold rounded-lg transition-all duration-150 ${
                    active
                      ? 'bg-interactive text-white shadow-elevation-1'
                      : 'bg-transparent text-text-secondary hover:bg-layer-hover-01'
                  }`}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
        </div>

        <div className="p-4">
          <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-[#0f62fe] via-[#3023ae] to-[#8a3ffc] p-4 text-white shadow-elevation-2">
            <div className="absolute -top-8 -right-8 w-28 h-28 rounded-full bg-white/10 blur-2xl" aria-hidden="true" />
            <div className="relative">
              <div className="text-caption-01 text-white/85 mb-1">
                {isLocal ? '실행 중인 MCP 서버' : '연결된 커넥터'}
              </div>
              <div className="text-[28px] font-light leading-none">
                {connectedCount}
                <span className="text-body-short-01 ml-1">개</span>
              </div>
              {(needsCount > 0 || updateCount > 0) && (
                <div className="text-caption-01 text-white/85 mt-1.5 flex items-center gap-1.5">
                  {needsCount > 0 && <span>{needsCount} {isLocal ? '중지됨' : '연결 필요'}</span>}
                  {needsCount > 0 && updateCount > 0 && <span className="text-white/40">·</span>}
                  {updateCount > 0 && <span>{updateCount} 업데이트</span>}
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="text-caption-01 text-text-helper px-4 pt-1 pb-1.5 tracking-wider">필터</div>
        <div className="flex-1 overflow-y-auto">
          {FILTERS.map((c) => {
            const isActive = c === filter;
            const badge =
              c === '연결 필요' && needsCount > 0
                ? needsCount
                : c === '업데이트 있음' && updateCount > 0
                ? updateCount
                : null;
            return (
              <button
                key={c}
                onClick={() => setFilter(c)}
                className={`flex items-center w-full text-left px-4 py-2.5 text-body-short-01 border-l-2 transition-colors ${
                  isActive
                    ? 'bg-layer-selected-01 border-l-blue-50 text-text-primary font-semibold'
                    : 'bg-transparent border-l-transparent text-text-secondary hover:bg-layer-hover-01'
                }`}
              >
                {c}
                {badge && (
                  <span className="ml-auto inline-flex items-center justify-center min-w-[18px] h-[18px] px-1.5 rounded-full bg-blue-60 text-white text-[11px]">
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
            {isLocal
              ? '로컬 MCP는 내 컴퓨터에서 직접 실행됩니다. 신뢰할 수 있는 서버만 등록하세요.'
              : '커넥터는 에이전트가 외부 계정·데이터에 접근하는 통로예요. 필요한 범위만 허용하세요.'}
          </span>
        </div>
      </div>

      {/* Rail resize handle */}
      <ResizeHandle
        onPointerDown={railPanel.startResize}
        onDoubleClick={railPanel.resetWidth}
        isResizing={railPanel.isResizing}
        aria-label="Resize filters panel"
      />

      {/* connector list */}
      <div
        className="flex-shrink-0 bg-background border-r border-border-subtle-01 overflow-y-auto min-w-0"
        style={{ width: listPanel.width }}
      >
        <div className="px-5 pt-5 pb-3 flex items-center gap-2">
          <h1 className="text-heading-03 font-light text-text-primary">{isLocal ? '로컬 MCP' : '커넥터'}</h1>
          {isLocal && (
            <Tag type="gray" size="sm">
              개발자
            </Tag>
          )}
          <div className="flex-1" />
          <button
            onClick={() => (isLocal ? setMcpModalOpen(true) : setDirectoryOpen(true))}
            className="inline-flex items-center gap-1 text-caption-01 text-link-primary hover:underline"
          >
            <Icon name="add" size={13} /> {isLocal ? '서버 등록' : '추가'}
          </button>
        </div>
        {list.map((c) => {
          const on = c.id === effectiveSel;
          const connected = c.status === 'connected';
          return (
            <button
              key={c.id}
              onClick={() => setSel(c.id)}
              className={`flex items-start gap-2.5 w-full text-left border-b border-border-subtle-01 border-l-2 px-[18px] py-3 transition-colors ${
                on ? 'bg-layer-selected-01 border-l-blue-50' : 'bg-transparent border-l-transparent hover:bg-layer-hover-01'
              } ${connected ? '' : 'opacity-70'}`}
            >
              <div className={`w-7 h-7 flex-shrink-0 flex items-center justify-center rounded mt-0.5 ${connected ? 'bg-blue-60' : 'bg-layer-03'}`}>
                <Icon name={c.icon} size={16} className="text-white" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="text-body-short-01 font-semibold text-text-primary truncate">{c.name}</span>
                  {c.update && (
                    <Tag type="blue" size="sm">
                      업데이트
                    </Tag>
                  )}
                </div>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <StatusDot connected={connected} />
                  <span className="text-caption-01 text-text-helper truncate">
                    {connected ? (isLocal ? '실행 중' : '연결됨') : isLocal ? '중지됨' : '연결 필요'} · {c.category}
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
                등록된 로컬 MCP 서버가 없어요.
                <button onClick={() => setMcpModalOpen(true)} className="block mx-auto mt-2 text-link-primary hover:underline">
                  서버 등록
                </button>
              </>
            ) : (
              <>
                해당하는 커넥터가 없어요.
                <button onClick={() => setDirectoryOpen(true)} className="block mx-auto mt-2 text-link-primary hover:underline">
                  커넥터 추가
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
        aria-label="Resize connectors list panel"
      />

      {/* detail */}
      <ConnectorDetailView connectorId={effectiveSel} connectors={connectors} setConnectors={setConnectors} />

      {/* 원격 커넥터 디렉터리 */}
      <ConnectorDirectoryModal
        open={directoryOpen}
        onClose={() => setDirectoryOpen(false)}
        existingIds={connectors.map((c) => c.id)}
        onAdd={handleAddFromDirectory}
      />

      {/* 로컬 MCP 등록 모달 */}
      <McpRegisterModal open={mcpModalOpen} onClose={() => setMcpModalOpen(false)} onRegister={handleRegisterMcp} />
    </div>
  );
}

export default ConnectorsScreen;

// Made with Bob
