import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize from 'rehype-sanitize';
import { Icon } from '../ui';

/**
 * Shared Markdown surface for chat answers and skill documents.
 *
 * Parsing is delegated to the CommonMark/GFM ecosystem instead of a handwritten
 * line parser. This keeps tables, nested lists, task lists, autolinks, emphasis,
 * and escaped delimiters correct while the custom components preserve RICE's UI.
 */

const FILE_PATH_RE = /^(?:\.{0,2}\/)?(?:[\w .@()+-]+\/)*[\w .@()+-]+\.(?:txt|md|markdown|json|jsonl|csv|tsv|log|ya?ml|py|js|mjs|cjs|ts|tsx|jsx|sh|bash|zsh|html?|css|scss|sql|toml|ini|env|xml|svg|png|jpe?g|gif|webp|pptx?|docx?|xlsx?|pdf)(?::\d+)?$/i;
const ABSOLUTE_FILE_LINK_RE = /^\/(?!\/).+\.[a-z0-9]{1,8}(?::\d+)?(?:#[^\s]+)?$/i;
const SHELL_LANGS = new Set(['bash', 'sh', 'shell', 'zsh', 'console', 'terminal']);

const cleanFileTarget = (value: string): string => value.replace(/^<|>$/g, '').replace(/#.*$/, '');

const isFileTarget = (value: string): boolean => {
  const clean = cleanFileTarget(value.trim());
  return FILE_PATH_RE.test(clean) || ABSOLUTE_FILE_LINK_RE.test(clean);
};

const splitTableCells = (line: string): string[] => {
  let trimmed = line.trim();
  if (trimmed.startsWith('|')) trimmed = trimmed.slice(1);
  if (trimmed.endsWith('|')) trimmed = trimmed.slice(0, -1);
  return trimmed.split('|').map((cell) => cell.trim());
};

const isTableSeparator = (line: string): boolean => {
  const cells = splitTableCells(line);
  return cells.length > 1 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
};

/**
 * A GFM table cannot be recognized until its separator row is complete. During
 * token streaming, exposing the half-built rows makes raw pipes flash as broken
 * prose. Hold only that unstable tail, while leaving all completed Markdown live.
 */
const stabilizeStreamingSource = (raw: string, streaming: boolean): { source: string; pendingTable: boolean } => {
  const source = raw.replace(/\r\n?/g, '\n');
  if (!streaming || !source) return { source, pendingTable: false };

  const lines = source.split('\n');
  let end = lines.length - 1;
  while (end >= 0 && lines[end].trim() === '') end--;
  if (end < 0 || !lines[end].trimStart().startsWith('|')) return { source, pendingTable: false };

  let start = end;
  while (start > 0 && lines[start - 1].trimStart().startsWith('|')) start--;
  const tableTail = lines.slice(start, end + 1);
  if (tableTail.length < 2 || !isTableSeparator(tableTail[1])) {
    return { source: lines.slice(0, start).join('\n').trimEnd(), pendingTable: true };
  }

  const last = tableTail[tableTail.length - 1].trimEnd();
  if (tableTail.length > 2 && !last.endsWith('|')) {
    return { source: [...lines.slice(0, end), ...lines.slice(end + 1)].join('\n').trimEnd(), pendingTable: true };
  }
  return { source, pendingTable: false };
};

const extractFrontmatter = (raw: string): { body: string; entries: Array<[string, string]> } => {
  const normalized = raw.replace(/\r\n?/g, '\n');
  if (!normalized.startsWith('---\n')) return { body: normalized, entries: [] };
  const closing = normalized.indexOf('\n---', 4);
  if (closing < 0) return { body: normalized, entries: [] };
  const yaml = normalized.slice(4, closing);
  const entries = yaml
    .split('\n')
    .map((line): [string, string] | null => {
      const colon = line.indexOf(':');
      if (colon <= 0) return null;
      return [line.slice(0, colon).trim(), line.slice(colon + 1).trim()];
    })
    .filter((entry): entry is [string, string] => entry !== null);
  return { body: normalized.slice(closing + 4).replace(/^\n/, ''), entries };
};

const Frontmatter: React.FC<{ entries: Array<[string, string]> }> = ({ entries }) => {
  if (entries.length === 0) return null;
  return (
    <dl className="mb-4 grid grid-cols-[minmax(72px,max-content)_1fr] gap-x-3 gap-y-1 rounded-lg border border-border-subtle-01 bg-layer-02 px-3 py-2.5 text-caption-01">
      {entries.map(([label, value], index) => (
        <React.Fragment key={`${label}-${index}`}>
          <dt className="text-text-helper">{label}</dt>
          <dd className="min-w-0 break-words font-mono text-text-secondary">{value}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
};

const CodeBlock: React.FC<{ code: string; language?: string }> = ({ code, language }) => {
  const { t } = useTranslation('common');
  const [copied, setCopied] = useState(false);
  const lang = (language || 'text').toLowerCase();
  const isShell = SHELL_LANGS.has(lang);
  const label = isShell ? 'Bash' : lang === 'text' ? 'Code' : lang.toUpperCase();

  const copy = () => {
    void navigator.clipboard?.writeText(code).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    });
  };

  return (
    <div className="group/code mb-4 overflow-hidden rounded-lg border border-border-subtle-01 bg-layer-02">
      <div className="flex h-9 items-center gap-2 border-b border-border-subtle-01 bg-layer-01 px-3">
        <Icon name={isShell ? 'terminal' : 'document'} size={14} className="text-icon-secondary" />
        <span className="text-label-micro font-semibold uppercase tracking-[0.08em] text-text-helper">{label}</span>
        <button
          type="button"
          onClick={copy}
          className="ml-auto inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-caption-01 text-text-helper transition-colors hover:bg-layer-hover-01 hover:text-text-primary"
          aria-label={t('markdown.codeAriaLabel')}
        >
          <Icon name={copied ? 'check' : 'copy'} size={13} />
          {copied ? t('markdown.codeCopied') : t('markdown.codeCopy')}
        </button>
      </div>
      <pre className="m-0 overflow-x-auto p-3 text-code-02 leading-5 text-text-primary">
        <code className="font-mono">{code.replace(/\n$/, '')}</code>
      </pre>
    </div>
  );
};

// <button>이 아닌 이유: 브라우저는 폼 컨트롤 내부 텍스트를 문서 텍스트 선택에서
// 제외한다 — 답변을 드래그 복사하면 파일명만 통째로 빠진 텍스트가 나온다(2026-07-14
// 실측). 파일 참조는 답변 본문의 일부이므로 선택/복사에 참여하는 요소로 그린다.
const FileChip: React.FC<{ path: string; onFileClick: (path: string) => void }> = ({ path, onFileClick }) => {
  const { t } = useTranslation('common');
  const open = () => onFileClick(cleanFileTarget(path));
  return (
    <a
      role="button"
      tabIndex={0}
      onClick={(e) => {
        e.preventDefault();
        open();
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          open();
        }
      }}
      title={t('markdown.fileOpenTitle', { path })}
      className="mx-0.5 inline-flex max-w-full cursor-pointer items-center gap-1 rounded-md border border-border-subtle-01 bg-layer-02 px-1.5 py-0.5 align-baseline font-mono text-code-02 text-link-primary no-underline transition-colors hover:border-border-strong-01 hover:bg-layer-hover-02"
    >
      <Icon name="document" size={12} className="flex-shrink-0" />
      <span className="truncate">{path}</span>
      <Icon name="arrow-up-right" size={10} className="flex-shrink-0 opacity-60" />
    </a>
  );
};

// 웹 출처를 긴 URL로 쏟지 않고 축약된 칩으로 그린다: globe 아이콘 + 짧은 라벨(제목이
// 있으면 제목, 없으면 호스트명), 전체 URL은 hover title로 노출. WHY: 모델이 조사
// 후 다는 출처가 raw URL이거나 utm 파라미터가 붙어 길게 늘어지면 답변 가독성을
// 해친다 — 검색엔진 인용 칩처럼 압축된 형태로 그린다. favicon 서비스(google/ddg
// 등)로 방문 도메인이 외부에 새는 것을 피하려 아이콘은 로컬 globe로 고정한다.
const URL_TEXT_RE = /^https?:\/\/\S+$/i;

const hostnameOf = (href: string): string => {
  try {
    return new URL(href).hostname.replace(/^www\./, '');
  } catch {
    return href;
  }
};

const nodeToText = (node: React.ReactNode): string =>
  React.Children.toArray(node)
    .map((child) => {
      if (typeof child === 'string' || typeof child === 'number') return String(child);
      if (React.isValidElement(child)) return nodeToText((child as React.ReactElement<{ children?: React.ReactNode }>).props.children);
      return '';
    })
    .join('');

const ExternalLinkChip: React.FC<{ href: string; children: React.ReactNode }> = ({ href, children }) => {
  const host = hostnameOf(href);
  const text = nodeToText(children).trim();
  // 링크 텍스트가 곧 URL(autolink)이거나 비어 있으면 호스트명을, 실제 제목이면 제목을.
  const label = !text || URL_TEXT_RE.test(text) ? host : text;
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      title={href}
      className="mx-0.5 inline-flex max-w-[16rem] items-center gap-1 rounded-full border border-border-subtle-01 bg-layer-02 px-2 py-0.5 align-baseline text-caption-01 text-text-secondary no-underline transition-colors hover:border-border-strong-01 hover:bg-layer-hover-02 hover:text-text-primary"
    >
      <Icon name="globe" size={12} className="flex-shrink-0 text-icon-secondary" />
      <span className="truncate">{label}</span>
      <Icon name="arrow-up-right" size={10} className="flex-shrink-0 opacity-50" />
    </a>
  );
};

export interface MarkdownProps {
  source: string;
  onFileClick?: (path: string) => void;
  /** True only while the current answer is receiving tokens. */
  streaming?: boolean;
}

export const Markdown: React.FC<MarkdownProps> = ({ source, onFileClick, streaming = false }) => {
  const { t } = useTranslation('common');
  const parsed = useMemo(() => {
    const frontmatter = extractFrontmatter(source);
    const stable = stabilizeStreamingSource(frontmatter.body, streaming);
    return { ...stable, entries: frontmatter.entries };
  }, [source, streaming]);

  const components = useMemo<Components>(
    () => ({
      h1: ({ children }) => <h1 className="mb-2.5 mt-1 text-heading-04 font-semibold text-text-primary">{children}</h1>,
      h2: ({ children }) => <h2 className="mb-2 mt-5 text-heading-02 font-semibold text-text-primary">{children}</h2>,
      h3: ({ children }) => <h3 className="mb-1.5 mt-4 text-body-short-02 font-semibold text-text-primary">{children}</h3>,
      h4: ({ children }) => <h4 className="mb-1.5 mt-3 text-body-short-01 font-semibold text-text-primary">{children}</h4>,
      p: ({ children }) => <p className="mb-3 text-body-short-01 leading-relaxed text-text-secondary">{children}</p>,
      strong: ({ children }) => <strong className="font-semibold text-text-primary">{children}</strong>,
      em: ({ children }) => <em className="text-text-secondary">{children}</em>,
      del: ({ children }) => <del className="text-text-helper decoration-text-helper">{children}</del>,
      hr: () => <hr className="my-4 border-border-subtle-01" />,
      blockquote: ({ children }) => (
        <blockquote className="mb-4 rounded-r-md border-l-2 border-link-primary/60 bg-layer-01 py-1 pl-3 pr-2 text-text-secondary [&>p:last-child]:mb-0">
          {children}
        </blockquote>
      ),
      ul: ({ children, className }) => (
        <ul className={`mb-4 space-y-1 pl-5 text-body-short-01 text-text-secondary ${className?.includes('contains-task-list') ? 'list-none pl-1' : 'list-disc'}`}>
          {children}
        </ul>
      ),
      ol: ({ children }) => <ol className="mb-4 list-decimal space-y-1 pl-5 text-body-short-01 text-text-secondary">{children}</ol>,
      li: ({ children, className }) => (
        <li className={`leading-5 marker:text-text-helper [&>p]:mb-1 ${className?.includes('task-list-item') ? 'flex items-start gap-2' : ''}`}>{children}</li>
      ),
      input: ({ type, checked, ...props }) =>
        type === 'checkbox' ? (
          <input type="checkbox" checked={checked} readOnly className="mt-1 h-3.5 w-3.5 accent-[var(--rice-interactive)]" />
        ) : (
          <input type={type} {...props} />
        ),
      a: ({ href = '', children }) => {
        if (onFileClick && isFileTarget(href)) {
          // FileChip과 같은 이유로 <button> 금지 — 선택/복사에서 텍스트가 빠진다.
          return (
            <a
              role="button"
              tabIndex={0}
              onClick={(e) => {
                e.preventDefault();
                onFileClick(cleanFileTarget(decodeURIComponent(href)));
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onFileClick(cleanFileTarget(decodeURIComponent(href)));
                }
              }}
              className="inline-flex cursor-pointer items-center gap-1 rounded px-1 text-link-primary underline decoration-link-primary/35 underline-offset-2 transition-colors hover:bg-layer-hover-01 hover:decoration-link-primary"
              title={t('markdown.fileOpenTitle', { path: href })}
            >
              <Icon name="document" size={12} />
              {children}
              <Icon name="arrow-up-right" size={10} className="opacity-60" />
            </a>
          );
        }
        // 외부 웹 출처는 축약 칩으로 — 긴 URL/제목이 답변에 쏟아지지 않게 한다.
        if (/^https?:\/\//i.test(href)) {
          return <ExternalLinkChip href={href}>{children}</ExternalLinkChip>;
        }
        // mailto:, 페이지 내 앵커(#...), 상대 경로 등은 평범한 링크로 둔다.
        return (
          <a href={href} target="_blank" rel="noreferrer noopener" className="text-link-primary underline decoration-link-primary/40 underline-offset-2 hover:decoration-link-primary">
            {children}
          </a>
        );
      },
      code: ({ className, children }) => {
        const match = /language-([^\s]+)/.exec(className || '');
        const value = String(children);
        if (match || value.includes('\n')) return <CodeBlock code={value} language={match?.[1]} />;
        const inline = value.replace(/\n$/, '');
        if (onFileClick && isFileTarget(inline.trim())) return <FileChip path={inline.trim()} onFileClick={onFileClick} />;
        return <code className="rounded bg-layer-02 px-1.5 py-0.5 font-mono text-code-02 text-link-primary">{inline}</code>;
      },
      pre: ({ children }) => <>{children}</>,
      table: ({ children }) => (
        <div className="mb-4 max-w-full overflow-hidden rounded-lg border border-border-subtle-01 bg-layer-01">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] border-collapse text-caption-01">{children}</table>
          </div>
        </div>
      ),
      thead: ({ children }) => <thead className="bg-layer-02">{children}</thead>,
      tbody: ({ children }) => <tbody className="divide-y divide-border-subtle-01">{children}</tbody>,
      tr: ({ children }) => <tr className="transition-colors hover:bg-layer-hover-01/60">{children}</tr>,
      th: ({ children, style }) => (
        <th style={style} className="border-b border-r border-border-subtle-01 px-3 py-2.5 text-left align-top font-semibold text-text-primary last:border-r-0">
          {children}
        </th>
      ),
      td: ({ children, style }) => (
        <td style={style} className="border-r border-border-subtle-01 px-3 py-2.5 align-top leading-5 text-text-secondary last:border-r-0">
          {children}
        </td>
      ),
    }),
    [onFileClick, t],
  );

  return (
    <div className="rice-markdown min-w-0 [&>*:last-child]:mb-0">
      <Frontmatter entries={parsed.entries} />
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw, rehypeSanitize]} components={components}>
        {parsed.source}
      </ReactMarkdown>
      {parsed.pendingTable && (
        <div className="mb-2 mt-1 inline-flex items-center gap-2 rounded-md border border-border-subtle-01 bg-layer-01 px-2.5 py-1.5 text-caption-01 text-text-helper" role="status">
          <span className="ia-spinner h-3 w-3" aria-hidden="true" />
          {t('markdown.tableBuilding')}
        </div>
      )}
    </div>
  );
};
