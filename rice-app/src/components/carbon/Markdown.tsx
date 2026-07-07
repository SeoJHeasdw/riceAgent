import React, { useEffect, useRef, useState } from 'react';

/**
 * Markdown — minimal, dependency-free markdown → JSX used by the skill source
 * surfaces (market detail browser + full-screen source workspace). Handles YAML
 * frontmatter, headings, lists, tables, code fences, quotes, hr, and inline
 * `code`/**bold**.
 */

// A workspace-file-looking code chip (e.g. `hello.txt`) → clickable when an
// `onFileClick` handler is supplied (chat answers). Scoped to real file
// extensions so command/identifier chips stay plain.
const FILE_CHIP_RE = /^\.?\/?[\w.-]+\.(txt|md|markdown|json|jsonl|csv|tsv|log|ya?ml|py|js|ts|tsx|jsx|sh|bash|html?|css|scss|sql|toml|ini|env|xml|svg|pptx?|docx?|xlsx?|pdf)$/i;

/** Render `inline code`, **bold** segments within a line of text. */
const renderInline = (text: string, keyBase: string, onFileClick?: (path: string) => void): React.ReactNode[] => {
  const parts: React.ReactNode[] = [];
  // split on `code` first
  const codeSplit = text.split(/(`[^`]+`)/g);
  codeSplit.forEach((seg, i) => {
    if (seg.startsWith('`') && seg.endsWith('`')) {
      const inner = seg.slice(1, -1);
      const codeCls = 'font-mono text-code-02 bg-layer-02 text-link-primary rounded px-1.5 py-0.5';
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

const splitTableCells = (line: string): string[] => {
  let trimmed = line.trim();
  if (trimmed.startsWith('|')) trimmed = trimmed.slice(1);
  if (trimmed.endsWith('|')) trimmed = trimmed.slice(0, -1);
  return trimmed.split('|').map((cell) => cell.trim());
};

const isTableRowLine = (line: string): boolean => /^\s*\|.*\|\s*$/.test(line);

const isTableSeparatorLine = (line: string): boolean => {
  const cells = splitTableCells(line);
  return cells.length > 1 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
};

const stripCellMarkup = (text: string): string =>
  text
    .replace(/\s*<br\s*\/?>\s*/gi, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .trim();

const clamp = (n: number, min: number, max: number): number => Math.min(max, Math.max(min, n));

const isCompactTableColumn = (header: string, values: string[]): boolean => {
  const label = stripCellMarkup(header).toLowerCase();
  if (['#', 'no', 'no.', 'id'].includes(label)) return true;
  return values.every((value) => /^[\d.\-#\s]{1,5}$/.test(stripCellMarkup(value)));
};

const estimateColumnWidth = (header: string, values: string[], columnCount: number): number => {
  if (isCompactTableColumn(header, values)) return 56;

  const label = stripCellMarkup(header).toLowerCase();
  const content = [header, ...values].map(stripCellMarkup);
  const longest = Math.max(...content.map((value) => value.length), 0);

  if (/title|제목|name|이름/.test(label)) return clamp(longest * 9 + 32, 150, 240);
  if (/script|본문|message|핵심|summary|설명|내용|포인트/.test(label)) return clamp(longest * 8 + 32, 220, 360);
  if (/visual|시각|제안|note|비고/.test(label)) return clamp(longest * 8 + 32, 160, 280);

  const maxWidth = columnCount >= 5 ? 300 : 380;
  return clamp(longest * 8 + 32, 120, maxWidth);
};

const TableCellContent: React.FC<{ text: string; cellKey: string; onFileClick?: (path: string) => void }> = ({
  text,
  cellKey,
  onFileClick,
}) => {
  const lines = text
    .split(/\s*<br\s*\/?>\s*/gi)
    .map((line) => line.trim())
    .filter(Boolean);
  const bulletLines = lines.filter((line) => /^[-*]\s+/.test(line));

  if (lines.length > 1 && bulletLines.length >= Math.max(2, lines.length - 1)) {
    return (
      <ul className="m-0 list-disc space-y-1 pl-4">
        {lines.map((line, i) => {
          const item = line.replace(/^[-*]\s+/, '');
          return (
            <li key={`${cellKey}-li${i}`} className="leading-5 marker:text-text-helper">
              {renderInline(item, `${cellKey}-${i}`, onFileClick)}
            </li>
          );
        })}
      </ul>
    );
  }

  return (
    <span className="space-y-1">
      {lines.length === 0
        ? null
        : lines.map((part, i) => (
            <React.Fragment key={`${cellKey}-br${i}`}>
              {i > 0 && <br />}
              {renderInline(part, `${cellKey}-${i}`, onFileClick)}
            </React.Fragment>
          ))}
    </span>
  );
};

const MarkdownTable: React.FC<{
  columns: string[];
  rows: string[][];
  onFileClick?: (path: string) => void;
}> = ({ columns, rows, onFileClick }) => {
  const tableSignature = JSON.stringify({ columns, rows });
  const modelRef = useRef<{
    signature: string;
    compactColumns: boolean[];
    initialWidths: number[];
  } | null>(null);

  if (modelRef.current?.signature !== tableSignature) {
    const columnValues = columns.map((_, col) => rows.map((row) => row[col] ?? ''));
    const compactColumns = columns.map((column, col) => isCompactTableColumn(column, columnValues[col] ?? []));
    modelRef.current = {
      signature: tableSignature,
      compactColumns,
      initialWidths: columns.map((column, col) => estimateColumnWidth(column, columnValues[col] ?? [], columns.length)),
    };
  }

  const { compactColumns, initialWidths } = modelRef.current!;
  const [widthState, setWidthState] = useState(() => ({ signature: tableSignature, widths: initialWidths }));
  const dragRef = useRef<{ col: number; startX: number; startWidth: number } | null>(null);
  const widths = widthState.signature === tableSignature ? widthState.widths : initialWidths;
  const totalWidth = widths.reduce((sum, width) => sum + width, 0);
  const stickyFirstColumn = columns.length > 4 && compactColumns[0];

  useEffect(() => {
    if (widthState.signature !== tableSignature) {
      setWidthState({ signature: tableSignature, widths: initialWidths });
    }
  }, [initialWidths, tableSignature, widthState.signature]);

  const startResize = (event: React.PointerEvent<HTMLButtonElement>, col: number) => {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = { col, startX: event.clientX, startWidth: widths[col] };
  };

  const moveResize = (event: React.PointerEvent<HTMLButtonElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    const nextWidth = clamp(drag.startWidth + event.clientX - drag.startX, compactColumns[drag.col] ? 44 : 96, 520);
    setWidthState((current) => ({
      signature: tableSignature,
      widths: (current.signature === tableSignature ? current.widths : initialWidths).map((width, col) =>
        col === drag.col ? nextWidth : width
      ),
    }));
  };

  const stopResize = (event: React.PointerEvent<HTMLButtonElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    dragRef.current = null;
  };

  const nudgeColumnWidth = (col: number, delta: number) => {
    setWidthState((current) => ({
      signature: tableSignature,
      widths: (current.signature === tableSignature ? current.widths : initialWidths).map((width, idx) =>
        idx === col ? clamp(width + delta, compactColumns[col] ? 44 : 96, 520) : width
      ),
    }));
  };

  return (
    <div className="mb-4 max-w-full overflow-hidden rounded-lg border border-border-subtle-01 bg-layer-01">
      <div className="overflow-x-auto">
        <table className="table-fixed border-collapse text-caption-01" style={{ width: totalWidth }}>
          <colgroup>
            {widths.map((width, col) => (
              <col key={col} style={{ width }} />
            ))}
          </colgroup>
          <thead>
            <tr className="bg-layer-02">
              {columns.map((column, col) => (
                <th
                  key={col}
                  className={`group relative border-b border-r border-border-subtle-01 bg-layer-02 px-3 py-2 text-left align-top font-semibold text-text-primary last:border-r-0 ${
                    stickyFirstColumn && col === 0 ? 'sticky left-0 z-20 shadow-[1px_0_0_var(--cds-border-subtle-01)]' : ''
                  }`}
                >
                  <div className="truncate pr-2" title={stripCellMarkup(column)}>
                    <TableCellContent text={column} cellKey={`th-${col}`} onFileClick={onFileClick} />
                  </div>
                  {col < columns.length - 1 && (
                    <button
                      type="button"
                      aria-label={`${stripCellMarkup(column)} 컬럼 너비 조절`}
                      className="absolute right-0 top-0 h-full w-2 translate-x-1 cursor-col-resize touch-none border-none bg-transparent outline-none before:absolute before:left-1/2 before:top-2 before:h-[calc(100%-16px)] before:w-px before:-translate-x-1/2 before:bg-border-strong-01 before:opacity-0 hover:before:opacity-100 focus-visible:before:opacity-100 active:before:opacity-100"
                      onPointerDown={(event) => startResize(event, col)}
                      onPointerMove={moveResize}
                      onPointerUp={stopResize}
                      onPointerCancel={stopResize}
                      onKeyDown={(event) => {
                        if (event.key === 'ArrowLeft') nudgeColumnWidth(col, -16);
                        if (event.key === 'ArrowRight') nudgeColumnWidth(col, 16);
                      }}
                    />
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex} className="border-t border-border-subtle-01 first:border-t-0 hover:bg-layer-hover-01/60">
                {row.map((cell, col) => {
                  const compact = compactColumns[col];
                  return (
                    <td
                      key={col}
                      className={`border-r border-border-subtle-01 px-3 py-2 align-top leading-5 text-text-secondary last:border-r-0 ${
                        stickyFirstColumn && col === 0 ? 'sticky left-0 z-10 bg-layer-01 shadow-[1px_0_0_var(--cds-border-subtle-01)]' : ''
                      } ${
                        compact ? 'text-center font-mono text-text-helper' : ''
                      }`}
                    >
                      <TableCellContent text={cell} cellKey={`td-${rowIndex}-${col}`} onFileClick={onFileClick} />
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

/** Minimal markdown → JSX. Handles headings, lists, tables, code fences, quotes, hr. */
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
          className="mb-4 overflow-x-auto rounded-lg bg-layer-02 border border-border-subtle-01 p-3 text-code-02 font-mono text-text-primary leading-5"
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

    // GitHub-flavored markdown tables
    if (isTableRowLine(line) && i + 1 < lines.length && isTableSeparatorLine(lines[i + 1])) {
      const columns = splitTableCells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && isTableRowLine(lines[i])) {
        const row = splitTableCells(lines[i]);
        rows.push(columns.map((_, col) => row[col] ?? ''));
        i++;
      }

      blocks.push(<MarkdownTable key={`t-${key++}-${columns.length}-${rows.length}`} columns={columns} rows={rows} onFileClick={onFileClick} />);
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
      !(isTableRowLine(lines[i]) && i + 1 < lines.length && isTableSeparatorLine(lines[i + 1])) &&
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
