import { useState } from 'react';
import { useTranslation, Trans } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Button, Icon, Tag, ResizeHandle, Toggle } from '../components/ui';
import { ConnectorRequiredModal } from '../components/carbon/ConnectorRequiredModal';
import { useResizable } from '../hooks/useResizable';
import { useLearning } from '../lib/learning/LearningContext';
import { api } from '../lib/api';
import { connectorIdForScope, type MyPlugin } from '../lib/learningData';

/**
 * MyPluginsScreen ("나의 플러그인") — management screen for installed plugins.
 * Master-detail: enable/disable a plugin, see its source/version/author,
 * the skills it provides (invokable via "/" or auto-used), and example
 * prompts to try. Mirrors the "나의 스킬" screen.
 */

/** stable filter code — UI 라벨은 렌더 시 plugins:myPlugins.filter* 키로 번역 */
const FILTER_CODES = ['all', 'active', 'has_update', 'inactive'] as const;
type Filter = (typeof FILTER_CODES)[number];

const hasUpdate = (p: MyPlugin) => !!p.update;

const SectionLabel: React.FC<{ children: React.ReactNode; right?: React.ReactNode }> = ({ children, right }) => (
  <div className="flex items-center mb-2.5">
    <span className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">{children}</span>
    {right && <span className="ml-auto">{right}</span>}
  </div>
);

type PluginTab = 'skills' | 'knowledge' | 'permissions';

/**
 * plugin.updated stable code → 번역 문자열.
 * 레거시 자유 문자열(ko/en 자연어)은 그대로 표시하고, 새 stable code만 번역한다.
 */
function renderUpdated(updated: string, t: ReturnType<typeof useTranslation<'plugins'>>['t']): string {
  switch (updated) {
    case 'plugin_updated_just_installed': return t('myPlugins.updatedJustInstalled');
    case 'plugin_updated_builtin':        return t('myPlugins.updatedBuiltin');
    case 'plugin_updated_installed':      return t('myPlugins.updatedInstalled');
    case 'plugin_updated_needs_connector': return t('myPlugins.updatedNeedsConnector');
    default: return updated; // 레거시 자유 문자열 폴백
  }
}

