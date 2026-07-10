import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { TwoColumnLayout } from '../components/TwoColumnLayout';
import { PluginCard } from '../components/carbon/PluginCard';
import { MarketplaceHeader } from '../components/carbon/MarketplaceHeader';
import { ArtifactDetailModal } from '../components/carbon/ArtifactDetailModal';
import { ConnectorRequiredModal } from '../components/carbon/ConnectorRequiredModal';
import { CardSkeletonGrid } from '../components/carbon/MarketSkeleton';
import { NotificationVariant } from '../components/carbon/NotificationToast';
import { useToast } from '../lib/toast/ToastContext';
import { useMarketplace } from '../hooks/useMarketplace';
import { useLearning } from '../lib/learning/LearningContext';
import { connectorIdForScope } from '../lib/learningData';
import { useResizable } from '../hooks/useResizable';
import { Icon, ResizeHandle } from '../components/ui';
import { pluginCategories } from '../lib/mockData';
import { Plugin } from '../lib/types';

/**
 * PluginMarketScreen - Marketplace for discovering and installing solution expert plugins
 */

function PluginMarketScreen() {
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
  const { installPluginFromMarket } = useLearning();

  const [selectedCategory, setSelectedCategory] = useState('전체');
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

  // Filter by category
  const categoryFilteredPlugins = filteredPlugins.filter((plugin) => {
    if (selectedCategory === '전체') return true;
    if (selectedCategory === '설치됨') return plugin.installed;
    return plugin.category === selectedCategory;
  });

  // Sort options
  const sortOptions = [
    { value: 'popular', label: '인기순' },
    { value: 'recent', label: '최신순' },
    { value: 'skills', label: '스킬 수' },
  ];

  // Handle plugin installation
  const handleInstall = async (plugin: Plugin) => {
    setDetailModalPlugin(null);
    const job = toast.progress({ label: '다운로드 중', sublabel: plugin.name, progress: 0 });

    // Simulate download progress
    for (let i = 0; i <= 50; i += 10) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      job.update({ progress: i });
    }

    job.update({ label: '설치 중', progress: 50 });

    // Simulate installation progress
    for (let i = 50; i <= 100; i += 10) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      job.update({ progress: i });
    }

    const installResult = await installArtifact(plugin.id, 'plugin');
    // 설치를 "내 플러그인"에도 즉시 반영 (스킬의 installSkillFromMarket 와 대칭).
    installPluginFromMarket(plugin, { enabled: installResult?.enabled ?? true });
    if (installResult?.missingConnectors.length) {
      job.done('success', '설치됨 · 커넥터 연결 필요');
      showToast(`${plugin.name}을(를) 활성화하려면 커넥터를 연결하세요`, 'warning');
    } else {
      job.done('success', '설치 완료');
    }
    // The toast lingers, then animates itself out automatically.
  };

  // Handle plugin uninstallation
  const handleUninstall = async (plugin: Plugin) => {
    setDetailModalPlugin(null);
    await uninstallArtifact(plugin.id, 'plugin');
    showToast(`${plugin.name}이(가) 제거되었습니다`, 'info');
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
        ? `${plugin.name}이(가) 일시정지되었습니다`
        : `${plugin.name}이(가) 활성화되었습니다`,
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

  // Categories with "설치됨" option
  const categories = ['전체', '설치됨', ...pluginCategories];

  return (
    <TwoColumnLayout>
      {/* Left Rail */}
      <div
        className="flex-shrink-0 bg-layer-01 border-r border-border-subtle-01 flex flex-col min-w-0 overflow-hidden"
        style={{ width: railPanel.width }}
      >
        {/* Installed Summary */}
        <div className="p-3">
          <div className="domain-summary domain-summary--plugin rounded-xl p-4 text-white">
            <div className="flex items-center gap-1.5 text-caption-01 text-white/70 mb-2">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                <circle cx="12" cy="12" r="9" />
                <circle cx="12" cy="12" r="3.5" />
              </svg>
              내 전문가
            </div>
            <div className="text-heading-03 font-semibold text-white leading-none tracking-tight tabular-nums">
              {installedCount}
              <span className="text-body-short-01 font-normal text-white/70 ml-1.5">설치됨</span>
            </div>
            <div className="inline-flex items-center gap-1.5 mt-3 px-2 py-0.5 rounded-full bg-white/15 text-caption-01 text-white">
              <span className="w-1.5 h-1.5 rounded-full bg-support-success" />
              {activeCount} 활성
            </div>
          </div>
        </div>

        {/* Categories */}
        <div className="text-caption-01 font-semibold uppercase tracking-wider text-text-helper px-4 py-1.5">
          솔루션
        </div>
        <div className="flex-1 overflow-y-auto px-2 space-y-0.5">
          {categories.map((category) => {
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
                <span className="truncate">{category}</span>
                {category === '설치됨' && (
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
            각 전문가는 해당 솔루션 개발팀에서 제공하고 관리합니다.
          </span>
        </div>
      </div>

      {/* Rail resize handle */}
      <ResizeHandle
        onPointerDown={railPanel.startResize}
        onDoubleClick={railPanel.resetWidth}
        isResizing={railPanel.isResizing}
        aria-label="Resize solutions panel"
      />

      {/* Main Content */}
      <div className="flex-1 min-w-0 overflow-y-auto bg-background">
        <MarketplaceHeader
          title="플러그인 마켓"
          subtitle="솔루션 전문가를 설치해 에이전트를 해당 도메인 전문가로 만드세요 — 고객 데모, 온프레미스 구축, 오류 처리에 활용할 수 있습니다"
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
              <div className="text-heading-06 mb-4">🔍</div>
              <div className="text-body-long-01">
                  {selectedCategory === '설치됨'
                    ? '아직 설치된 전문가가 없습니다'
                    : '해당 솔루션의 전문가를 찾지 못했습니다'}
                </div>
                <div className="text-caption-01 mt-1">
                  {selectedCategory === '설치됨'
                    ? '마켓에서 솔루션 전문가를 설치해보세요'
                    : '검색어나 필터를 조정해보세요'}
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

// Made with Bob
