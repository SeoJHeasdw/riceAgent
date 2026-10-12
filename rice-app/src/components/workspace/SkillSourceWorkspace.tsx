import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../ui';
import type { SkillFile, SkillFileLanguage } from '../../lib/types';
import type { Skill, SkillDetail } from '../../lib/learningData';
import { ownedSkillFiles } from '../../lib/skillBridge';
import { Markdown } from './Markdown';
import { FileTypeIcon, FolderIcon } from './FileTypeIcon';

/**
 * SkillSourceWorkspace — full-screen source view for SKILL.md and any bundled
 * files an imported marketplace skill may carry.
 *
 * 학습 뷰가 홈(비개발자), 여기는 필요할 때 들어오는 소스 작업공간(개발자도 사용).
 * 편집은 학습 레이어가 뭉개지지 않도록 통합 chat-patch(onEdit)로만 흐른다.
 */

// 풀스크린 오버레이가 타이틀바까지 덮으므로, macOS 트래픽 라이트(x:16) 공간을 좌측에 비워둔다 (Layout과 동일).
const isElectron = navigator.userAgent.toLowerCase().includes('electron');

// SKILL.md 먼저, 그다음 README, 나머지 알파벳 — 루트 파일 정렬
const rootRank = (name: string) => (/^skill\.md$/i.test(name) ? 0 : /^readme/i.test(name) ? 1 : 2);

// 언어 식별자는 고유명사 — 번역 대상 아님
const LANG_LABEL: Record<SkillFileLanguage, string> = {
  markdown: 'Markdown',
  shell: 'Shell',
  text: 'Text',
  python: 'Python',
  html: 'HTML',
  json: 'JSON',
};