const PluginDetailView: React.FC<{ pluginId: string | null }> = ({ pluginId }) => {
  const navigate = useNavigate();
  const { t } = useTranslation('plugins');
  const { myPlugins, setMyPlugins } = useLearning();
  const [tab, setTab] = useState<PluginTab>('skills');
  const [connectorDialogOpen, setConnectorDialogOpen] = useState(false);
  const plugin = myPlugins.find((p) => p.id === pluginId);

  if (!plugin || !pluginId) {
    return <div className="flex-1 flex items-center justify-center text-body-short-02 text-text-helper">{t('myPlugins.selectPlaceholder')}</div>;
  }

  const patch = (fn: (p: MyPlugin) => MyPlugin) => setMyPlugins((a) => a.map((x) => (x.id === pluginId ? fn(x) : x)));
  const permissionsNeedingConnection = plugin.permissions.filter((p) => p.mode === 'needs_connection');
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
    plugin.update && patch((x) => ({ ...x, update: undefined, version: plugin.update!.version, updated: 'plugin_updated_just_installed' }));

  const meta: [string, string][] = [
    [t('myPlugins.metaSource'),   plugin.source],
    [t('myPlugins.metaVersion'),  plugin.version],
    [t('myPlugins.metaAuthor'),   plugin.author],
    [t('myPlugins.metaUpdated'),  renderUpdated(plugin.updated, t)],
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
                {t('myPlugins.tagPlugin')}
              </Tag>
              {plugin.builtin && (
                <Tag type="teal" size="sm">
                  {t('myPlugins.tagBuiltin')}
                </Tag>
              )}
              {!plugin.enabled && (
                <Tag type="gray" size="sm">
                  {t('myPlugins.tagInactive')}
                </Tag>
              )}
            </div>
            <p className="mt-1.5 text-body-short-01 leading-5 text-text-secondary">{plugin.summary}</p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <span className="text-caption-01 text-text-helper">{plugin.enabled ? t('myPlugins.statusActive') : t('myPlugins.statusInactive')}</span>
            <Toggle
              checked={plugin.enabled}
              onChange={toggle}
              tone="purple"
              aria-label={plugin.enabled ? t('myPlugins.ariaDisable', { name: plugin.name }) : t('myPlugins.ariaEnable', { name: plugin.name })}
            />
          </div>
        </div>

        {/* update available — sits right under the plugin description */}
        {plugin.update && !plugin.builtin && (
          <div className="mt-4 flex items-center gap-3 rounded-xl border border-blue-50/30 bg-gradient-to-r from-blue-60/12 to-blue-60/[0.04] pl-3 pr-3 py-2.5">
            <div className="w-8 h-8 flex-shrink-0 flex items-center justify-center rounded-lg bg-blue-60/15">
              <Icon name="renew" size={16} className="text-link-primary" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1.5 text-body-short-01 leading-5 text-text-primary">
                <span className="font-semibold">{t('myPlugins.updateTitle')}</span>
                <span className="font-mono font-semibold text-link-primary">{plugin.update.version}</span>
                <span className="text-text-secondary">{t('myPlugins.updateAvailable')}</span>
              </div>
              <div className="text-caption-01 leading-4 text-text-helper truncate mt-0.5">{plugin.update.note}</div>
            </div>
            <Button kind="primary" size="sm" icon={<Icon name="renew" size={14} />} onClick={update} className="flex-shrink-0">
              {t('myPlugins.updateButton')}
            </Button>
          </div>
        )}
      </div>

      <div className="px-7 pt-5 pb-10 flex flex-col gap-7">
        {/* metadata */}
        <div>
          <SectionLabel>{t('myPlugins.metaTitle')}</SectionLabel>
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
              ['skills',      t('myPlugins.tabSkills',      { count: plugin.skills.length })],
              ['knowledge',   t('myPlugins.tabKnowledge',   { count: plugin.knowledge.length })],
              ['permissions', t('myPlugins.tabPermissions', { count: plugin.permissions.length })],
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
                {t('myPlugins.tabSkillsViewAll')} <Icon name="arrow-up-right" size={12} />
              </button>
            )}
            {tab === 'permissions' && permissionsNeedingConnection.length > 0 && (
              <Button kind="tertiary" size="sm" icon={<Icon name="arrow-up-right" size={14} />} onClick={() => navigate(firstConnectorTarget)}>
                {t('myPlugins.tabPermissionsConnect')}
              </Button>
            )}
          </div>

          {tab === 'skills' && (
            <>
              <p className="text-caption-01 text-text-helper mb-2.5">
                <Trans
                  t={t}
                  i18nKey="myPlugins.skillsHint"
                  components={{ slash: <span className="font-mono text-text-secondary" /> }}
                />
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
                {t('myPlugins.knowledgeHint')}
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
                {t('myPlugins.permissionsHint')}
              </p>
              {plugin.permissions.length === 0 ? (
                <div className="text-caption-01 text-text-helper bg-layer-01 border border-border-subtle-01 rounded-lg px-3.5 py-3.5">
                  {t('myPlugins.permissionsEmpty')}
                </div>
              ) : (
                <div className="flex flex-col gap-px bg-border-subtle-01 rounded-lg overflow-hidden">
                  {plugin.permissions.map((p, i) => {
                    const cid = connectorIdForScope(p.name);
                    const needsConnection = p.mode === 'needs_connection';
                    return (
                      <div key={i} className="flex items-center gap-2.5 bg-background px-3.5 py-2.5">
                        <button
                          type="button"
                          onClick={() => navigate(cid ? `/connectors/${cid}` : '/connectors')}
                          title={cid ? t('myPlugins.permTitleViewConnector') : t('myPlugins.permTitleGoConnectors')}
                          className="group flex items-center gap-2.5 flex-1 min-w-0 text-left"
                        >
                          <Icon name={needsConnection ? 'connect' : 'lock'} size={15} className="text-purple-40 flex-shrink-0 opacity-80" />
                          <span className="flex-1 min-w-0 text-body-short-01 font-mono text-text-primary truncate group-hover:underline">
                            {p.name}
                          </span>
                          {cid && <Icon name="arrow-up-right" size={13} className="text-icon-secondary opacity-50 flex-shrink-0" />}
                        </button>
                        <Tag type={needsConnection ? 'yellow' : 'gray'} size="sm">
                          {needsConnection ? t('myPlugins.permModeNeedsConnection') : t('myPlugins.permModeConnected')}
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
          <SectionLabel>{t('myPlugins.examplesTitle')}</SectionLabel>
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
              {t('myPlugins.examplesEmpty')}
            </div>
          )}
        </div>

        {/* actions */}
        <div className="flex gap-2 border-t border-border-subtle-01 pt-4 items-center">
          <Button kind="secondary" size="sm" icon={<Icon name="arrow-up-right" size={15} />} onClick={() => navigate('/plugins')}>
            {t('myPlugins.actionViewMarket')}
          </Button>
          <div className="flex-1" />
          {plugin.builtin ? (
            <span className="text-caption-01 text-text-helper">{t('myPlugins.actionBuiltinNote')}</span>
          ) : (
            <button
              onClick={remove}
              className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg border border-support-error/40 bg-transparent text-support-error text-body-short-01 font-semibold hover:bg-support-error/10 hover:border-support-error/70 transition-colors"
            >
              <Icon name="trash" size={15} />
              {t('myPlugins.actionRemove')}
            </button>
          )}
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
  const { t } = useTranslation('plugins');
  const { myPlugins, hydrating } = useLearning();
  const [filter, setFilter] = useState<Filter>('all');
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
    filter === 'all' ||
    (filter === 'active' && p.enabled) ||
    (filter === 'has_update' && hasUpdate(p)) ||
    (filter === 'inactive' && !p.enabled);

  const list = myPlugins.filter(match);
  const active = myPlugins.filter((p) => p.enabled).length;
  const updateCount = myPlugins.filter(hasUpdate).length;

  const selectedExists = sel && myPlugins.some((p) => p.id === sel);
  const effectiveSel = selectedExists ? sel : list[0]?.id ?? myPlugins[0]?.id ?? null;

  /** filter code → 번역 라벨 */
  const filterLabel = (code: Filter): string => {
    switch (code) {
      case 'all':        return t('myPlugins.filterAll');
      case 'active':     return t('myPlugins.filterActive');
      case 'has_update': return t('myPlugins.filterHasUpdate');
      case 'inactive':   return t('myPlugins.filterInactive');
    }
  };

  return (
    <div className="flex flex-1 min-w-0 bg-background overflow-hidden">
      {/* rail */}
      <div
        className="flex-shrink-0 bg-layer-01 border-r border-border-subtle-01 flex flex-col min-w-0 overflow-hidden"
        style={{ width: railPanel.width }}
      >
        <div className="p-4">
          <div className="domain-summary domain-summary--plugin rounded-xl p-4 text-white">
            <div className="text-caption-01 text-white/70 mb-1">{t('myPlugins.summaryTitle')}</div>
            <div className="text-heading-03 font-semibold text-white leading-none tabular-nums">
              {myPlugins.length}
              {t('myPlugins.summaryCountUnit') && (
                <span className="text-body-short-01 font-normal text-white/70 ml-1">{t('myPlugins.summaryCountUnit')}</span>
              )}
            </div>
            <div className="text-caption-01 text-white/80 mt-1.5 flex gap-3">
              <span>{t('myPlugins.summaryActive', { count: active })}</span>
              {updateCount > 0 && <span>{t('myPlugins.summaryUpdate', { count: updateCount })}</span>}
            </div>
          </div>
        </div>

        <div className="text-caption-01 text-text-helper px-4 pt-1 pb-1.5 tracking-wider">{t('myPlugins.filterLabel')}</div>
        <div className="flex-1 overflow-y-auto">
          {FILTER_CODES.map((c) => {
            const isActive = c === filter;
            const badge = c === 'has_update' && updateCount > 0 ? updateCount : null;
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
                {filterLabel(c)}
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
            {t('myPlugins.tipInstall')}
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
          <h1 className="text-heading-03 font-semibold text-text-primary">{t('myPlugins.listTitle')}</h1>
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
                    {t('myPlugins.listSkillCount', { count: p.skills.length })} · {renderUpdated(p.updated, t)}
                  </span>
                  {hasUpdate(p) && (
                    <Tag type="purple" size="sm">
                      {t('myPlugins.updateButton')}
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
              t('myPlugins.listLoading')
            ) : myPlugins.length === 0 ? (
              <div className="flex flex-col items-center gap-3">
                <span>{t('myPlugins.listEmpty')}</span>
                <button
                  onClick={() => navigate('/plugins')}
                  className="inline-flex items-center gap-1.5 text-link-primary hover:underline text-body-short-01"
                >
                  <Icon name="plug" size={14} />
                  {t('myPlugins.listEmptyBrowse')}
                  <Icon name="arrow-up-right" size={13} />
                </button>
              </div>
            ) : (
              t('myPlugins.listNoMatch')
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
