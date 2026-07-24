import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { TwoColumnLayout } from '../components/TwoColumnLayout';
import { SkillCard } from '../components/carbon/SkillCard';
import { MarketplaceHeader } from '../components/carbon/MarketplaceHeader';
import { SponsorModal } from '../components/carbon/SponsorModal';
import { ArtifactDetailModal } from '../components/carbon/ArtifactDetailModal';
import { CardSkeletonGrid } from '../components/carbon/MarketSkeleton';
import { NotificationVariant } from '../components/carbon/NotificationToast';
import { SkillAuthorModal } from '../components/carbon/SkillAuthorModal';
import { useToast } from '../lib/toast/ToastContext';
import { useMarketplace } from '../hooks/useMarketplace';
import { useResizable } from '../hooks/useResizable';
import { useLearning } from '../lib/learning/LearningContext';
import { Icon, ResizeHandle } from '../components/ui';
import { skillCategoryCodes, type SkillCategoryCode } from '../lib/mockData';
import { Skill } from '../lib/types';
import {
  installedSkillId,
  isUserPublishedMarketId,
  ownedSkillIdFromPublishedMarketId,
} from '../lib/skillBridge';

/**
 * SkillMarketScreen - Marketplace for discovering and installing skills
 */

// 카테고리 code → 번역 함수. t()가 정적 키만 수용하므로 명시적 맵을 사용.
// (SkillAuthorModal의 getCategoryLabel과 동일 패턴)
function getCategoryLabel(code: SkillCategoryCode, t: ReturnType<typeof useTranslation<'skills'>>['t']): string {
  const map: Record<SkillCategoryCode, string> = {
    all: t('market.categoryAll'),
    dataAnalysis: t('market.categoryDataAnalysis'),
    automation: t('market.categoryAutomation'),
    integration: t('market.categoryIntegration'),
    dev: t('market.categoryDev'),
    communication: t('market.categoryCommunication'),
    productivity: t('market.categoryProductivity'),
  };
  return map[code] ?? code;
}

