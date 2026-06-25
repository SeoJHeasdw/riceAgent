import React, { useEffect, useMemo, useState } from 'react';
import { Icon } from '../ui';
import type { SkillFile, SkillFileLanguage } from '../../lib/types';
import type { Skill, SkillDetail } from '../../lib/learningData';
import { ownedSkillFiles } from '../../lib/skillBridge';
import { Markdown } from './Markdown';
import { FileTypeIcon, FolderIcon } from './FileTypeIcon';

/**
 * SkillSourceWorkspace — full-screen, IDE-like view of a skill *bundle*
 * (SKILL.md + scripts/ + references/ + assets/ …). A skill is a small repo, not
 * a single prompt, so the source needs the whole canvas — not a cramped pane.
 *
 * 학습 뷰가 홈(비개발자), 여기는 필요할 때 들어오는 소스 작업공간(개발자도 사용).
 * 편집은 학습 레이어가 뭉개지지 않도록 통합 chat-patch(onEdit)로만 흐른다.
 */

// 풀스크린 오버레이가 타이틀바까지 덮으므로, macOS 트래픽 라이트(x:16) 공간을 좌측에 비워둔다 (Layout과 동일).
const isElectron = navigator.userAgent.toLowerCase().includes('electron');

const LANG_LABEL: Record<SkillFileLanguage, string> = {
  markdown: 'Markdown',
  shell: 'Shell',
  text: 'Text',
  python: 'Python',
  html: 'HTML',
  json: 'JSON',
};

// SKILL.md 먼저, 그다음 README, 나머지 알파벳 — 루트 파일 정렬
const rootRank = (name: string) => (/^skill\.md$/i.test(name) ? 0 : /^readme/i.test(name) ? 1 : 2);

