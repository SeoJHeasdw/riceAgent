import { useEffect, useRef, useState } from 'react';
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
import { skillCategories } from '../lib/mockData';
import { Skill } from '../lib/types';

/**
 * SkillMarketScreen - Marketplace for discovering and installing skills
 */

function SkillMarketScreen() {
  const {
    filteredSkills,
    catalogLoading,
    searchQuery,
    setSearchQuery,
    sortBy,
    setSortBy,
    installArtifact,
    uninstallArtifact,
    sponsorSkill,
    publishSkill,
    bluePointBalance,
    setBluePointBalance,
  } = useMarketplace();

  // 설치를 학습 store까지 배선 — 설치한 마켓 스킬이 "내 스킬"에 소유 인스턴스로 등장한다.
  const { installSkillFromMarket } = useLearning();

  const [selectedCategory, setSelectedCategory] = useState('All');
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

  // Filter by category
  const categoryFilteredSkills = filteredSkills.filter(
    (skill) => selectedCategory === 'All' || skill.category === selectedCategory
  );

  // Sort options
  const sortOptions = [
    { value: 'popular', label: '인기순' },
    { value: 'recent', label: '최신순' },
    { value: 'rating', label: '평점순' },
    { value: 'sponsored', label: '후원 많은 순' },
  ];

  // Handle skill installation
  const handleInstall = async (skill: Skill) => {
    setDetailModalSkill(null);
    const job = toast.progress({ label: '다운로드 중', sublabel: skill.name, progress: 0 });

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

    await installArtifact(skill.id, 'skill');
    // 학습 store에 소유 인스턴스로 materialize (origin:installed, upstream 기록).
    installSkillFromMarket(skill);
    job.done('success', '설치 완료');
    // The toast lingers, then animates itself out automatically.
  };

  // Handle skill uninstallation
  const handleUninstall = async (skill: Skill) => {
    setDetailModalSkill(null);
    await uninstallArtifact(skill.id, 'skill');
    showToast(`${skill.name}이(가) 제거되었습니다`, 'info');
  };

  // Handle sponsorship
  const handleSponsor = async (amount: number) => {
    if (sponsorModalSkill) {
      await sponsorSkill(sponsorModalSkill.id, amount);
      setBluePointBalance(bluePointBalance - amount);
      showToast(`${sponsorModalSkill.author}에게 ${amount.toLocaleString()} BP를 후원했습니다`);
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
    const t = setTimeout(() => setHighlightId(null), 2800);
    return () => clearTimeout(t);
  }, [highlightId]);

  // Surface a skill in the grid regardless of current filters (recent + All + no search).
  const surfaceSkill = (skill: Skill) => {
    setSearchQuery('');
    setSelectedCategory('All');
    setSortBy('recent');
    setHighlightId(skill.id);
  };

  // Publish a user-authored skill, then reveal it at the top of the grid.
  const handlePublish = (draft: Parameters<typeof publishSkill>[0]) => {
    const skill = publishSkill(draft);
    surfaceSkill(skill);
    showToast(`${skill.name}을(를) 마켓에 게시했어요`, 'success');
    return skill;
  };

  return (
    <TwoColumnLayout>
      {/* Left Rail */}
      <div
        className="flex-shrink-0 bg-layer-01 border-r border-border-subtle-01 flex flex-col min-w-0 overflow-hidden"
        style={{ width: railPanel.width }}
      >
        {/* Blue Point Balance — Skill Market 전광판, gradient-skill로 3대 요소 색 언어 통일 */}
        <div className="p-3">
          <div className="rounded-xl gradient-skill p-4 text-white border border-blue-50/20 shadow-elevation-1">
            <div className="flex items-center gap-1.5 text-caption-01 text-white/70 mb-2">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 2l2.4 7.2H22l-6 4.8 2.4 7.2-6.4-4.8L5.6 21.2 8 14 2 9.2h7.6z" />
              </svg>
              내 IBM 블루 포인트
            </div>
            <div className="text-heading-03 font-semibold text-white leading-none tracking-tight tabular-nums">
              {bluePointBalance.toLocaleString()}
              <span className="text-body-short-01 font-normal text-white/70 ml-1.5">BP</span>
            </div>
            <div className="inline-flex items-center gap-1 mt-3 px-2 py-0.5 rounded-full bg-white/15 text-caption-01 text-white">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 19V5M5 12l7-7 7 7" />
              </svg>
              이번 달 +3,200
            </div>
          </div>
        </div>

        {/* Categories */}
        <div className="text-caption-01 font-semibold uppercase tracking-wider text-text-helper px-4 py-1.5">
          카테고리
        </div>
        <div className="flex-1 overflow-y-auto px-2 space-y-0.5">
          {skillCategories.map((category) => {
            const isActive = category === selectedCategory;
            return (
              <button
                key={category}
                onClick={() => setSelectedCategory(category)}
                className={`flex w-full items-center text-left border-none px-2.5 py-2 text-body-short-01 rounded-lg transition-colors truncate ${
                  isActive
                    ? 'bg-blue-60/12 text-blue-30 font-semibold ring-1 ring-inset ring-blue-50/25'
                    : 'bg-transparent text-text-secondary hover:bg-blue-60/[0.06] hover:text-text-primary'
                }`}
              >
                <span className="truncate">{category}</span>
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
            내 스킬 업로드
          </button>
        </div>
      </div>

      {/* Rail resize handle */}
      <ResizeHandle
        onPointerDown={railPanel.startResize}
        onDoubleClick={railPanel.resetWidth}
        isResizing={railPanel.isResizing}
        aria-label="Resize categories panel"
      />

      {/* Main Content */}
      <div className="flex-1 min-w-0 overflow-y-auto bg-background">
        <MarketplaceHeader
          title="스킬 마켓"
          subtitle="IBMer가 만든 스킬을 다운로드하고 블루 포인트로 제작자를 후원하세요"
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
              <div className="text-body-long-01">스킬을 찾지 못했습니다</div>
              <div className="text-caption-01 mt-1">검색어나 필터를 조정해보세요</div>
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
