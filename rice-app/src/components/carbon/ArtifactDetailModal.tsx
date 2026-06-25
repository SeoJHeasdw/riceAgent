import React from 'react';
import { Button, Tag, Toggle } from '../ui';
import { dialogBackdropCls } from '../ui/surfaces';
import { Skill, Plugin } from '../../lib/types';
import { getSkillFiles } from '../../lib/skillFiles';
import { SkillFileBrowser } from './SkillFileBrowser';

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
  if (!isOpen || !artifact) return null;

  const isSkill = type === 'skill';
  const skill = isSkill ? (artifact as Skill) : null;
  const plugin = !isSkill ? (artifact as Plugin) : null;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
      {/* Overlay */}
      <div onClick={onClose} className={dialogBackdropCls} />

      {/* Modal */}
      <div className={`relative ${isSkill ? 'w-[860px]' : 'w-[680px]'} max-w-full max-h-[85vh] bg-layer-01 border border-border-subtle-01 rounded-2xl z-[61] shadow-overlay animate-scale-in overflow-hidden flex flex-col`}>
        {/* Header */}
        <div className="flex items-start gap-3.5 px-5 py-5 border-b border-border-subtle-01 flex-shrink-0">
          <div className="relative flex-shrink-0">
            <div
              className={`absolute inset-0 rounded-xl blur-md ${isSkill ? 'bg-interactive/25' : 'bg-[#8a3ffc]/25'}`}
              aria-hidden="true"
            />
            <div
              className={`relative w-11 h-11 flex items-center justify-center rounded-xl ring-1 ring-white/10 shadow-elevation-1 bg-gradient-to-br ${
                isSkill ? 'from-[#4589ff] to-[#0043ce]' : 'from-[#8a3ffc] to-[#6929c4]'
              }`}
            >
              <svg width="22" height="22" viewBox="0 0 32 32" fill="white">
                {isSkill ? (
                  <path d="M30 8h-4.1c-.5-2.3-2.5-4-4.9-4s-4.4 1.7-4.9 4H2v2h14.1c.5 2.3 2.5 4 4.9 4s4.4-1.7 4.9-4H30V8zM21 12c-1.7 0-3-1.3-3-3s1.3-3 3-3 3 1.3 3 3-1.3 3-3 3zM2 24h4.1c.5 2.3 2.5 4 4.9 4s4.4-1.7 4.9-4H30v-2H15.9c-.5-2.3-2.5-4-4.9-4s-4.4 1.7-4.9 4H2v2zm9-4c1.7 0 3 1.3 3 3s-1.3 3-3 3-3-1.3-3-3 1.3-3 3-3z"/>
                ) : (
                  <path d="M16 2a14 14 0 1014 14A14 14 0 0016 2zm0 26a12 12 0 1112-12 12 12 0 01-12 12z"/>
                )}
              </svg>
            </div>
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-heading-04 font-semibold text-text-primary">
                {artifact.name}
              </span>
              {!isSkill && (
                <Tag type="purple" size="sm">
                  솔루션 전문가
                </Tag>
              )}
            </div>
            <div className="text-caption-01 text-text-helper">
              {isSkill && skill ? `${skill.author}${skill.team ? ` · ${skill.team}` : ''}` : ''}
              {!isSkill && plugin ? `제공: ${plugin.provider}` : ''}
            </div>
            {isSkill && skill?.forkedFrom && (
              <div className="mt-1 inline-flex items-center gap-1 text-caption-01 text-text-helper">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="6" cy="6" r="3" /><circle cx="18" cy="6" r="3" /><circle cx="12" cy="18" r="3" />
                  <path d="M6 9v3a3 3 0 003 3h6a3 3 0 003-3V9M12 15v0" />
                </svg>
                원작 <span className="text-text-secondary">{skill.forkedFrom.author}{skill.forkedFrom.team ? ` · ${skill.forkedFrom.team}` : ''}</span> · {skill.author} 개선 (파생본)
              </div>
            )}
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 flex items-center justify-center hover:bg-layer-hover-01 transition-colors flex-shrink-0"
            aria-label="Close"
          >
            <svg width="16" height="16" viewBox="0 0 32 32" fill="currentColor">
              <path d="M24 9.4L22.6 8 16 14.6 9.4 8 8 9.4l6.6 6.6L8 22.6 9.4 24l6.6-6.6 6.6 6.6 1.4-1.4-6.6-6.6L24 9.4z"/>
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5">
          <p className="text-body-long-01 text-text-secondary mb-4.5 leading-relaxed">
            {artifact.description}
          </p>

          {/* Skill-specific content */}
          {isSkill && skill && (
            <>
              {/* Stats */}
              <div className="flex items-center gap-4 mb-4 text-body-short-01 text-text-secondary">
                <span className="flex items-center gap-1.5">
                  <svg width="16" height="16" viewBox="0 0 32 32" fill="currentColor">
                    <path d="M26 24v4H6v-4H4v4a2 2 0 002 2h20a2 2 0 002-2v-4zM26 14l-1.41-1.41L17 20.17V2h-2v18.17l-7.59-7.58L6 14l10 10 10-10z"/>
                  </svg>
                  {skill.downloads.toLocaleString()} 다운로드
                </span>
                <span className="flex items-center gap-1.5">
                  <svg width="16" height="16" viewBox="0 0 32 32" fill="currentColor">
                    <path d="M16 6.52l2.76 5.58.46 1 1.1.16 6.16.89-4.46 4.35-.8.77.19 1.09 1.05 6.13-5.51-2.89L16 23l-.95.5-5.51 2.89 1.05-6.13.19-1.09-.8-.77-4.46-4.35 6.16-.89 1.1-.16.46-1L16 6.52M16 2l-4.55 9.22-10.17 1.47 7.36 7.18L6.9 30l9.1-4.78L25.1 30l-1.74-10.13 7.36-7.18-10.17-1.47L16 2z"/>
                  </svg>
                  {skill.rating} 평점
                </span>
                <span className="flex items-center gap-1.5 text-interactive font-mono">
                  <svg width="16" height="16" viewBox="0 0 32 32" fill="currentColor">
                    <path d="M22.45 6h-7.88l-1.94-4.68A1 1 0 0011.7 1H3.53a1 1 0 00-.93.63L.1 8.31a1 1 0 00.93 1.37h4.67l-2.5 6.54a1 1 0 00.55 1.3 1 1 0 001.3-.55l2.5-6.54h4.67l-2.5 6.54a1 1 0 00.55 1.3 1 1 0 001.3-.55l2.5-6.54h4.67l-2.5 6.54a1 1 0 00.55 1.3 1 1 0 001.3-.55l2.5-6.54h4.67a1 1 0 00.93-1.37l-2.5-6.68A1 1 0 0022.45 6z"/>
                  </svg>
                  {skill.points.toLocaleString()} BP
                </span>
              </div>

              {/* Tags */}
              <div className="mb-5">
                <div className="text-label-01 text-text-secondary mb-2">태그</div>
                <div className="flex flex-wrap gap-2">
                  {skill.tags.map((tag) => (
                    <Tag key={tag} type="gray" size="md">
                      {tag}
                    </Tag>
                  ))}
                </div>
              </div>

              {/* Bundled files (README / SKILL.md / scripts …) */}
              <div className="mb-5">
                <div className="text-label-01 text-text-secondary mb-2">파일</div>
                <SkillFileBrowser files={getSkillFiles(skill)} />
              </div>
            </>
          )}

          {/* Plugin-specific content */}
          {!isSkill && plugin && (
            <>
              {/* Slim composition summary — counts at a glance */}
              <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 mb-4.5">
                {[
                  { label: '스킬', value: plugin.skills?.length ?? plugin.skillCount, accent: 'text-[#a56eff]' },
                  { label: '커넥터', value: plugin.connectors?.length ?? 0, accent: 'text-interactive' },
                  { label: '지식', value: plugin.knowledge?.length ?? 0, accent: 'text-support-success' },
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
                  핵심 역량
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
                    <span className="text-label-01 text-text-secondary uppercase tracking-wider">스킬</span>
                    <span className="text-caption-01 font-mono text-text-helper bg-layer-02 border border-border-subtle-01 rounded-full px-1.5 leading-5">
                      {plugin.skills.length}
                    </span>
                    <span className="ml-auto text-caption-01 text-text-helper">
                      채팅에서 <span className="font-mono text-text-secondary">/</span> 로 호출
                    </span>
                  </div>
                  <div className="flex flex-col gap-px bg-border-subtle-01 rounded-xl overflow-hidden">
                    {plugin.skills.map((s) => (
                      <div key={s.command} className="flex items-start gap-2.5 bg-background px-3.5 py-2.5">
                        <svg width="16" height="16" viewBox="0 0 32 32" fill="currentColor" className="flex-shrink-0 mt-1 text-[#a56eff]">
                          <path d="M30 8h-4.1c-.5-2.3-2.5-4-4.9-4s-4.4 1.7-4.9 4H2v2h14.1c.5 2.3 2.5 4 4.9 4s4.4-1.7 4.9-4H30V8zM21 12c-1.7 0-3-1.3-3-3s1.3-3 3-3 3 1.3 3 3-1.3 3-3 3zM2 24h4.1c.5 2.3 2.5 4 4.9 4s4.4-1.7 4.9-4H30v-2H15.9c-.5-2.3-2.5-4-4.9-4s-4.4 1.7-4.9 4H2v2zm9-4c1.7 0 3 1.3 3 3s-1.3 3-3 3-3-1.3-3-3 1.3-3 3-3z"/>
                        </svg>
                        <div className="flex-1 min-w-0">
                          <code className="inline-block font-mono text-[13px] font-semibold text-[#c4a7ff] bg-[#8a3ffc]/12 rounded-md px-1.5 py-0.5">
                            /{s.command}
                          </code>
                          <div className="text-caption-01 text-text-helper mt-1">{s.desc}</div>
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
                    <span className="text-label-01 text-text-secondary uppercase tracking-wider">연결되는 커넥터</span>
                    <span className="text-caption-01 font-mono text-text-helper bg-layer-02 border border-border-subtle-01 rounded-full px-1.5 leading-5">
                      {plugin.connectors.length}
                    </span>
                  </div>
                  <div className="flex flex-col gap-px bg-border-subtle-01 rounded-xl overflow-hidden">
                    {plugin.connectors.map((c) => (
                      <div key={c.name} className="flex items-start gap-2.5 bg-background px-3.5 py-2.5">
                        <svg width="16" height="16" viewBox="0 0 32 32" fill="currentColor" className="flex-shrink-0 mt-0.5 text-interactive">
                          <path d="M24 21.32a4.66 4.66 0 00-3.69 1.82l-7-3.45a4.6 4.6 0 000-3.38l7-3.45A4.67 4.67 0 1019.33 8a4.65 4.65 0 00.18 1.26l-7 3.45a4.67 4.67 0 100 6.58l7 3.45A4.6 4.6 0 0019.33 24 4.67 4.67 0 1024 21.32z"/>
                        </svg>
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
                    <span className="text-label-01 text-text-secondary uppercase tracking-wider">보유 지식</span>
                    <span className="text-caption-01 font-mono text-text-helper bg-layer-02 border border-border-subtle-01 rounded-full px-1.5 leading-5">
                      {plugin.knowledge.length}
                    </span>
                  </div>
                  <div className="flex flex-col gap-px bg-border-subtle-01 rounded-xl overflow-hidden">
                    {plugin.knowledge.map((k) => (
                      <div key={k.name} className="flex items-start gap-2.5 bg-background px-3.5 py-2.5">
                        <svg width="16" height="16" viewBox="0 0 32 32" fill="currentColor" className="flex-shrink-0 mt-0.5 text-support-success">
                          <path d="M26 4H8a2 2 0 00-2 2v1H4v2h2v6H4v2h2v6H4v2h2v1a2 2 0 002 2h18a2 2 0 002-2V6a2 2 0 00-2-2zm0 22H8v-1h2v-2H8v-6h2v-2H8V9h2V7H8V6h18z"/>
                        </svg>
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
                  활용 사례
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
            버전 {artifact.version}
          </div>
        </div>

        {/* Footer actions */}
        <div className="flex-shrink-0 px-5 py-3.5 border-t border-border-subtle-01 bg-layer-01">
          {artifact.installed ? (
            <div className="flex items-center gap-3">
              {/* Plugin toggle */}
              {!isSkill && onToggle && (
                <>
                  <Toggle checked={artifact.enabled} onChange={onToggle} tone="green" />
                  <span className="flex items-center gap-1.5 flex-1 min-w-0">
                    <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${artifact.enabled ? 'bg-support-success' : 'bg-text-helper'}`} />
                    <span className="text-body-short-01 text-text-secondary truncate">
                      {artifact.enabled ? '에이전트가 사용 중' : '일시정지됨'}
                    </span>
                  </span>
                </>
              )}

              {/* Uninstall button */}
              <Button kind="danger" size="sm" onClick={onUninstall} className="ml-auto">
                <svg width="15" height="15" viewBox="0 0 32 32" fill="currentColor">
                  <path d="M12 12h2v12h-2zm6 0h2v12h-2z"/>
                  <path d="M4 6v2h2v20a2 2 0 002 2h16a2 2 0 002-2V8h2V6zm4 22V8h16v20zm4-26h8v2h-8z"/>
                </svg>
                제거
              </Button>
            </div>
          ) : (
            <div className="flex items-center gap-2.5">
              {isSkill && skill && (
                <span className="text-caption-01 text-text-helper">
                  <span className="font-mono font-semibold text-interactive">{skill.points.toLocaleString()} BP</span> 리워드
                </span>
              )}
              <div className="flex-1" />
              {isSkill && onSponsor && (
                <Button kind="tertiary" size="sm" onClick={onSponsor}>
                  <svg width="15" height="15" viewBox="0 0 32 32" fill="currentColor">
                    <path d="M22.45 6h-7.88l-1.94-4.68A1 1 0 0011.7 1H3.53a1 1 0 00-.93.63L.1 8.31a1 1 0 00.93 1.37h4.67l-2.5 6.54a1 1 0 00.55 1.3 1 1 0 001.3-.55l2.5-6.54h4.67l-2.5 6.54a1 1 0 00.55 1.3 1 1 0 001.3-.55l2.5-6.54h4.67l-2.5 6.54a1 1 0 00.55 1.3 1 1 0 001.3-.55l2.5-6.54h4.67a1 1 0 00.93-1.37l-2.5-6.68A1 1 0 0022.45 6z"/>
                  </svg>
                  후원
                </Button>
              )}
              <Button kind="primary" size="sm" onClick={onInstall}>
                <svg width="15" height="15" viewBox="0 0 32 32" fill="currentColor">
                  <path d="M26 24v4H6v-4H4v4a2 2 0 002 2h20a2 2 0 002-2v-4zM26 14l-1.41-1.41L17 20.17V2h-2v18.17l-7.59-7.58L6 14l10 10 10-10z"/>
                </svg>
                {isSkill ? '스킬 설치' : '전문가 설치'}
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

// Made with Bob