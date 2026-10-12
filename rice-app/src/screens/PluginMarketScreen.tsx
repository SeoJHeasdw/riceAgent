import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { TwoColumnLayout } from '../components/TwoColumnLayout';
import { PluginCard } from '../components/workspace/PluginCard';
import { MarketplaceHeader } from '../components/workspace/MarketplaceHeader';
import { ArtifactDetailModal } from '../components/workspace/ArtifactDetailModal';
import { ConnectorRequiredModal } from '../components/workspace/ConnectorRequiredModal';
import { CardSkeletonGrid } from '../components/workspace/MarketSkeleton';
import { NotificationVariant } from '../components/workspace/NotificationToast';
import { useToast } from '../lib/toast/ToastContext';
import { useMarketplace } from '../hooks/useMarketplace';
import { useLearning } from '../lib/learning/LearningContext';
import { connectorIdForScope } from '../lib/learningData';
import { useResizable } from '../hooks/useResizable';
import { Icon, ResizeHandle } from '../components/ui';
import { pluginCategoryCodes, type PluginCategoryCode } from '../lib/mockData';
import { Plugin } from '../lib/types';
import { installedPluginId } from '../lib/skillBridge';

/**
 * PluginMarketScreen - Marketplace for discovering and installing solution expert plugins
 */

// 카테고리 sentinel — 상태에 저장하는 stable code
const CAT_ALL = '__all__' as const;
const CAT_INSTALLED = '__installed__' as const;
type PluginCategorySentinel = typeof CAT_ALL | typeof CAT_INSTALLED;
type SelectedPluginCategory = PluginCategorySentinel | PluginCategoryCode | string;

// PluginCategoryCode → 번역. t()가 정적 키만 수용하므로 명시적 맵 사용.
function getPluginCategoryLabel(code: PluginCategoryCode, t: ReturnType<typeof useTranslation<'plugins'>>['t']): string {
  const map: Record<PluginCategoryCode, string> = {
    sales: t('category.sales'),
    hr: t('category.hr'),
    finance: t('category.finance'),
    marketing: t('category.marketing'),
    itOps: t('category.itOps'),
  };
  return map[code] ?? code;
}