function SkillMarketScreen() {
  const { t } = useTranslation('skills');
  const {
    filteredSkills,
    catalogLoading,
    searchQuery,
    setSearchQuery,
    sortBy,
    setSortBy,
    installArtifact,
    uninstallArtifact,
    unpublishSkill,
    sponsorSkill,
    publishSkill,
    bluePointBalance,
    setBluePointBalance,
  } = useMarketplace();

  // 설치를 학습 store까지 배선 — 설치한 마켓 스킬이 "나의 스킬"에 소유 인스턴스로 등장한다.
  const {
    installSkillFromMarket,
    refreshSkills,
    setDetails: setOwnedDetails,
    setSkills: setOwnedSkills,
  } = useLearning();

  // selectedCategory는 stable code 저장 — 렌더 시 t()로 표시
  const [selectedCategory, setSelectedCategory] = useState<SkillCategoryCode>('all');
  const [sponsorModalSkill, setSponsorModalSkill] = useState<Skill | null>(null);
  const [detailModalSkill, setDetailModalSkill] = useState<Skill | null>(null);
  const [authorOpen, setAuthorOpen] = useState(false);
  // Just-published skill — briefly ringed + scrolled into view in the grid.
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const highlightRef = useRef<HTMLDivElement | null>(null);
  const toast = useToast();

  // Resizable left rail
  const railPanel = useResizable({
    initial: 282,
    min: 184,
    max: 360,
    storageKey: 'rice.panel.skillmarket',
  });

  // Filter by category — 'all'은 전체 표시
  const categoryFilteredSkills = filteredSkills.filter(
    (skill) => selectedCategory === 'all' || skill.category === selectedCategory
  );

  // Sort options — stable code 저장, 렌더 시 t()로 표시
  const sortOptions = [
    { value: 'popular', label: t('market.sortPopular') },
    { value: 'recent', label: t('market.sortRecent') },
    { value: 'rating', label: t('market.sortRating') },
    { value: 'sponsored', label: t('market.sortSponsored') },
  ];

  // Handle skill installation
  const handleInstall = async (skill: Skill) => {
    setDetailModalSkill(null);
    const job = toast.progress({ label: t('market.toastDownloading'), sublabel: skill.name, progress: 0 });

    // Simulate download progress
    for (let i = 0; i <= 50; i += 10) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      job.update({ progress: i });
    }

    job.update({ label: t('market.toastInstalling'), progress: 50 });

    // Simulate installation progress
    for (let i = 50; i <= 100; i += 10) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      job.update({ progress: i });
    }

    const installResult = await installArtifact(skill.id, 'skill');
    if (!installResult) {
      job.done('error', t('market.toastInstallFailed'));
      return;
    }
    // 학습 store에 소유 인스턴스로 materialize (origin:installed, upstream 기록).
    installSkillFromMarket(skill);
    job.done('success', t('market.toastInstallDone'));
    if (installResult.riskFlags.length) {
      // 위험 스캔 플래그 — 스킬은 설치를 막지 않는다(나의 스킬에서 본문 검수 가능).
      showToast(t('market.toastRiskFlagged', { name: skill.name }), 'warning');
    }
    // The toast lingers, then animates itself out automatically.
  };

  // Handle skill uninstallation
  const handleUninstall = async (skill: Skill) => {
    setDetailModalSkill(null);

    // 내가 올린 스킬의 "제거"는 설치 상태를 끄는 동작이 아니라 마켓에서 내리는
    // 동작이다. 즉시 카탈로그 상태에서 제거해 다른 화면을 다녀올 필요가 없게 한다.
    if (isUserPublishedMarketId(skill.id)) {
      const removed = await unpublishSkill(skill.id);
      if (!removed) {
        showToast(t('market.toastRemoveFailed', { name: skill.name }), 'error');
        return;
      }
      const ownedId = ownedSkillIdFromPublishedMarketId(skill.id);
      if (ownedId) {
        setOwnedSkills((prev) =>
          prev.map((owned) => (owned.id === ownedId ? { ...owned, shared: false } : owned)),
        );
        setOwnedDetails((prev) =>
          prev[ownedId]
            ? { ...prev, [ownedId]: { ...prev[ownedId], shared: false } }
            : prev,
        );
      }
      showToast(t('market.toastUninstalled', { name: skill.name }), 'info');
      return;
    }

    const removed = await uninstallArtifact(skill.id, 'skill');
    if (!removed) {
      showToast(t('market.toastRemoveFailed', { name: skill.name }), 'error');
      return;
    }
    const ownedId = installedSkillId(skill.id);
    setOwnedSkills((prev) => prev.filter((owned) => owned.id !== ownedId));
    setOwnedDetails((prev) => {
      if (!(ownedId in prev)) return prev;
      const next = { ...prev };
      delete next[ownedId];
      return next;
    });
    showToast(t('market.toastUninstalled', { name: skill.name }), 'info');
  };

  // Handle sponsorship
  const handleSponsor = async (amount: number) => {
    if (sponsorModalSkill) {
      await sponsorSkill(sponsorModalSkill.id, amount);
      setBluePointBalance(bluePointBalance - amount);
      showToast(t('market.toastSponsored', { author: sponsorModalSkill.author, amount: amount.toLocaleString() }));
      setSponsorModalSkill(null);
    }
  };

  // Show toast notification — the toast owns its own dismiss timing/animation.
  const showToast = (message: string, variant: NotificationVariant = 'success') => {
    toast.notify({ message, variant });
  };

  // Scroll the freshly-published skill into view, then fade the ring out.
  useEffect(() => {
    if (!highlightId) return;
    highlightRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    const timer = setTimeout(() => setHighlightId(null), 2800);
    return () => clearTimeout(timer);
  }, [highlightId]);

  // Surface a skill in the grid regardless of current filters (recent + All + no search).
  const surfaceSkill = (skill: Skill) => {
    setSearchQuery('');
    setSelectedCategory('all');
    setSortBy('recent');
    setHighlightId(skill.id);
  };

  // Publish a user-authored skill, then reveal it at the top of the grid.
  const handlePublish = async (draft: Parameters<typeof publishSkill>[0]) => {
    const skill = await publishSkill(draft);
    if (!skill) {
      showToast(t('market.toastPublishFailed', { name: draft.name }), 'error');
      return undefined;
    }
    await refreshSkills();
    surfaceSkill(skill);
    showToast(t('market.toastPublished', { name: skill.name }), 'success');
    return skill;
  };

  return (
    <TwoColumnLayout>
      {/* Left Rail */}
      <div
        className="flex-shrink-0 bg-layer-01 border-r border-border-subtle-01 flex flex-col min-w-0 overflow-hidden"
        style={{ width: railPanel.width }}
      >
        {/* Blue Point Balance — domain accent만 남겨 장시간 쓰는 작업 화면의 시각 소음을 줄인다. */}
        <div className="p-3">
          <div className="domain-summary domain-summary--skill rounded-xl p-4">
            <div className="mb-2 flex items-center gap-1.5 text-caption-01 text-text-helper">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 2l2.4 7.2H22l-6 4.8 2.4 7.2-6.4-4.8L5.6 21.2 8 14 2 9.2h7.6z" />
              </svg>
              {t('market.railBalanceLabel')}
            </div>
            <div className="text-heading-03 font-semibold text-text-primary leading-none tracking-tight tabular-nums">
              {bluePointBalance.toLocaleString()}
              <span className="text-body-short-01 font-normal text-text-helper ml-1.5">BP</span>
            </div>
            <div className="domain-summary__chip mt-3 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-caption-01">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 19V5M5 12l7-7 7 7" />
              </svg>
              {t('market.railMonthlyChange', { amount: '3,200' })}
            </div>
          </div>
        </div>

        {/* Categories */}
        <div className="text-caption-01 font-semibold uppercase tracking-wider text-text-helper px-4 py-1.5">
          {t('market.railCategoriesTitle')}
        </div>
        <div className="flex-1 overflow-y-auto px-2 space-y-0.5">
          {skillCategoryCodes.map((code) => {
            const isActive = code === selectedCategory;
            return (
              <button
                key={code}
                onClick={() => setSelectedCategory(code)}
                className={`flex w-full items-center text-left border-none px-2.5 py-2 text-body-short-01 rounded-lg transition-colors truncate ${
                  isActive
                    ? 'bg-blue-60/12 text-blue-30 font-semibold ring-1 ring-inset ring-blue-50/25'
                    : 'bg-transparent text-text-secondary hover:bg-blue-60/[0.06] hover:text-text-primary'
                }`}
              >
                <span className="truncate">
                  {getCategoryLabel(code, t)}
                </span>
              </button>
            );
          })}
        </div>

        {/* Upload Skill */}
        <div className="border-t border-border-subtle-01 p-3">
          <button
            onClick={() => setAuthorOpen(true)}
            className="
              w-full flex items-center justify-center gap-2 py-2 rounded-lg
              border border-dashed border-border-strong-01 text-text-secondary
              text-body-short-01 font-semibold transition-colors
              hover:border-blue-50/45 hover:text-blue-30 hover:bg-blue-60/10
            "
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <path d="M17 8l-5-5-5 5M12 3v12" />
            </svg>
            {t('market.railUploadBtn')}
          </button>
        </div>
      </div>

      {/* Rail resize handle */}
      <ResizeHandle
        onPointerDown={railPanel.startResize}
        onDoubleClick={railPanel.resetWidth}
        isResizing={railPanel.isResizing}
        value={railPanel.width} min={railPanel.min} max={railPanel.max} onResizeBy={railPanel.resizeBy}
        aria-label="Resize categories panel"
      />

      {/* Main Content */}
      <div className="flex-1 min-w-0 overflow-y-auto bg-background">
        <MarketplaceHeader
          title={t('market.title')}
          subtitle={t('market.subtitle')}
          searchValue={searchQuery}
          onSearchChange={setSearchQuery}
          sortValue={sortBy}
          sortOptions={sortOptions}
          onSortChange={setSortBy}
        />

        {/* Skills Grid */}
        <div className="px-8 py-5 pb-10">
          {catalogLoading && categoryFilteredSkills.length === 0 ? (
            <CardSkeletonGrid />
          ) : categoryFilteredSkills.length > 0 ? (
            <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(330px, 1fr))' }}>
              {categoryFilteredSkills.map((skill) => {
                const isNew = skill.id === highlightId;
                return (
                  <div
                    key={skill.id}
                    ref={isNew ? highlightRef : undefined}
                    onClick={() => setDetailModalSkill(skill)}
                    className={
                      isNew
                        ? 'rounded-xl ring-2 ring-interactive ring-offset-2 ring-offset-background transition-shadow'
                        : undefined
                    }
                  >
                    <SkillCard
                      {...skill}
                      onInstall={() => setDetailModalSkill(skill)}
                      onSponsor={() => setSponsorModalSkill(skill)}
                    />
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="text-center py-16 text-text-helper">
              <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl border border-blue-50/20 bg-blue-60/10 text-blue-30">
                <Icon name="search" size={22} />
              </div>
              <div className="text-body-long-01">{t('market.emptyTitle')}</div>
              <div className="text-caption-01 mt-1">{t('market.emptyHint')}</div>
            </div>
          )}
        </div>
      </div>

      {/* Sponsor Modal */}
      {sponsorModalSkill && (
        <SponsorModal
          isOpen={true}
          onClose={() => setSponsorModalSkill(null)}
          skillName={sponsorModalSkill.name}
          authorName={sponsorModalSkill.author}
          onSponsor={handleSponsor}
          balance={bluePointBalance}
        />
      )}

      {/* Detail Modal */}
      {detailModalSkill && (
        <ArtifactDetailModal
          isOpen={true}
          onClose={() => setDetailModalSkill(null)}
          artifact={detailModalSkill}
          type="skill"
          onInstall={() => handleInstall(detailModalSkill)}
          onUninstall={() => handleUninstall(detailModalSkill)}
          onSponsor={() => {
            setDetailModalSkill(null);
            setSponsorModalSkill(detailModalSkill);
          }}
        />
      )}

      {/* Skill Authoring / Upload */}
      <SkillAuthorModal
        open={authorOpen}
        onClose={() => setAuthorOpen(false)}
        onPublish={handlePublish}
        onViewInMarket={(skill) => {
          setAuthorOpen(false);
          surfaceSkill(skill);
        }}
      />

    </TwoColumnLayout>
  );
}

export default SkillMarketScreen;

// Made with Bob