export const SkillSourceWorkspace: React.FC<{
  open: boolean;
  skill: Skill;
  detail: SkillDetail;
  onClose: () => void;
  /** 편집 = 통합 chat-patch 드로어 열기 (현재 보고 있는 파일을 대상으로).
   *  생략하면 읽기 전용 — 빌트인 스킬처럼 편집이 허용되지 않는 소스 뷰. */
  onEdit?: (path: string) => void;
}> = ({ open, skill, detail, onClose, onEdit }) => {
  const { t } = useTranslation('skills');

  const { files, synthesized } = useMemo(() => ownedSkillFiles(skill, detail), [skill, detail]);

  const { roots, folders } = useMemo(() => {
    const roots: SkillFile[] = [];
    const folders: Record<string, SkillFile[]> = {};
    for (const f of files) {
      const slash = f.path.indexOf('/');
      if (slash === -1) roots.push(f);
      else (folders[f.path.slice(0, slash)] ??= []).push(f);
    }
    roots.sort((a, b) => rootRank(a.path) - rootRank(b.path) || a.path.localeCompare(b.path));
    return { roots, folders };
  }, [files]);

  const [activePath, setActivePath] = useState(roots[0]?.path ?? files[0]?.path ?? '');
  const [raw, setRaw] = useState(false);
  const [copied, setCopied] = useState(false);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  // 스킬이 바뀌거나 다시 열면 첫 파일(보통 SKILL.md)로 리셋
  useEffect(() => {
    if (!open) return;
    setActivePath(roots[0]?.path ?? files[0]?.path ?? '');
    setRaw(false);
    setCollapsed({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skill.id, open]);

  // ESC로 닫기
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', h);
    return () => document.removeEventListener('keydown', h);
  }, [open, onClose]);

  if (!open) return null;

  const active = files.find((f) => f.path === activePath) ?? files[0];
  const isMarkdown = active?.language === 'markdown';
  const showRaw = raw || !isMarkdown;
  const folderCount = Object.keys(folders).length;

  // 파일 경로에 따른 역할 라벨/설명 — 렌더링 시 t()로 현재 언어 적용
  const fileRole = (path: string): { label: string; description: string } => {
    const name = path.split('/').pop()?.toLowerCase() ?? path.toLowerCase();
    if (name === 'skill.md') return { label: t('sourceWorkspace.roleSkillMdLabel'), description: t('sourceWorkspace.roleSkillMdDesc') };
    if (name.startsWith('readme')) return { label: t('sourceWorkspace.roleReadmeLabel'), description: t('sourceWorkspace.roleReadmeDesc') };
    if (path.startsWith('scripts/') || /\.(sh|py)$/i.test(path)) return { label: t('sourceWorkspace.roleScriptLabel'), description: t('sourceWorkspace.roleScriptDesc') };
    if (/\.(html|htm)$/i.test(path)) return { label: t('sourceWorkspace.roleHtmlLabel'), description: t('sourceWorkspace.roleHtmlDesc') };
    if (/\.json$/i.test(path)) return { label: t('sourceWorkspace.roleJsonLabel'), description: t('sourceWorkspace.roleJsonDesc') };
    return { label: t('sourceWorkspace.roleDefaultLabel'), description: t('sourceWorkspace.roleDefaultDesc') };
  };

  const activeRole = active ? fileRole(active.path) : null;

  const copy = async () => {
    if (!active) return;
    try {
      await navigator.clipboard.writeText(active.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };

  const FileRow: React.FC<{ file: SkillFile; indent?: boolean }> = ({ file, indent }) => {
    const on = file.path === activePath;
    const base = file.path.split('/').pop() ?? file.path;
    const isEntry = /^skill\.md$/i.test(base);
    return (
      <button
        onClick={() => {
          setActivePath(file.path);
          setRaw(false);
        }}
        className={`group flex items-center gap-2 w-full text-left rounded-lg py-2 pr-2.5 text-body-short-01 transition-colors ${
          indent ? 'pl-8' : 'pl-2.5'
        } ${
          on
            ? 'bg-layer-selected-01 text-text-primary font-semibold'
            : 'text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary'
        }`}
      >
        <FileTypeIcon path={file.path} size={16} />
        <span className="truncate flex-1 min-w-0">{base}</span>
        {isEntry && <span className="text-label-micro font-medium text-link-primary flex-shrink-0">{t('sourceWorkspace.coreFileTag')}</span>}
      </button>
    );
  };

  return (
    <div className="fixed inset-0 z-[55] bg-background flex flex-col animate-fade-in">
      {/* 저장소를 개발자 IDE가 아니라 스킬의 '구성'으로 설명하는 상단 바. */}
      <div className="flex-shrink-0 border-b border-border-subtle-01 bg-layer-01">
        {isElectron && <div className="h-7 w-full electron-drag" />}
        <div className="flex h-12 items-center gap-2.5 px-4">
        <button
          onClick={onClose}
          title={t('sourceWorkspace.backTitle')}
          aria-label={t('sourceWorkspace.backAriaLabel')}
          className="electron-no-drag inline-flex h-8 w-8 items-center justify-center rounded-lg text-text-secondary bg-layer-02 border border-border-subtle-01 hover:bg-layer-hover-01 hover:text-text-primary hover:border-border-strong-01 transition-colors flex-shrink-0"
        >
          <Icon name="arrow-right" size={15} className="rotate-180" />
        </button>
        <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border border-blue-50/25 bg-blue-60/15">
          <Icon name="folder" size={16} className="text-blue-30" />
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-body-short-02 font-semibold text-text-primary truncate">{skill.name}</span>
            <span className="text-caption-01 font-mono text-text-helper">{skill.ver}</span>
          </div>
          <div className="text-caption-01 text-text-helper">
            {folderCount > 0
              ? t('sourceWorkspace.headerSummaryWithFolders', { count: files.length, folders: folderCount })
              : t('sourceWorkspace.headerSummary', { count: files.length })}
          </div>
        </div>
        <div className="flex-1" />
        {/* 편집 = 지금 보고 있는 파일 대상. 어느 파일인지 라벨에 드러낸다.
            onEdit 미제공(빌트인 등) 시 읽기 전용 배지로 대체한다. */}
        {onEdit ? (
          <button
            onClick={() => active && onEdit(active.path)}
            disabled={!active}
            title={active ? t('sourceWorkspace.editBtnTitle', { path: active.path }) : t('sourceWorkspace.editBtnLabel')}
            className="electron-no-drag inline-flex items-center gap-2 h-8 px-3 rounded-lg text-body-short-01 font-semibold bg-interactive text-text-on-color hover:bg-interactive-hover transition-colors flex-shrink-0 disabled:opacity-40 max-w-[300px]"
          >
            <Icon name="sparkle" size={14} className="flex-shrink-0" />
            <span className="truncate">{t('sourceWorkspace.editBtnLabel')}</span>
          </button>
        ) : (
          <span
            title={t('sourceWorkspace.readOnlyTitle')}
            className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg text-caption-01 font-semibold text-text-helper bg-layer-02 border border-border-subtle-01 flex-shrink-0"
          >
            <Icon name="lock" size={13} />
            {t('sourceWorkspace.readOnlyLabel')}
          </span>
        )}
        </div>
      </div>

      {/* 학습형 스킬은 파일이 자동 구성됐다는 provenance만 조용히 알린다. */}
      {synthesized && (
        <div className="flex-shrink-0 flex items-center gap-2 px-5 py-2 bg-interactive/8 border-b border-border-subtle-01">
          <Icon name="idea" size={14} className="text-icon-primary flex-shrink-0" />
          <span className="text-caption-01 text-text-secondary">
            {t('sourceWorkspace.synthesizedBanner')}
          </span>
        </div>
      )}

      {/* body: tree + content */}
      <div className="flex-1 min-h-0 flex">
        {/* tree */}
        <div className="w-[296px] flex-shrink-0 border-r border-border-subtle-01 bg-layer-01 overflow-y-auto p-3">
          <div className="mb-3 rounded-xl border border-border-subtle-01 bg-background p-3">
            <div className="flex items-center gap-2 text-body-short-01 font-semibold text-text-primary">
              <Icon name="skill" size={15} className="text-blue-30" />
              {t('sourceWorkspace.treeSectionTitle')}
            </div>
            <p className="mt-1.5 text-caption-01 leading-4 text-text-helper">
              {t('sourceWorkspace.treeSectionBody', { skillMd: 'SKILL.md' })}
            </p>
          </div>
          <div className="flex items-center gap-1.5 px-2.5 pb-2">
            <span className="text-caption-01 font-semibold text-text-secondary">{t('sourceWorkspace.filesLabel')}</span>
            <span className="ml-auto text-caption-01 text-text-helper">{files.length}</span>
          </div>
          {roots.map((f) => (
            <FileRow key={f.path} file={f} />
          ))}
          {Object.entries(folders).map(([dir, fs]) => {
            const isCollapsed = collapsed[dir];
            return (
              <div key={dir} className="mt-0.5">
                <button
                  onClick={() => setCollapsed((c) => ({ ...c, [dir]: !c[dir] }))}
                  className="flex items-center gap-1.5 w-full text-left rounded-lg px-2.5 py-2 text-body-short-01 text-text-secondary hover:bg-layer-hover-01 transition-colors"
                >
                  <Icon name="chevron-down" size={13} className={`text-icon-secondary transition-transform ${isCollapsed ? '-rotate-90' : ''}`} />
                  <span className="text-icon-secondary">
                    <FolderIcon open={!isCollapsed} size={15} />
                  </span>
                  <span className="truncate flex-1 min-w-0">{dir}</span>
                  <span className="text-caption-01 text-text-helper flex-shrink-0">{fs.length}</span>
                </button>
                {!isCollapsed && fs.map((f) => <FileRow key={f.path} file={f} indent />)}
              </div>
            );
          })}
        </div>

        {/* content */}
        <div className="flex-1 min-w-0 flex flex-col bg-background">
          {/* file toolbar */}
          <div className="flex h-[54px] flex-shrink-0 items-center gap-2.5 border-b border-border-subtle-01 bg-layer-01 px-4">
            {active && <FileTypeIcon path={active.path} size={15} />}
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-body-short-01 font-mono font-semibold text-text-primary truncate">{active?.path}</span>
                {active && <span className="text-label-micro text-text-helper">{LANG_LABEL[active.language]}</span>}
              </div>
              {activeRole && <div className="text-caption-01 text-text-helper truncate">{activeRole.label} · {activeRole.description}</div>}
            </div>
            <div className="ml-auto flex items-center gap-1.5 flex-shrink-0">
              {isMarkdown && (
                <div className="flex items-center rounded-md bg-layer-02 p-0.5">
                  <button
                    onClick={() => setRaw(false)}
                    className={`px-2.5 py-1 rounded text-caption-01 transition-colors ${
                      !raw ? 'bg-layer-01 text-text-primary shadow-elevation-1' : 'text-text-helper hover:text-text-primary'
                    }`}
                  >
                    {'Preview'}
                  </button>
                  <button
                    onClick={() => setRaw(true)}
                    className={`px-2.5 py-1 rounded text-caption-01 transition-colors ${
                      raw ? 'bg-layer-01 text-text-primary shadow-elevation-1' : 'text-text-helper hover:text-text-primary'
                    }`}
                  >
                    {'Source'}
                  </button>
                </div>
              )}
              <button
                onClick={copy}
                title="Copy"
                className="inline-flex items-center gap-1.5 h-7 px-2 rounded-md text-caption-01 text-icon-secondary hover:bg-layer-hover-01 hover:text-text-primary transition-colors"
              >
                <Icon name={copied ? 'check' : 'copy'} size={14} className={copied ? 'text-support-success' : ''} />
                {copied ? 'Copied!' : 'Copy'}
              </button>
            </div>
          </div>

          {/* file body */}
          <div className="flex-1 overflow-y-auto">
            {!active ? (
              <div className="h-full flex items-center justify-center text-body-short-01 text-text-helper">{t('sourceWorkspace.emptyFile')}</div>
            ) : showRaw ? (
              <pre className="mx-auto max-w-[980px] px-10 py-8 text-code-02 font-mono leading-6 text-text-primary whitespace-pre-wrap break-words">
                {active.content}
              </pre>
            ) : (
              <div className="mx-auto max-w-[920px] px-10 py-8">
                <Markdown source={active.content} />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