function PluginMarketScreen() {
  const { t } = useTranslation('plugins');
  const navigate = useNavigate();
  const {
    filteredPlugins,
    catalogLoading,
    searchQuery,
    setSearchQuery,
    sortBy,
    setSortBy,
    installArtifact,
    uninstallArtifact,
    togglePlugin,
  } = useMarketplace();
  const { installPluginFromMarket, setMyPlugins } = useLearning();

  // selectedCategory는 stable code 저장 — 렌더 시 t()로 표시
  const [selectedCategory, setSelectedCategory] = useState<SelectedPluginCategory>(CAT_ALL);
  const [detailModalPlugin, setDetailModalPlugin] = useState<Plugin | null>(null);
  const [connectorPromptPlugin, setConnectorPromptPlugin] = useState<Plugin | null>(null);
  const toast = useToast();

  // Resizable left rail
  const railPanel = useResizable({
    initial: 282,
    min: 184,
    max: 360,
    storageKey: 'rice.panel.pluginmarket',
  });

  // Filter by category — sentinel 처리
  const categoryFilteredPlugins = filteredPlugins.filter((plugin) => {
    if (selectedCategory === CAT_ALL) return true;
    if (selectedCategory === CAT_INSTALLED) return plugin.installed;
    return plugin.category === selectedCategory;
  });

  // Sort options — stable value 저장, 렌더 시 t()로 표시
  const sortOptions = [
    { value: 'popular', label: t('sortPopular') },
    { value: 'recent', label: t('sortRecent') },
    { value: 'skills', label: t('sortSkillCount') },
  ];

  // Handle plugin installation
  const handleInstall = async (plugin: Plugin) => {
    setDetailModalPlugin(null);
    const job = toast.progress({ label: t('toastDownloading'), sublabel: plugin.name, progress: 0 });

    // Simulate download progress
    for (let i = 0; i <= 50; i += 10) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      job.update({ progress: i });
    }

    job.update({ label: t('toastInstalling'), progress: 50 });

    // Simulate installation progress
    for (let i = 50; i <= 100; i += 10) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      job.update({ progress: i });
    }

    const installResult = await installArtifact(plugin.id, 'plugin');
    if (!installResult) {
      job.done('error', t('toastInstallFailed'));
      return;
    }
    // 설치를 "나의 플러그인"에도 즉시 반영 (스킬의 installSkillFromMarket 와 대칭).
    installPluginFromMarket(plugin, {
      enabled: installResult.enabled,
      riskFlags: installResult.riskFlags,
    });
    job.done('success', t('toastInstallDone'));
    if (installResult.riskFlags.length) {
      // 위험 스캔 플래그 — 런타임이 비활성으로 설치했다. 상세는 나의 플러그인 배지에.
      showToast(t('toastRiskFlagged', { name: plugin.name }), 'warning');
    } else if (installResult.missingConnectors.length) {
      showToast(t('toastNeedsConnector', { name: plugin.name }), 'warning');
    }
    // The toast lingers, then animates itself out automatically.
  };

  // Handle plugin uninstallation
  const handleUninstall = async (plugin: Plugin) => {
    setDetailModalPlugin(null);
    const removed = await uninstallArtifact(plugin.id, 'plugin');
    if (!removed) {
      showToast(t('toastUninstallFailed', { name: plugin.name }), 'error');
      return;
    }
    const ownedId = installedPluginId(plugin.id);
    setMyPlugins((prev) => prev.filter((owned) => owned.id !== ownedId));
    showToast(t('toastUninstalled', { name: plugin.name }), 'info');
  };

  // Handle plugin toggle
  const handleToggle = async (plugin: Plugin) => {
    const next = !plugin.enabled;
    const ok = await togglePlugin(plugin.id, next);
    if (!ok) {
      setConnectorPromptPlugin(plugin);
      return;
    }
    showToast(
      plugin.enabled
        ? t('toastPaused', { name: plugin.name })
        : t('toastEnabled', { name: plugin.name }),
      plugin.enabled ? 'info' : 'success'
    );
  };

  // Show toast notification — the toast owns its own dismiss timing/animation.
  const showToast = (message: string, variant: NotificationVariant = 'success') => {
    toast.notify({ message, variant });
  };

  const requiredConnectors = (plugin: Plugin | null) =>
    plugin?.connectors?.map((c) => c.name).filter(Boolean) ?? [];

  const connectorTarget = (plugin: Plugin | null) => {
    const first = requiredConnectors(plugin)[0];
    const cid = first ? connectorIdForScope(first) : null;
    return cid ? `/connectors/${cid}` : '/connectors';
  };

  // Installed plugins count
  const installedCount = filteredPlugins.filter((p) => p.installed).length;
  const activeCount = filteredPlugins.filter((p) => p.enabled).length;

  // 카테고리 버튼 레이블 (sentinel + plugin category codes)
  const getCategoryButtonLabel = (cat: SelectedPluginCategory): string => {
    if (cat === CAT_ALL) return t('categoryAll');
    if (cat === CAT_INSTALLED) return t('categoryInstalled');
    // pluginCategoryCode인지 확인
    if (pluginCategoryCodes.includes(cat as PluginCategoryCode)) {
      return getPluginCategoryLabel(cat as PluginCategoryCode, t);
    }
    return cat; // 백엔드 자유 문자열 (번역 없음)
  };

  const allCategories: SelectedPluginCategory[] = [CAT_ALL, CAT_INSTALLED, ...pluginCategoryCodes];

  return (
    <TwoColumnLayout>
      {/* Left Rail */}
      <div
        className="flex-shrink-0 bg-layer-01 border-r border-border-subtle-01 flex flex-col min-w-0 overflow-hidden"
        style={{ width: railPanel.width }}
      >
        {/* Installed Summary */}
        <div className="p-3">
          <div className="domain-summary domain-summary--plugin rounded-xl p-4">
            <div className="mb-2 flex items-center gap-1.5 text-caption-01 text-text-helper">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                <circle cx="12" cy="12" r="9" />
                <circle cx="12" cy="12" r="3.5" />
              </svg>
              {t('railExpertLabel')}
            </div>
            <div className="text-heading-03 font-semibold text-text-primary leading-none tracking-tight tabular-nums">
              {installedCount}
              <span className="text-body-short-01 font-normal text-text-helper ml-1.5">{t('railInstalledUnit')}</span>
            </div>
            <div className="domain-summary__chip mt-3 inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-caption-01">
              <span className="w-1.5 h-1.5 rounded-full bg-support-success" />
              {t('railActiveUnit', { count: activeCount })}
            </div>
          </div>
        </div>

        {/* Categories */}
        <div className="text-caption-01 font-semibold uppercase tracking-wider text-text-helper px-4 py-1.5">
          {t('railSolutionsTitle')}
        </div>
        <div className="flex-1 overflow-y-auto px-2 space-y-0.5">
          {allCategories.map((category) => {
            const isActive = category === selectedCategory;
            return (
              <button
                key={category}
                onClick={() => setSelectedCategory(category)}
                className={`flex items-center w-full min-w-0 text-left border-none px-2.5 py-2 text-body-short-01 rounded-lg transition-colors ${
                  isActive
                    ? 'bg-layer-selected-01 text-text-primary font-semibold'
                    : 'bg-transparent text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary'
                }`}
              >
                <span className="truncate">{getCategoryButtonLabel(category)}</span>
                {category === CAT_INSTALLED && (
                  <span className="ml-auto pl-2 flex-shrink-0 text-caption-01 text-text-helper font-mono">
                    {installedCount}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* Info Footer */}
        <div className="border-t border-border-subtle-01 px-4 py-3 flex items-start gap-2">
          <Icon name="idea" size={15} className="text-icon-secondary mt-0.5 flex-shrink-0" />
          <span className="text-caption-01 text-text-helper leading-4">
            {t('railInfoHint')}
          </span>
        </div>
      </div>

      {/* Rail resize handle */}
      <ResizeHandle
        onPointerDown={railPanel.startResize}
        onDoubleClick={railPanel.resetWidth}
        isResizing={railPanel.isResizing}
        value={railPanel.width} min={railPanel.min} max={railPanel.max} onResizeBy={railPanel.resizeBy}
        aria-label="Resize solutions panel"
      />

      {/* Main Content */}
      <div className="flex-1 min-w-0 overflow-y-auto bg-background">
        <MarketplaceHeader
          title={t('title')}
          subtitle={t('subtitle')}
          searchValue={searchQuery}
          onSearchChange={setSearchQuery}
          sortValue={sortBy}
          sortOptions={sortOptions}
          onSortChange={setSortBy}
        />

        {/* Plugins Grid */}
        <div className="px-8 py-5 pb-10">
          {catalogLoading && categoryFilteredPlugins.length === 0 ? (
            <CardSkeletonGrid />
          ) : categoryFilteredPlugins.length > 0 ? (
            <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(330px, 1fr))' }}>
              {categoryFilteredPlugins.map((plugin) => (
                <PluginCard
                  key={plugin.id}
                  {...plugin}
                  onInstall={() => handleInstall(plugin)}
                  onToggle={() => handleToggle(plugin)}
                  onOpenDetails={() => setDetailModalPlugin(plugin)}
                />
              ))}
            </div>
          ) : (
            <div className="text-center py-16 text-text-helper">
              <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl border border-purple-50/20 bg-purple-60/10 text-purple-30">
                <Icon name="search" size={22} />
              </div>
              <div className="text-body-long-01">
                {selectedCategory === CAT_INSTALLED
                  ? t('emptyInstalledTitle')
                  : t('emptySearchTitle')}
              </div>
              <div className="text-caption-01 mt-1">
                {selectedCategory === CAT_INSTALLED
                  ? t('emptyInstalledHint')
                  : t('emptySearchHint')}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Detail Modal */}
      {detailModalPlugin && (
        <ArtifactDetailModal
          isOpen={true}
          onClose={() => setDetailModalPlugin(null)}
          artifact={detailModalPlugin}
          type="plugin"
          onInstall={() => handleInstall(detailModalPlugin)}
          onUninstall={() => handleUninstall(detailModalPlugin)}
          onToggle={(enabled) => {
            void togglePlugin(detailModalPlugin.id, enabled).then((ok) => {
              if (!ok) {
                setConnectorPromptPlugin(detailModalPlugin);
                return;
              }
              setDetailModalPlugin({ ...detailModalPlugin, enabled });
            });
          }}
        />
      )}
      {connectorPromptPlugin && (
        <ConnectorRequiredModal
          open={true}
          pluginName={connectorPromptPlugin.name}
          connectors={requiredConnectors(connectorPromptPlugin)}
          onClose={() => setConnectorPromptPlugin(null)}
          onConnect={() => {
            const target = connectorTarget(connectorPromptPlugin);
            setConnectorPromptPlugin(null);
            setDetailModalPlugin(null);
            navigate(target);
          }}
        />
      )}
    </TwoColumnLayout>
  );
}

export default PluginMarketScreen;
