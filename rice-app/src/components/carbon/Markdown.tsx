import React from 'react';

/**
 * Markdown — minimal, dependency-free markdown → JSX used by the skill source
 * surfaces (market detail browser + full-screen source workspace). Handles YAML
 * frontmatter, headings, lists, code fences, quotes, hr, and inline `code`/**bold**.
 */

// A workspace-file-looking code chip (e.g. `hello.txt`) → clickable when an
// `onFileClick` handler is supplied (chat answers). Scoped to real file
// extensions so command/identifier chips stay plain.
const FILE_CHIP_RE = /^\.?\/?[\w.-]+\.(txt|md|markdown|json|jsonl|csv|tsv|log|ya?ml|py|js|ts|tsx|jsx|sh|bash|html?|css|scss|sql|toml|ini|env|xml|svg)$/i;

/** Render `inline code`, **bold** segments within a line of text. */
const renderInline = (text: string, keyBase: string, onFileClick?: (path: string) => void): React.ReactNode[] => {
  const parts: React.ReactNode[] = [];
  // split on `code` first
  const codeSplit = text.split(/(`[^`]+`)/g);
  codeSplit.forEach((seg, i) => {
    if (seg.startsWith('`') && seg.endsWith('`')) {
      const inner = seg.slice(1, -1);
      const codeCls = 'font-mono text-[0.8125rem] bg-layer-02 text-link-primary rounded px-1.5 py-0.5';
      if (onFileClick && FILE_CHIP_RE.test(inner.trim())) {
        parts.push(
          <button
            key={`${keyBase}-c${i}`}
            onClick={() => onFileClick(inner)}
            title="파일 열기"
            className={`${codeCls} cursor-pointer hover:underline`}
          >
            {inner}
          </button>
        );
        return;
      }
      parts.push(
        <code key={`${keyBase}-c${i}`} className={codeCls}>
          {inner}
        </code>
      );
      return;
    }
    // bold within the remaining text
    const boldSplit = seg.split(/(\*\*[^*]+\*\*)/g);
    boldSplit.forEach((b, j) => {
      if (b.startsWith('**') && b.endsWith('**')) {
        parts.push(
          <strong key={`${keyBase}-b${i}-${j}`} className="font-semibold text-text-primary">
            {b.slice(2, -2)}
          </strong>
        );
      } else if (b) {
        parts.push(<React.Fragment key={`${keyBase}-t${i}-${j}`}>{b}</React.Fragment>);
      }
    });
  });
  return parts;
};

/** Minimal markdown → JSX. Handles headings, lists, code fences, quotes, hr. */
export const Markdown: React.FC<{ source: string; onFileClick?: (path: string) => void }> = ({ source, onFileClick }) => {
  const blocks: React.ReactNode[] = [];
  const lines = source.replace(/\r\n/g, '\n').split('\n');
  let i = 0;
  let key = 0;

  // Optional YAML frontmatter
  if (lines[0]?.trim() === '---') {
    const fm: string[] = [];
    let j = 1;
    while (j < lines.length && lines[j].trim() !== '---') {
      fm.push(lines[j]);
      j++;
    }
    if (j < lines.length) {
      blocks.push(
        <div key={`fm-${key++}`} className="mb-4 rounded-lg bg-layer-02 border border-border-subtle-01 px-3 py-2.5">
          {fm.map((l, k) => {
            const idx = l.indexOf(':');
            if (idx === -1) return null;
            return (
              <div key={k} className="flex gap-2 text-caption-01 leading-5">
                <span className="text-text-helper min-w-[72px]">{l.slice(0, idx).trim()}</span>
                <span className="text-text-secondary font-mono">{l.slice(idx + 1).trim()}</span>
              </div>
            );
          })}
        </div>
      );
      i = j + 1;
    }
  }

  while (i < lines.length) {
    const line = lines[i];

    // blank
    if (line.trim() === '') {
      i++;
      continue;
    }

    // fenced code block
    if (line.trim().startsWith('```')) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        code.push(lines[i]);
        i++;
      }
      i++; // closing fence
      blocks.push(
        <pre
          key={`code-${key++}`}
          className="mb-4 overflow-x-auto rounded-lg bg-layer-02 border border-border-subtle-01 p-3 text-[0.8125rem] font-mono text-text-primary leading-5"
        >
          {code.join('\n')}
        </pre>
      );
      continue;
    }

    // horizontal rule
    if (/^---+$/.test(line.trim())) {
      blocks.push(<hr key={`hr-${key++}`} className="my-4 border-border-subtle-01" />);
      i++;
      continue;
    }

    // headings
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    if (h) {
      const level = h[1].length;
      const cls =
        level === 1
          ? 'text-heading-04 font-semibold text-text-primary mt-1 mb-2.5'
          : level === 2
          ? 'text-heading-02 font-semibold text-text-primary mt-4 mb-2'
          : 'text-body-short-02 font-semibold text-text-primary mt-3 mb-1.5';
      blocks.push(
        <div key={`h-${key++}`} className={cls}>
          {renderInline(h[2], `h${key}`, onFileClick)}
        </div>
      );
      i++;
      continue;
    }

    // blockquote
    if (line.trim().startsWith('>')) {
      const quote: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        quote.push(lines[i].replace(/^\s*>\s?/, ''));
        i++;
      }
      blocks.push(
        <blockquote
          key={`q-${key++}`}
          className="mb-4 border-l-2 border-link-primary/50 pl-3 text-body-short-01 text-text-secondary italic"
        >
          {renderInline(quote.join(' '), `q${key}`, onFileClick)}
        </blockquote>
      );
      continue;
    }

    // lists (ordered or unordered)
    const isUl = /^\s*[-*]\s+/.test(line);
    const isOl = /^\s*\d+\.\s+/.test(line);
    if (isUl || isOl) {
      const items: string[] = [];
      const re = isUl ? /^\s*[-*]\s+/ : /^\s*\d+\.\s+/;
      while (i < lines.length && (isUl ? /^\s*[-*]\s+/ : /^\s*\d+\.\s+/).test(lines[i])) {
        items.push(lines[i].replace(re, ''));
        i++;
      }
      const ListTag = isOl ? 'ol' : 'ul';
      blocks.push(
        <ListTag
          key={`l-${key++}`}
          className={`mb-4 space-y-1 text-body-short-01 text-text-secondary ${
            isOl ? 'list-decimal' : 'list-disc'
          } pl-5`}
        >
          {items.map((it, k) => (
            <li key={k} className="leading-5 marker:text-text-helper">
              {renderInline(it, `li${key}-${k}`, onFileClick)}
            </li>
          ))}
        </ListTag>
      );
      continue;
    }

    // paragraph (accumulate consecutive plain lines)
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !/^(#{1,3})\s+/.test(lines[i]) &&
      !lines[i].trim().startsWith('```') &&
      !lines[i].trim().startsWith('>') &&
      !/^\s*[-*]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i]) &&
      !/^---+$/.test(lines[i].trim())
    ) {
      para.push(lines[i]);
      i++;
    }
    blocks.push(
      <p key={`p-${key++}`} className="mb-4 text-body-short-01 leading-relaxed text-text-secondary">
        {renderInline(para.join(' '), `p${key}`, onFileClick)}
      </p>
    );
  }

  return <div>{blocks}</div>;
};

// Made with Bob
