import React, { useMemo, useState } from 'react';
import { SkillFile } from '../../lib/types';
import { Markdown } from './Markdown';
import { FileTypeIcon, FolderIcon } from './FileTypeIcon';
import { Icon } from '../ui';

/**
 * SkillFileBrowser — a compact two-pane file explorer for a skill's bundled
 * files (SKILL.md, README, scripts, license…). Left: file tree. Right: the
 * selected file rendered as lightweight markdown or raw code. Used in the
 * market detail modal; the full-screen source workspace has its own chrome.
 */

export const SkillFileBrowser: React.FC<{ files: SkillFile[] }> = ({ files }) => {
  const [activePath, setActivePath] = useState(files[0]?.path ?? '');
  const [raw, setRaw] = useState(false);
  const [copied, setCopied] = useState(false);

  const active = files.find((f) => f.path === activePath) ?? files[0];

  // Group files into a folder tree (one level deep is enough for skills).
  const tree = useMemo(() => {
    const roots: { name: string; file?: SkillFile }[] = [];
    const folders: Record<string, SkillFile[]> = {};
    for (const f of files) {
      const slash = f.path.indexOf('/');
      if (slash === -1) {
        roots.push({ name: f.path, file: f });
      } else {
        const dir = f.path.slice(0, slash);
        (folders[dir] ??= []).push(f);
      }
    }
    return { roots, folders };
  }, [files]);

  const isMarkdown = active?.language === 'markdown';
  const showRaw = raw || !isMarkdown;

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

  const FileRow = ({ file, indent }: { file: SkillFile; indent?: boolean }) => {
    const on = file.path === activePath;
    const base = file.path.split('/').pop() ?? file.path;
    return (
      <button
        onClick={() => {
          setActivePath(file.path);
          setRaw(false);
        }}
        className={`flex items-center gap-2 w-full text-left rounded-md py-1.5 pr-2 text-body-short-01 transition-colors ${
          indent ? 'pl-7' : 'pl-2'
        } ${
          on
            ? 'bg-layer-selected-01 text-text-primary font-semibold'
            : 'text-text-secondary hover:bg-layer-hover-01 hover:text-text-primary'
        }`}
      >
        <FileTypeIcon path={file.path} />
        <span className="truncate">{base}</span>
      </button>
    );
  };

  return (
    <div className="flex rounded-xl border border-border-subtle-01 overflow-hidden bg-background min-h-[300px]">
      {/* File tree */}
      <div className="w-[200px] flex-shrink-0 border-r border-border-subtle-01 bg-layer-01 p-2 overflow-y-auto">
        <div className="px-2 py-1 text-caption-01 font-semibold uppercase tracking-wider text-text-helper">
          Files
        </div>
        {tree.roots.map((r) => r.file && <FileRow key={r.name} file={r.file} />)}
        {Object.entries(tree.folders).map(([dir, fs]) => (
          <div key={dir}>
            <div className="flex items-center gap-2 px-2 py-1.5 text-body-short-01 text-text-secondary">
              <span className="text-icon-secondary">
                <FolderIcon />
              </span>
              <span className="truncate">{dir}</span>
            </div>
            {fs.map((f) => (
              <FileRow key={f.path} file={f} indent />
            ))}
          </div>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0 flex flex-col bg-background">
        {/* Toolbar */}
        <div className="flex items-center gap-2 px-3.5 py-2 border-b border-border-subtle-01 flex-shrink-0">
          <span className="text-caption-01 font-mono text-text-secondary truncate">
            {active?.path}
          </span>
          <div className="ml-auto flex items-center gap-1">
            {isMarkdown && (
              <div className="flex items-center rounded-md bg-layer-02 p-0.5">
                <button
                  onClick={() => setRaw(false)}
                  title="Preview"
                  className={`px-2 py-1 rounded text-caption-01 transition-colors ${
                    !raw ? 'bg-layer-01 text-text-primary shadow-elevation-1' : 'text-text-helper hover:text-text-primary'
                  }`}
                >
                  Preview
                </button>
                <button
                  onClick={() => setRaw(true)}
                  title="Raw"
                  className={`px-2 py-1 rounded text-caption-01 transition-colors ${
                    raw ? 'bg-layer-01 text-text-primary shadow-elevation-1' : 'text-text-helper hover:text-text-primary'
                  }`}
                >
                  Raw
                </button>
              </div>
            )}
            <button
              onClick={copy}
              title="Copy"
              className="w-7 h-7 flex items-center justify-center rounded-md text-icon-secondary hover:bg-layer-hover-01 hover:text-text-primary transition-colors"
            >
              {copied ? <Icon name="check" size={15} /> : <Icon name="copy" size={15} />}
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-4 max-h-[360px]">
          {active &&
            (showRaw ? (
              <pre className="text-code-02 font-mono leading-5 text-text-primary whitespace-pre-wrap break-words">
                {active.content}
              </pre>
            ) : (
              <Markdown source={active.content} />
            ))}
        </div>
      </div>
    </div>
  );
};

// Made with Bob