export const SkillSourceWorkspace: React.FC<{
  open: boolean;
  skill: Skill;
  detail: SkillDetail;
  onClose: () => void;
  /** 편집 = 통합 chat-patch 드로어 열기 (현재 보고 있는 파일을 대상으로) */
  onEdit: (path: string) => void;
}> = ({ open, skill, detail, onClose, onEdit }) => {
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
        className={`group flex items-center gap-2 w-full text-left rounded-md py-1.5 pr-2 text-body-short-01 transition-colors ${
          indent ? 'pl-7' : 'pl-2.5'
        } ${
          on
            ? 'bg-[#0f62fe]/15 text-text-primary font-semibold'
            : 'text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary'
        }`}
      >
        <FileTypeIcon path={file.path} size={16} />
        <span className="truncate flex-1 min-w-0">{base}</span>
        {isEntry && (
          <span className="text-[10px] font-medium text-link-primary bg-[#0f62fe]/12 rounded px-1 py-px flex-shrink-0">정의</span>
        )}
      </button>
    );
  };

  return (
    <div className="fixed inset-0 z-[55] bg-background flex flex-col animate-fade-in">
      {/* top bar — 닫기는 좌측 "학습으로"(+ESC) 하나로 통일하고, 우측은 편집 대상 파일을 명시한 단일 액션.
          electron에선 위쪽에 트래픽 라이트용 드래그 스트립을 두어, 좌우로 밀지 않고 height로 겹침을 해소한다. */}
      <div className="flex-shrink-0 border-b border-border-subtle-01 bg-layer-01">
        {isElectron && <div className="h-7 w-full electron-drag" />}
        <div className="flex items-center gap-3 px-3 h-12">
        <button
          onClick={onClose}
          title="학습 뷰로 돌아가기 (Esc)"
          className="electron-no-drag inline-flex items-center gap-1.5 h-8 pl-2 pr-3 rounded-lg text-body-short-01 font-semibold text-text-secondary bg-layer-02 border border-border-subtle-01 hover:bg-layer-hover-01 hover:text-text-primary hover:border-border-strong-01 transition-colors flex-shrink-0"
        >
          <Icon name="arrow-right" size={16} className="rotate-180" />
          학습으로
        </button>
        <span className="w-px h-5 bg-border-subtle-01 flex-shrink-0" />
        <div className="w-7 h-7 flex-shrink-0 flex items-center justify-center rounded-lg bg-blue-60">
          <Icon name="skill" size={15} className="text-white" />
        </div>
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-body-short-02 font-semibold text-text-primary truncate">{skill.name}</span>
          <span className="text-caption-01 font-mono text-text-secondary bg-layer-02 border border-border-subtle-01 rounded px-1.5 py-px flex-shrink-0">
            {skill.ver}
          </span>
          <span className="text-caption-01 text-text-helper flex-shrink-0">소스</span>
        </div>
        <div className="flex-1" />
        {/* 편집 = 지금 보고 있는 파일 대상. 어느 파일인지 라벨에 드러낸다. */}
        <button
          onClick={() => active && onEdit(active.path)}
          disabled={!active}
          title={active ? `${active.path.split('/').pop()} 편집` : '편집'}
          className="electron-no-drag inline-flex items-center gap-1.5 h-8 pl-2.5 pr-3 rounded-lg text-body-short-01 font-semibold bg-interactive text-text-on-color hover:bg-[#0353e9] transition-colors flex-shrink-0 disabled:opacity-40 max-w-[260px]"
        >
          <Icon name="edit" size={14} className="flex-shrink-0" />
          <span className="truncate">{active ? `${active.path.split('/').pop()} 편집` : '편집'}</span>
        </button>
        </div>
      </div>

      {/* synthesized notice (learned skills have no real files) */}
      {synthesized && (
        <div className="flex-shrink-0 flex items-center gap-2 px-5 py-2 bg-[#0f62fe]/8 border-b border-border-subtle-01">
          <Icon name="idea" size={14} className="text-icon-primary flex-shrink-0" />
          <span className="text-caption-01 text-text-secondary">
            이 스킬은 대화로 학습돼 <span className="text-text-primary font-semibold">정의가 자동 구성</span>됐어요. 규칙·단계는 학습 뷰에서, 또는 위 <span className="font-semibold">편집</span>으로 다듬을 수 있어요.
          </span>
        </div>
      )}

      {/* body: tree + content */}
      <div className="flex-1 min-h-0 flex">
        {/* tree */}
        <div className="w-[260px] flex-shrink-0 border-r border-border-subtle-01 bg-layer-01 overflow-y-auto py-3 px-2">
          <div className="flex items-center gap-1.5 px-2.5 pb-2">
            <span className="text-caption-01 font-semibold uppercase tracking-wider text-text-helper">구성</span>
            <span className="text-caption-01 text-text-helper">
              · 파일 {files.length}{folderCount > 0 ? ` · 폴더 ${folderCount}` : ''}
            </span>
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
                  className="flex items-center gap-1.5 w-full text-left rounded-md px-2.5 py-1.5 text-body-short-01 text-text-secondary hover:bg-layer-hover-01 transition-colors"
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
          <div className="flex-shrink-0 flex items-center gap-2 px-5 h-11 border-b border-border-subtle-01">
            {active && <FileTypeIcon path={active.path} size={15} />}
            <span className="text-body-short-01 font-mono text-text-secondary truncate">{active?.path}</span>
            {active && (
              <span className="text-caption-01 text-text-helper bg-layer-02 border border-border-subtle-01 rounded px-1.5 py-px flex-shrink-0">
                {LANG_LABEL[active.language]}
              </span>
            )}
            <div className="ml-auto flex items-center gap-1.5 flex-shrink-0">
              {isMarkdown && (
                <div className="flex items-center rounded-md bg-layer-02 p-0.5">
                  <button
                    onClick={() => setRaw(false)}
                    className={`px-2.5 py-1 rounded text-caption-01 transition-colors ${
                      !raw ? 'bg-layer-01 text-text-primary shadow-elevation-1' : 'text-text-helper hover:text-text-primary'
                    }`}
                  >
                    Preview
                  </button>
                  <button
                    onClick={() => setRaw(true)}
                    className={`px-2.5 py-1 rounded text-caption-01 transition-colors ${
                      raw ? 'bg-layer-01 text-text-primary shadow-elevation-1' : 'text-text-helper hover:text-text-primary'
                    }`}
                  >
                    Raw
                  </button>
                </div>
              )}
              <button
                onClick={copy}
                title="복사"
                className="inline-flex items-center gap-1.5 h-7 px-2 rounded-md text-caption-01 text-icon-secondary hover:bg-layer-hover-01 hover:text-text-primary transition-colors"
              >
                <Icon name={copied ? 'check' : 'copy'} size={14} className={copied ? 'text-support-success' : ''} />
                {copied ? '복사됨' : '복사'}
              </button>
            </div>
          </div>

          {/* file body */}
          <div className="flex-1 overflow-y-auto">
            {!active ? (
              <div className="h-full flex items-center justify-center text-body-short-01 text-text-helper">파일이 없어요.</div>
            ) : showRaw ? (
              <pre className="px-6 py-5 text-[0.8125rem] font-mono leading-[1.55] text-text-primary whitespace-pre-wrap break-words">
                {active.content}
              </pre>
            ) : (
              <div className="px-8 py-6 max-w-[820px]">
                <Markdown source={active.content} />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

// Made with Bob
