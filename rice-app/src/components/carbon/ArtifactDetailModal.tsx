import React, { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Icon, Tag, Toggle } from '../ui';
import { dialogBackdropCls } from '../ui/surfaces';
import { Skill, Plugin, SkillFile, PlatformArtifact } from '../../lib/types';
import { getSkillFiles } from '../../lib/skillFiles';
import { platform } from '../../lib/api';
import { SkillFileBrowser } from './SkillFileBrowser';
import { useDialogA11y } from '../../hooks/useDialogA11y';

/**
 * ArtifactDetailModal - Detailed view for skills and plugins
 */

export interface ArtifactDetailModalProps {
  isOpen: boolean;
  onClose: () => void;
  artifact: Skill | Plugin | null;
  type: 'skill' | 'plugin';
  onInstall: () => void;
  onUninstall: () => void;
  onSponsor?: () => void;
  onToggle?: (enabled: boolean) => void;
}

export const ArtifactDetailModal: React.FC<ArtifactDetailModalProps> = ({
  isOpen,
  onClose,
  artifact,
  type,
  onInstall,
  onUninstall,
  onSponsor,
  onToggle,
}) => {
  const { t: tSkills } = useTranslation('skills');
  const { t: tPlugins } = useTranslation('plugins');
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useDialogA11y({ open: isOpen && !!artifact, dialogRef, onClose });
  const isSkill = type === 'skill';
  const skill = isSkill && artifact ? (artifact as Skill) : null;
  const userPublishedSkill = !!skill?.userPublished;
  // 플랫폼 스킬(id: "platform-{id}")은 실제 SKILL.md를 platform 서버에서 가져온다.
  const isPlatformSkill = !!skill && skill.id.startsWith('platform-');

  // 플랫폼 스킬 파일 lazy load — modal이 열릴 때 GET /artifacts/{id} → /files/{path}.
  // mock/제작 스킬은 메타데이터에서 합성한 파일(getSkillFiles)을 그대로 쓴다.
  // null = 로딩 중, [] = 표시할 파일 없음.
  const [platformFiles, setPlatformFiles] = useState<SkillFile[] | null>(null);
  // 실제 아티팩트 메타(license·번들 리소스·버전) — 헤더/메타 스트립에서 "진짜 패키지"임을 보여준다.
  const [platformArtifact, setPlatformArtifact] = useState<PlatformArtifact | null>(null);
  useEffect(() => {
    if (!isOpen || !skill || !isPlatformSkill) {
      setPlatformFiles(null);
      setPlatformArtifact(null);
      return;
    }
    let cancelled = false;
    const artifactId = skill.id.slice('platform-'.length);
    void platform.getArtifact(artifactId).then(async (res) => {
      if (cancelled) return;
      if (!res.success || !res.data) {
        setPlatformFiles([]);
        return;
      }
      setPlatformArtifact(res.data);
      const files = await platform.getArtifactFiles(res.data);
      if (!cancelled) setPlatformFiles(files);
    });
    return () => {
      cancelled = true;
    };
    // skill 객체 전체가 아닌 정체성(id)에만 반응 — 부모 리렌더로 새 객체가 와도 불필요한 재요청 방지.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, isPlatformSkill, skill?.id]);

  if (!isOpen || !artifact) return null;

  const plugin = !isSkill ? (artifact as Plugin) : null;
  const builtinPlugin = !!plugin?.builtin;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
      {/* Overlay */}
      <div onClick={onClose} className={dialogBackdropCls} aria-hidden="true" />

      {/* Modal */}
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`relative ${isSkill ? 'w-[840px]' : 'w-[680px]'} max-w-full max-h-[82vh] bg-layer-01 border border-border-subtle-01 rounded-xl z-[61] shadow-elevation-3 animate-scale-in overflow-hidden flex flex-col`}
      >
        <span
          aria-hidden="true"
          className={`absolute left-0 right-0 top-0 h-0.5 ${isSkill ? 'bg-blue-60' : 'bg-purple-60'}`}
        />
        {/* Header */}
        <div className={`flex items-start gap-3.5 px-5 py-4 border-b border-border-subtle-01 flex-shrink-0 ${
          isSkill ? 'bg-blue-60/[0.035]' : 'bg-purple-60/[0.035]'
        }`}>
          <div
            className={`flex-shrink-0 w-11 h-11 flex items-center justify-center rounded-lg border ${
              isSkill
                ? 'bg-blue-60/12 border-blue-50/25 text-blue-30'
                : 'bg-purple-60/12 border-purple-50/25 text-purple-40'
            }`}
          >
            {isSkill ? (
              <Icon name="skill" size={22} />
            ) : (
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                <circle cx="12" cy="12" r="9" />
                <circle cx="12" cy="12" r="3.5" />
              </svg>
            )}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <h2 id={titleId} className={`${isSkill ? 'text-heading-03' : 'text-heading-04'} font-semibold text-text-primary`}>
                {artifact.name}
              </h2>
              {!isSkill && (
                <Tag type="purple" size="sm">
                  {tPlugins('detail.expertTag')}
                </Tag>
              )}
            </div>
            <div className="text-caption-01 text-text-helper">
              {isSkill && skill ? `${skill.author}${skill.team ? ` · ${skill.team}` : ''} · v${skill.version}` : ''}
              {!isSkill && plugin ? tPlugins('detail.providerMeta', { provider: plugin.provider, version: plugin.version }) : ''}
            </div>
            {isSkill && skill?.forkedFrom && (
              <div className="mt-1 inline-flex items-center gap-1 text-caption-01 text-text-helper">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="6" cy="6" r="3" /><circle cx="18" cy="6" r="3" /><circle cx="12" cy="18" r="3" />
                  <path d="M6 9v3a3 3 0 003 3h6a3 3 0 003-3V9M12 15v0" />
                </svg>
                {tSkills('market.detail.forkedFrom')}{' '}<span className="text-text-secondary">{skill.forkedFrom.author}{skill.forkedFrom.team ? ` · ${skill.forkedFrom.team}` : ''}</span>{' '}· {skill.author} {tSkills('market.detail.forkedImprovement')}
              </div>
            )}
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-icon-secondary hover:text-icon-primary hover:bg-layer-hover-01 transition-colors flex-shrink-0"
            aria-label={tSkills('market.detail.closeAriaLabel')}
          >
            <Icon name="close" size={16} />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          <p className="text-body-long-01 text-text-secondary mb-4 leading-relaxed">
            {artifact.description}
          </p>

          {/* Skill-specific content */}
          {isSkill && skill && (
            <>
              {/* Stats */}
              <div className="grid grid-cols-3 gap-2 mb-4">
                {[
                  { label: tSkills('market.detail.statDownloads'), value: skill.downloads.toLocaleString(), icon: <Icon name="download" size={15} />, tone: 'text-text-primary' },
                  {
                    label: tSkills('market.detail.statRating'),
                    value: skill.rating > 0 ? skill.rating : tSkills('market.detail.statRatingNew'),
                    icon: (
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M12 2.5l2.9 5.9 6.5.95-4.7 4.6 1.1 6.5L12 17.4l-5.8 3.05 1.1-6.5-4.7-4.6 6.5-.95L12 2.5z" />
                      </svg>
                    ),
                    tone: 'text-text-primary',
                  },
                  {
                    label: tSkills('market.detail.statReward'),
                    value: `${skill.points.toLocaleString()} BP`,
                    icon: (
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" />
                      </svg>
                    ),
                    tone: 'text-blue-30',
                  },
                ].map((stat) => (
                  <div key={stat.label} className="rounded-lg border border-border-subtle-01 bg-background px-3 py-2">
                    <div className="flex items-center gap-1.5 text-caption-01 text-text-helper">
                      {stat.icon}
                      {stat.label}
                    </div>
                    <div className={`mt-1 text-body-short-01 font-semibold tabular-nums ${stat.tone}`}>
                      {stat.value}
                    </div>
                  </div>
                ))}
              </div>

              {/* Tags */}
              <div className="mb-4">
                <div className="text-label-01 text-text-secondary mb-2">{tSkills('market.detail.sectionTags')}</div>
                <div className="flex flex-wrap gap-2">
                  {skill.tags.map((tag) => (
                    <Tag key={tag} type="blue" size="sm" className="bg-blue-60/12 text-blue-30 border border-blue-50/20">
                      {tag}
                    </Tag>
                  ))}
                </div>
              </div>

              {/* 패키지 메타 — 실제 아티팩트의 license·번들 리소스(platform skill). "진짜 패키지" 신호. */}
              {platformArtifact && (platformArtifact.license || platformArtifact.bundled_resources.length > 0) && (
                <div className="mb-5 flex flex-wrap items-center gap-2">
                  {platformArtifact.license && (
                    <Tag type="gray" size="sm">{tSkills('market.detail.packageLicense', { license: platformArtifact.license })}</Tag>
                  )}
                  {platformArtifact.bundled_resources.map((r) => (
                    <Tag key={r} type="outline" size="sm">{r}/</Tag>
                  ))}
                </div>
              )}

              {/* Bundled files (README / SKILL.md / scripts …) */}
              <div className="mb-4">
                <div className="flex items-center gap-2 mb-2">
                  <div className="text-label-01 text-text-secondary">{tSkills('market.detail.sectionFiles')}</div>
                  <span className="text-caption-01 text-text-helper">{tSkills('market.detail.sectionFilesHint')}</span>
                </div>
                {isPlatformSkill ? (
                  platformFiles === null ? (
                    <div className="px-1 py-3 text-caption-01 text-text-helper">{tSkills('market.detail.filesLoading')}</div>
                  ) : platformFiles.length ? (
                    // key로 강제 remount → SkillFileBrowser의 초기 선택이 첫 파일로 잡힌다.
                    <SkillFileBrowser key={platformFiles.map((f) => f.path).join('|')} files={platformFiles} />
                  ) : (
                    <div className="px-1 py-3 text-caption-01 text-text-helper">{tSkills('market.detail.filesEmpty')}</div>
                  )
                ) : (
                  <SkillFileBrowser files={getSkillFiles(skill)} />
                )}
              </div>
            </>
          )}

          {/* Plugin-specific content */}
          {!isSkill && plugin && (
            <>
              {/* Slim composition summary — counts at a glance */}
              <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 mb-4.5">
                {[
                  { label: tPlugins('detail.sectionSkills'), value: plugin.skills?.length ?? plugin.skillCount, accent: 'text-blue-50' },
                  { label: tPlugins('detail.sectionConnectors'), value: plugin.connectors?.length ?? 0, accent: 'text-teal-40' },
                  { label: tPlugins('detail.sectionKnowledge'), value: plugin.knowledge?.length ?? 0, accent: 'text-magenta-50' },
                ].map((s) => (
                  <span key={s.label} className="inline-flex items-baseline gap-1.5">
                    <span className={`text-heading-02 font-semibold tabular-nums ${s.accent}`}>{s.value}</span>
                    <span className="text-caption-01 text-text-helper">{s.label}</span>
                  </span>
                ))}
              </div>

              {/* Capabilities (gains) — compact tag row */}
              <div className="mb-5">
                <div className="text-label-01 text-text-secondary uppercase tracking-wider mb-2">
                  {tPlugins('detail.sectionCapabilities')}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {plugin.gains.split('·').map((gain, i) => (
                    <Tag key={i} type="purple" size="sm">
                      {gain.trim()}
                    </Tag>
                  ))}
                </div>
              </div>

              {/* Skills — invoked with "/" in chat */}
              {plugin.skills && plugin.skills.length > 0 && (
                <div className="mb-5">
                  <div className="flex items-center gap-2 mb-2.5">
                    <span className="text-label-01 text-text-secondary uppercase tracking-wider">{tPlugins('detail.sectionSkills')}</span>
                    <span className="text-caption-01 font-mono text-text-helper bg-layer-02 border border-border-subtle-01 rounded-full px-1.5 leading-5">
                      {plugin.skills.length}
                    </span>
                    <span className="ml-auto text-caption-01 text-text-helper">
                      {tPlugins('detail.sectionSkillsHint')}
                    </span>
                  </div>
                  <div className="flex flex-col gap-px bg-border-subtle-01 rounded-xl overflow-hidden">
                    {plugin.skills.map((s) => (
                      <div key={s.command} className="flex items-start gap-2.5 bg-background px-3.5 py-2.5">
                        <Icon name="skill" size={16} className="flex-shrink-0 mt-1 text-blue-50" />
                        <div className="flex-1 min-w-0">
                          <code className="inline-block font-mono text-body-short-01 font-semibold text-blue-40 bg-blue-60/12 rounded-md px-1.5 py-0.5">
                            /{s.command}
                          </code>
                          {s.desc && <div className="text-caption-01 text-text-helper mt-1">{s.desc}</div>}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Connectors — data/tools the agent can reach */}
              {plugin.connectors && plugin.connectors.length > 0 && (
                <div className="mb-5">
                  <div className="flex items-center gap-2 mb-2.5">
                    <span className="text-label-01 text-text-secondary uppercase tracking-wider">{tPlugins('detail.sectionConnectors')}</span>
                    <span className="text-caption-01 font-mono text-text-helper bg-layer-02 border border-border-subtle-01 rounded-full px-1.5 leading-5">
                      {plugin.connectors.length}
                    </span>
                  </div>
                  <div className="flex flex-col gap-px bg-border-subtle-01 rounded-xl overflow-hidden">
                    {plugin.connectors.map((c) => (
                      <div key={c.name} className="flex items-start gap-2.5 bg-background px-3.5 py-2.5">
                        <Icon name="connect" size={16} className="flex-shrink-0 mt-0.5 text-teal-40" />
                        <div className="flex-1 min-w-0">
                          <div className="text-body-short-01 font-semibold text-text-primary">{c.name}</div>
                          {c.desc && <div className="text-caption-01 text-text-helper mt-0.5">{c.desc}</div>}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Knowledge — sources the agent draws on */}
              {plugin.knowledge && plugin.knowledge.length > 0 && (
                <div className="mb-5">
                  <div className="flex items-center gap-2 mb-2.5">
                    <span className="text-label-01 text-text-secondary uppercase tracking-wider">{tPlugins('detail.sectionKnowledge')}</span>
                    <span className="text-caption-01 font-mono text-text-helper bg-layer-02 border border-border-subtle-01 rounded-full px-1.5 leading-5">
                      {plugin.knowledge.length}
                    </span>
                  </div>
                  <div className="flex flex-col gap-px bg-border-subtle-01 rounded-xl overflow-hidden">
                    {plugin.knowledge.map((k) => (
                      <div key={k.name} className="flex items-start gap-2.5 bg-background px-3.5 py-2.5">
                        <Icon name="document" size={16} className="flex-shrink-0 mt-0.5 text-magenta-50" />
                        <div className="flex-1 min-w-0">
                          <div className="text-body-short-01 font-semibold text-text-primary">{k.name}</div>
                          <div className="text-caption-01 text-text-helper mt-0.5">{k.desc}</div>
                        </div>
                        {k.meta && (
                          <span className="text-caption-01 font-mono text-text-helper flex-shrink-0 mt-0.5">{k.meta}</span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Use cases */}
              <div className="mb-5">
                <div className="text-label-01 text-text-secondary uppercase tracking-wider mb-2.5">
                  {tPlugins('detail.sectionUseCases')}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {plugin.useCases.map((useCase) => (
                    <Tag key={useCase} type="blue" size="md">
                      {useCase}
                    </Tag>
                  ))}
                </div>
              </div>
            </>
          )}

          {/* Version info */}
          <div className="text-caption-01 text-text-helper">
            {tSkills('market.detail.versionLabel', { version: artifact.version })}
          </div>
        </div>

        {/* Footer actions */}
        <div className="flex-shrink-0 px-5 py-3.5 border-t border-border-subtle-01 bg-layer-01">
          {artifact.installed || userPublishedSkill ? (
            <div className="flex items-center gap-3">
              {/* Plugin toggle */}
              {!isSkill && onToggle && (
                <>
                  <Toggle
                    checked={artifact.enabled}
                    onChange={onToggle}
                    tone="purple"
                    aria-label={artifact.enabled
                      ? tSkills('market.detail.toggleAriaDisable', { name: artifact.name })
                      : tSkills('market.detail.toggleAriaEnable', { name: artifact.name })}
                  />
                  <span className="flex items-center gap-1.5 flex-1 min-w-0">
                    <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${artifact.enabled ? 'bg-support-success' : 'bg-text-helper'}`} />
                    <span className="text-body-short-01 text-text-secondary truncate">
                      {artifact.enabled ? tSkills('market.detail.toggleActiveLabel') : tSkills('market.detail.togglePausedLabel')}
                    </span>
                  </span>
                </>
              )}

              {isSkill && (
                <span className="flex items-center gap-1.5 text-body-short-01 text-text-secondary">
                  <span className="w-1.5 h-1.5 rounded-full bg-blue-50" />
                  {userPublishedSkill
                    ? tSkills('market.detail.publishedManage')
                    : tSkills('market.detail.installedManage')}
                </span>
              )}

              {builtinPlugin ? (
                <span className="ml-auto text-caption-01 text-text-helper">
                  {tPlugins('detail.builtinNote')}
                </span>
              ) : (
                <Button
                  kind="danger-ghost"
                  size="sm"
                  onClick={onUninstall}
                  className="ml-auto"
                  icon={<Icon name="trash" size={14} />}
                >
                  {userPublishedSkill
                    ? tSkills('market.detail.unpublishBtn')
                    : tPlugins('detail.uninstallBtn')}
                </Button>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-2.5">
              {isSkill && skill && (
                <span className="text-caption-01 text-text-helper">
                  <span className="font-mono font-semibold text-interactive">{skill.points.toLocaleString()} BP</span>{' '}{tSkills('market.detail.rewardSuffix')}
                </span>
              )}
              <div className="flex-1" />
              {isSkill && onSponsor && (
                <Button
                  kind="secondary"
                  size="sm"
                  onClick={onSponsor}
                  icon={
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" />
                    </svg>
                  }
                >
                  {tSkills('market.detail.sponsorBtn')}
                </Button>
              )}
              <Button kind="primary" size="sm" onClick={onInstall} icon={<Icon name="download" size={14} />}>
                {isSkill ? tSkills('market.detail.installBtn') : tPlugins('detail.installBtn')}
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

// Made with Bob
