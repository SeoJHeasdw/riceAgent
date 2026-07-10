import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Icon, Tag, ResizeHandle, Toggle } from '../components/ui';
import { ConnectorRequiredModal } from '../components/carbon/ConnectorRequiredModal';
import { useResizable } from '../hooks/useResizable';
import { useLearning } from '../lib/learning/LearningContext';
import { api } from '../lib/api';
import { connectorIdForScope, type MyPlugin } from '../lib/learningData';

/**
 * MyPluginsScreen ("내 플러그인") — management screen for installed plugins.
 * Master-detail: enable/disable a plugin, see its source/version/author,
 * the skills it provides (invokable via "/" or auto-used), and example
 * prompts to try. Mirrors the "내 스킬" screen.
 */

const FILTERS = ['전체', '활성', '업데이트 있음', '비활성'] as const;
type Filter = (typeof FILTERS)[number];

const hasUpdate = (p: MyPlugin) => !!p.update;

const SectionLabel: React.FC<{ children: React.ReactNode; right?: React.ReactNode }> = ({ children, right }) => (
  <div className="flex items-center mb-2.5">
    <span className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">{children}</span>
    {right && <span className="ml-auto">{right}</span>}
  </div>
);

type PluginTab = 'skills' | 'knowledge' | 'permissions';

const PluginDetailView: React.FC<{ pluginId: string | null }> = ({ pluginId }) => {
  const navigate = useNavigate();
  const { myPlugins, setMyPlugins } = useLearning();
  const [tab, setTab] = useState<PluginTab>('skills');
  const [connectorDialogOpen, setConnectorDialogOpen] = useState(false);
  const plugin = myPlugins.find((p) => p.id === pluginId);

  if (!plugin || !pluginId) {
    return <div className="flex-1 flex items-center justify-center text-body-short-02 text-text-helper">플러그인을 선택하세요.</div>;
  }

  const patch = (fn: (p: MyPlugin) => MyPlugin) => setMyPlugins((a) => a.map((x) => (x.id === pluginId ? fn(x) : x)));
  const permissionsNeedingConnection = plugin.permissions.filter((p) => p.mode.includes('연결 필요'));
  const missingConnectorNames = permissionsNeedingConnection.map((p) => p.name);
  const firstConnectorTarget = (() => {
    const first = permissionsNeedingConnection[0]?.name ?? plugin.permissions[0]?.name;
    const cid = first ? connectorIdForScope(first) : null;
    return cid ? `/connectors/${cid}` : '/connectors';
  })();
  // 토글·삭제는 로컬 상태 + 런타임에도 반영해 재시작 후에도 일관되게 유지한다.
  // (런타임 오프라인이거나 mock/낙관적 항목이면 api.isEnabled 가드로 no-op.)
  const toggle = () => {
    const next = !plugin.enabled;
    if (next && permissionsNeedingConnection.length > 0) {
      setConnectorDialogOpen(true);
      return;
    }
    patch((x) => ({ ...x, enabled: next }));
    if (api.isEnabled) {
      void api.setPluginEnabled(pluginId, next).then((res) => {
        if (!res.success) {
          patch((x) => ({ ...x, enabled: !next }));
          setConnectorDialogOpen(true);
          console.error('plugin toggle failed', res.error);
        }
      });
    }
  };
  const remove = () => {
    setMyPlugins((a) => a.filter((x) => x.id !== pluginId));
    if (api.isEnabled) void api.removeArtifact(pluginId);
  };
  const update = () =>
    plugin.update && patch((x) => ({ ...x, update: undefined, version: plugin.update!.version, updated: '방금' }));

  const meta: [string, string][] = [
    ['소스', plugin.source],
    ['버전', plugin.version],
    ['작성자', plugin.author],
    ['마지막 업데이트', plugin.updated],
  ];

  return (
    <>
    <div className="flex-1 min-w-0 overflow-y-auto">
      {/* header */}
      <div className="px-7 pt-6 pb-5 border-b border-border-subtle-01">
        <div className="flex items-start gap-3.5">
          <div className={`w-11 h-11 flex-shrink-0 flex items-center justify-center rounded-lg border ${
            plugin.enabled
              ? 'bg-purple-60/12 border-purple-60/25 text-purple-40'
              : 'bg-layer-03 border-border-subtle-02 text-text-helper'
          }`}>
            <Icon name={plugin.icon} size={22} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-heading-03 font-semibold text-text-primary">{plugin.name}</h2>
              <Tag type="purple" size="sm">
                플러그인
              </Tag>
              {!plugin.enabled && (
                <Tag type="gray" size="sm">
                  비활성
                </Tag>
              )}
            </div>
            <p className="mt-1.5 text-body-short-01 leading-5 text-text-secondary">{plugin.summary}</p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <span className="text-caption-01 text-text-helper">{plugin.enabled ? '활성' : '비활성'}</span>
            <Toggle
              checked={plugin.enabled}
              onChange={toggle}
              tone="purple"
              aria-label={`${plugin.name} ${plugin.enabled ? '비활성화' : '활성화'}`}
            />
          </div>
        </div>

        {/* update available — sits right under the plugin description */}
        {plugin.update && (
          <div className="mt-4 flex items-center gap-3 rounded-xl border border-blue-50/30 bg-gradient-to-r from-blue-60/12 to-blue-60/[0.04] pl-3 pr-3 py-2.5">
            <div className="w-8 h-8 flex-shrink-0 flex items-center justify-center rounded-lg bg-blue-60/15">
              <Icon name="renew" size={16} className="text-link-primary" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1.5 text-body-short-01 leading-5 text-text-primary">
                <span className="font-semibold">새 버전</span>
                <span className="font-mono font-semibold text-link-primary">{plugin.update.version}</span>
                <span className="text-text-secondary">사용 가능</span>
              </div>
              <div className="text-caption-01 leading-4 text-text-helper truncate mt-0.5">{plugin.update.note}</div>
            </div>
            <Button kind="primary" size="sm" icon={<Icon name="renew" size={14} />} onClick={update} className="flex-shrink-0">
              업데이트
            </Button>
          </div>
        )}
      </div>

      <div className="px-7 pt-5 pb-10 flex flex-col gap-7">
        {/* metadata */}
        <div>
          <SectionLabel>정보</SectionLabel>
          <div className="grid grid-cols-2 gap-x-6 gap-y-3.5">
            {meta.map(([k, v]) => (
              <div key={k}>
                <div className="text-caption-01 text-text-helper">{k}</div>
                <div className="text-body-short-01 text-text-primary mt-0.5">{v}</div>
              </div>
            ))}
          </div>
        </div>

        {/* skills · knowledge · permissions — tabbed */}
        <div>
          <div className="flex items-center border-b border-border-subtle-01 mb-3.5">
            {([
              ['skills', `스킬 · ${plugin.skills.length}`],
              ['knowledge', `지식 · ${plugin.knowledge.length}`],
              ['permissions', `권한 · ${plugin.permissions.length}`],
            ] as const).map(([id, label]) => {
              const on = tab === id;
              return (
                <button
                  key={id}
                  onClick={() => setTab(id)}
                  className={`relative px-3.5 py-2.5 text-body-short-01 transition-colors ${
                    on ? 'text-text-primary font-semibold' : 'text-text-secondary hover:text-text-primary'
                  }`}
                >
                  {label}
                  {on && <span className="absolute bottom-0 left-2 right-2 h-0.5 rounded-full bg-purple-60" />}
                </button>
              );
            })}
            <div className="flex-1" />
            {tab === 'skills' && (
              <button
                onClick={() => navigate('/my-skills')}
                className="inline-flex items-center gap-1 text-caption-01 text-link-primary hover:underline"
              >
                모두 보기 <Icon name="arrow-up-right" size={12} />
              </button>
            )}
            {tab === 'permissions' && permissionsNeedingConnection.length > 0 && (
              <Button kind="tertiary" size="sm" icon={<Icon name="arrow-up-right" size={14} />} onClick={() => navigate(firstConnectorTarget)}>
                커넥터 연결
              </Button>
            )}
          </div>

          {tab === 'skills' && (
            <>
              <p className="text-caption-01 text-text-helper mb-2.5">
                채팅에서 <span className="font-mono text-text-secondary">/</span> 를 입력해 호출하거나, RICE가 관련 작업에 자동으로 사용합니다.
              </p>
              <div className="flex flex-col gap-px bg-border-subtle-01 rounded-lg overflow-hidden">
                {plugin.skills.map((s, i) => (
                  <div key={i} className="flex items-start gap-2.5 bg-background px-3.5 py-2.5">
                    <Icon name="skill" size={16} className="text-icon-secondary mt-0.5 flex-shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="text-body-short-01 font-semibold text-text-primary">{s.name}</span>
                        <span className="text-label-micro font-mono text-text-helper border border-border-subtle-01 rounded px-1 py-px">
                          /{s.name.replace(/\s+/g, '')}
                        </span>
                      </div>
                      {s.desc && <div className="text-caption-01 text-text-helper mt-0.5">{s.desc}</div>}
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}

          {tab === 'knowledge' && (
            <>
              <p className="text-caption-01 text-text-helper mb-2.5">
                플러그인이 답변에 활용하는 지식 소스예요. 제공사가 갱신하면 자동으로 최신화됩니다.
              </p>
              <div className="flex flex-col gap-px bg-border-subtle-01 rounded-lg overflow-hidden">
                {plugin.knowledge.map((k, i) => (
                  <div key={i} className="flex items-start gap-2.5 bg-background px-3.5 py-2.5">
                    <Icon name="bookmark" size={16} className="text-icon-secondary mt-0.5 flex-shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="text-body-short-01 font-semibold text-text-primary">{k.name}</div>
                      <div className="text-caption-01 text-text-helper mt-0.5">{k.desc}</div>
                    </div>
                    <span className="text-caption-01 font-mono text-text-helper flex-shrink-0 mt-0.5">{k.meta}</span>
                  </div>
                ))}
              </div>
            </>
          )}

          {tab === 'permissions' && (
            <>
              <p className="text-caption-01 text-text-helper mb-2.5">
                이 플러그인이 접근할 수 있는 데이터·도구 범위예요. 항목을 누르면 해당 커넥터로 이동합니다.
              </p>
              {plugin.permissions.length === 0 ? (
                <div className="text-caption-01 text-text-helper bg-layer-01 border border-border-subtle-01 rounded-lg px-3.5 py-3.5">
                  추가 연결 없이 사용할 수 있는 플러그인이에요.
                </div>
              ) : (
                <div className="flex flex-col gap-px bg-border-subtle-01 rounded-lg overflow-hidden">
                  {plugin.permissions.map((p, i) => {
                    const cid = connectorIdForScope(p.name);
                    const needsConnection = p.mode.includes('연결 필요');
                    return (
                      <div key={i} className="flex items-center gap-2.5 bg-background px-3.5 py-2.5">
                        <button
                          type="button"
                          onClick={() => navigate(cid ? `/connectors/${cid}` : '/connectors')}
                          title={cid ? '커넥터에서 보기' : '커넥터 화면으로'}
                          className="group flex items-center gap-2.5 flex-1 min-w-0 text-left"
                        >
                          <Icon name={needsConnection ? 'connect' : 'lock'} size={15} className="text-purple-40 flex-shrink-0 opacity-80" />
                          <span className="flex-1 min-w-0 text-body-short-01 font-mono text-text-primary truncate group-hover:underline">
                            {p.name}
                          </span>
                          {cid && <Icon name="arrow-up-right" size={13} className="text-icon-secondary opacity-50 flex-shrink-0" />}
                        </button>
                        <Tag type={needsConnection ? 'yellow' : 'gray'} size="sm">
                          {p.mode}
                        </Tag>
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>

        {/* example prompts */}
        <div>
          <SectionLabel>다음과 같이 질문해 보세요</SectionLabel>
          {plugin.examples.length > 0 ? (
            <div className="flex flex-col gap-1.5">
              {plugin.examples.map((q, i) => (
              <button
                key={i}
                onClick={() => navigate('/chat')}
                disabled={!plugin.enabled}
                className={`group flex items-center gap-2.5 text-left bg-layer-01 border border-border-subtle-01 rounded-lg px-3 py-2.5 transition-colors ${
                  plugin.enabled ? 'hover:bg-layer-hover-01 hover:border-border-strong-01' : 'opacity-50 cursor-not-allowed'
                }`}
              >
                <Icon name="chat" size={15} className="text-icon-secondary flex-shrink-0" />
                <span className="flex-1 min-w-0 text-body-short-01 text-text-primary">{q}</span>
                <Icon name="arrow-up-right" size={13} className="text-icon-secondary opacity-0 group-hover:opacity-70 transition-opacity flex-shrink-0" />
              </button>
              ))}
            </div>
          ) : (
            <div className="text-caption-01 text-text-helper bg-layer-01 border border-border-subtle-01 rounded-lg px-3.5 py-3.5">
              추천 질문이 아직 제공되지 않았어요.
            </div>
          )}
        </div>

        {/* actions */}
        <div className="flex gap-2 border-t border-border-subtle-01 pt-4 items-center">
          <Button kind="secondary" size="sm" icon={<Icon name="arrow-up-right" size={15} />} onClick={() => navigate('/plugins')}>
            Plugin Market에서 보기
          </Button>
          <div className="flex-1" />
          <button
            onClick={remove}
            className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg border border-support-error/40 bg-transparent text-support-error text-body-short-01 font-semibold hover:bg-support-error/10 hover:border-support-error/70 transition-colors"
          >
            <Icon name="trash" size={15} />
            제거
          </button>
        </div>
      </div>
    </div>
    <ConnectorRequiredModal
      open={connectorDialogOpen}
      pluginName={plugin.name}
      connectors={missingConnectorNames}
      onClose={() => setConnectorDialogOpen(false)}
      onConnect={() => {
        setConnectorDialogOpen(false);
        navigate(firstConnectorTarget);
      }}
    />
    </>
  );
};

function MyPluginsScreen() {
  const navigate = useNavigate();
  const { myPlugins, hydrating } = useLearning();
  const [filter, setFilter] = useState<Filter>('전체');
  const [sel, setSel] = useState<string | null>(myPlugins[0]?.id ?? null);

  // Resizable left rail
  const railPanel = useResizable({
    initial: 282,
    min: 184,
    max: 360,
    storageKey: 'rice.panel.myplugins',
  });

  // Resizable plugin list
  const listPanel = useResizable({
    initial: 312,
    min: 240,
    max: 480,
    storageKey: 'rice.panel.myplugins-list',
  });

  const match = (p: MyPlugin) =>
    filter === '전체' ||
    (filter === '활성' && p.enabled) ||
    (filter === '업데이트 있음' && hasUpdate(p)) ||
    (filter === '비활성' && !p.enabled);

  const list = myPlugins.filter(match);
  const active = myPlugins.filter((p) => p.enabled).length;
  const updateCount = myPlugins.filter(hasUpdate).length;

  const selectedExists = sel && myPlugins.some((p) => p.id === sel);
  const effectiveSel = selectedExists ? sel : list[0]?.id ?? myPlugins[0]?.id ?? null;

  return (
    <div className="flex flex-1 min-w-0 bg-background overflow-hidden">
      {/* rail */}
      <div
        className="flex-shrink-0 bg-layer-01 border-r border-border-subtle-01 flex flex-col min-w-0 overflow-hidden"
        style={{ width: railPanel.width }}
      >
        <div className="p-4">
          <div className="domain-summary domain-summary--plugin rounded-xl p-4 text-white">
            <div className="text-caption-01 text-white/70 mb-1">설치된 플러그인</div>
            <div className="text-heading-03 font-semibold text-white leading-none tabular-nums">
              {myPlugins.length}
              <span className="text-body-short-01 font-normal text-white/70 ml-1">개</span>
            </div>
            <div className="text-caption-01 text-white/80 mt-1.5 flex gap-3">
              <span>{active} 활성</span>
              {updateCount > 0 && <span>{updateCount} 업데이트</span>}
            </div>
          </div>
        </div>

        <div className="text-caption-01 text-text-helper px-4 pt-1 pb-1.5 tracking-wider">필터</div>
        <div className="flex-1 overflow-y-auto">
          {FILTERS.map((c) => {
            const isActive = c === filter;
            const badge = c === '업데이트 있음' && updateCount > 0 ? updateCount : null;
            return (
              <button
                key={c}
                onClick={() => setFilter(c)}
                className={`flex items-center w-full text-left px-4 py-2.5 text-body-short-01 border-l-2 transition-colors ${
                  isActive
                    ? 'bg-layer-selected-01 border-l-purple-50 text-text-primary font-semibold'
                    : 'bg-transparent border-l-transparent text-text-secondary hover:bg-layer-hover-01'
                }`}
              >
                {c}
                {badge && (
                  <span className="ml-auto inline-flex items-center justify-center min-w-[18px] h-[18px] px-1.5 rounded-full bg-purple-60/20 text-purple-30 text-label-micro">
                    {badge}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        <div className="border-t border-border-subtle-01 px-4 py-3 flex items-start gap-2">
          <Icon name="idea" size={15} className="text-icon-secondary mt-0.5 flex-shrink-0" />
          <span className="text-caption-01 text-text-helper leading-4">
            플러그인은 Plugin Market에서 설치돼요. 여기서 켜고 끄거나 제공 스킬을 확인하세요.
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

      {/* plugin list */}
      <div
        className="flex-shrink-0 bg-background border-r border-border-subtle-01 overflow-y-auto min-w-0"
        style={{ width: listPanel.width }}
      >
        <div className="px-5 pt-5 pb-3">
          <h1 className="text-heading-03 font-semibold text-text-primary">내 플러그인</h1>
        </div>
        {list.map((p) => {
          const on = p.id === effectiveSel;
          return (
            <button
              key={p.id}
              onClick={() => setSel(p.id)}
              className={`flex items-start gap-2.5 w-full text-left border-b border-border-subtle-01 border-l-2 px-[18px] py-3 transition-colors ${
                on ? 'bg-layer-selected-01 border-l-purple-50' : 'bg-transparent border-l-transparent hover:bg-layer-hover-01'
              } ${p.enabled ? '' : 'opacity-55'}`}
            >
              <div
                className={`w-7 h-7 flex-shrink-0 flex items-center justify-center rounded border mt-0.5 ${
                  p.enabled
                    ? 'border-purple-50/25 bg-purple-60/14 text-purple-40'
                    : 'border-border-subtle-02 bg-layer-03 text-text-helper'
                }`}
              >
                <Icon name={p.icon} size={16} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="text-body-short-01 font-semibold text-text-primary truncate">{p.name}</span>
                  <span className="text-caption-01 font-mono text-text-helper flex-shrink-0">{p.version}</span>
                </div>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <span className="text-caption-01 text-text-helper truncate">
                    {p.skills.length}개 스킬 · {p.updated}
                  </span>
                  {hasUpdate(p) && (
                    <Tag type="purple" size="sm">
                      업데이트
                    </Tag>
                  )}
                </div>
              </div>
              {!p.enabled && (
                <Tag type="gray" size="sm">
                  off
                </Tag>
              )}
            </button>
          );
        })}
        {list.length === 0 && (
          <div className="p-10 text-center text-body-short-01 text-text-helper">
            {hydrating ? (
              '불러오는 중…'
            ) : myPlugins.length === 0 ? (
              <div className="flex flex-col items-center gap-3">
                <span>아직 설치된 플러그인이 없어요. 마켓에서 솔루션 전문가를 설치해보세요.</span>
                <button
                  onClick={() => navigate('/plugins')}
                  className="inline-flex items-center gap-1.5 text-link-primary hover:underline text-body-short-01"
                >
                  <Icon name="plug" size={14} />
                  Plugin Market 둘러보기
                  <Icon name="arrow-up-right" size={13} />
                </button>
              </div>
            ) : (
              '해당하는 플러그인이 없어요.'
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
        aria-label="Resize plugins list panel"
      />

      {/* detail */}
      <PluginDetailView pluginId={effectiveSel} />
    </div>
  );
}

export default MyPluginsScreen;

// Made with Bob
