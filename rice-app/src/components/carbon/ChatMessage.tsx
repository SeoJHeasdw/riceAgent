import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Button, CompanionCloudIcon, Icon, Tag } from '../ui';
import { connectorIdForScope, type ThreadMessage, type ToolCall, type SkillRef, type EditDiffRow } from '../../lib/learningData';
import type { PlanStepData } from '../../lib/types';
import { useFileViewer } from '../../lib/fileViewer/FileViewerContext';
import { localizeLearningWarning } from '../../lib/learningWarnings';
import { Markdown } from './Markdown';

// A workspace-file-looking inline code chip (e.g. `hello.txt`, `./src/a.ts`) →
// rendered clickable to open the shared file viewer. Tightly scoped to real file
// extensions so command/identifier chips (`db2.query`) stay plain.
const FILE_CHIP_RE = /^\.?\/?[\w.-]+\.(txt|md|markdown|json|jsonl|csv|tsv|log|ya?ml|py|js|ts|tsx|jsx|sh|bash|html?|css|scss|sql|toml|ini|env|xml|svg|pptx?|docx?|xlsx?|pdf)$/i;

/** 앱 언어 코드 → Intl 로케일 태그 */
const LOCALE_MAP: Record<string, string> = { ko: 'ko-KR', en: 'en-US' };

/**
 * epoch ms → 시간 라벨 (언어 기반 포맷).
 * 앱 언어를 locale 파라미터로 받아 언어 변경 즉시 다시 렌더된다.
 * undefined/0은 빈 문자열로 조용히 처리.
 */
const formatTime = (ms: number | undefined, locale: string): string => {
  if (!ms) return '';
  const intlLocale = LOCALE_MAP[locale] ?? locale;
  const d = new Date(ms);
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  const sameYear = d.getFullYear() === now.getFullYear();
  const time = d.toLocaleTimeString(intlLocale, { hour: '2-digit', minute: '2-digit', hour12: false });
  if (sameDay) return time;
  if (sameYear)
    return d.toLocaleDateString(intlLocale, { month: 'numeric', day: 'numeric' }) + ' ' + time;
  return d.toLocaleDateString(intlLocale, { year: 'numeric', month: 'numeric', day: 'numeric' }) + ' ' + time;
};

/**
 * ChatMessage — renders one thread message. Handles the new learning-loop
 * message kinds: multi-step tool-call cards, the memory-written indicator,
 * skill-learned tiles, plus the per-answer action bar (copy / feedback /
 * regenerate / correct-to-teach) and clickable used-skill badges.
 */

// ── AI label (gradient-border pill, mirrors the design-system AILabel) ──
const AILabel: React.FC = () => (
  <span className="ai-label flex-shrink-0">
    <span className="ai-label__text">AI</span>
  </span>
);

// ── inline rich text — renders `backtick` spans as mono code chips ─────────
// 본격 마크다운이 아니라 인라인 코드만 (chat1/chat3 의 `localhost:8080` 류 하이라이트).
const Rich: React.FC<{ text: string }> = ({ text }) => {
  const { openFile } = useFileViewer();
  const { t } = useTranslation('chat');
  const chipCls =
    'font-mono text-code-02 bg-layer-02 text-blue-40 border border-border-subtle-01 rounded px-1 py-px';
  return (
    <>
      {text.split(/(`[^`]+`)/g).map((part, i) => {
        if (part.startsWith('`') && part.endsWith('`') && part.length > 1) {
          const inner = part.slice(1, -1);
          if (FILE_CHIP_RE.test(inner.trim())) {
            return (
              <button
                key={i}
                onClick={() => openFile(inner)}
                title={t('message.openFile')}
                className={`${chipCls} hover:bg-layer-hover-02 hover:underline cursor-pointer`}
              >
                {inner}
              </button>
            );
          }
          return (
            <code key={i} className={chipCls}>
              {inner}
            </code>
          );
        }
        return <React.Fragment key={i}>{part}</React.Fragment>;
      })}
    </>
  );
};

const stripPlanningBreadcrumb = (text: string): string =>
  text.replace(/^(?:\s*\(planning…\)\s*\n?)+/u, '').replace(/^(?:\s*\(planning\.\.\.\)\s*\n?)+/u, '');

const AnimatedEllipsis: React.FC<{ className?: string }> = ({ className = '' }) => (
  <span className={`rice-ellipsis ${className}`} aria-hidden="true">
    <span>.</span>
    <span>.</span>
    <span>.</span>
  </span>
);

// ── streaming typing indicator ───────────────────────────────
// Shown in an assistant bubble that has no text yet (live SSE turn in flight),
// so the user can tell RICE is generating a response and isn't stuck.
const TypingDots: React.FC = () => {
  const { t } = useTranslation('chat');
  return (
    <span className="inline-flex min-w-[160px] py-1" aria-label={t('message.generating')}>
      <span className="text-caption-01 text-text-helper">
        {t('message.generating')}
        <AnimatedEllipsis />
      </span>
    </span>
  );
};

// ── avatar ───────────────────────────────────────────────────
const Avatar: React.FC<{ kind: 'user' | 'agent' }> = ({ kind }) => {
  const agent = kind === 'agent';
  if (agent) {
    return (
      <span className="flex h-7 w-8 flex-shrink-0 items-center justify-center text-text-secondary" aria-hidden="true">
        <CompanionCloudIcon className="h-7 w-8" />
      </span>
    );
  }

  return (
    <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg border border-border-subtle-02 bg-layer-03 font-mono text-caption-01 font-semibold text-text-secondary">
      U
    </div>
  );
};

// ── status glyph for a tool step ─────────────────────────────
const StepStatus: React.FC<{ status: ToolCall['status'] }> = ({ status }) => {
  const { t } = useTranslation('chat');
  if (status === 'running') {
    return <span className="ia-spinner w-3.5 h-3.5 flex-shrink-0" aria-label={t('message.running')} />;
  }
  if (status === 'error') {
    return <Icon name="warning" size={14} filled className="text-support-error flex-shrink-0" title={t('message.error')} />;
  }
  return <Icon name="check-circle" size={14} className="text-support-success flex-shrink-0" title={t('message.done')} />;
};

// ── one tool step (expandable: params + result) ──────────────
const ToolStep: React.FC<{ c: ToolCall }> = ({ c }) => {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const { t } = useTranslation('chat');
  return (
    <div className="border-t border-border-subtle-01 first:border-t-0">
      <div
        onClick={() => setOpen((o) => !o)}
        role="button"
        className="flex items-center gap-2.5 px-3 py-2.5 cursor-pointer hover:bg-layer-hover-01 transition-colors"
      >
        <StepStatus status={c.status} />
        <span className="text-caption-01 font-mono text-blue-40 flex-shrink-0">{c.tool}</span>
        <span className="text-caption-01 text-text-secondary truncate">{c.label}</span>
        {c.scope && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              const cid = connectorIdForScope(c.scope!.name);
              navigate(cid ? `/connectors/${cid}` : '/connectors');
            }}
            title={t('message.viewConnector', { name: c.scope.name, mode: c.scope.mode })}
            className="inline-flex items-center gap-1 flex-shrink-0 border border-border-subtle-01 px-1.5 py-0.5 rounded text-label-micro font-mono text-text-helper hover:bg-layer-hover-02 hover:text-text-secondary transition-colors"
          >
            <Icon name="lock" size={10} className="opacity-70" />
            {c.scope.name} · {c.scope.mode}
          </button>
        )}
        <span className="ml-auto text-caption-01 text-text-helper font-mono flex-shrink-0">{c.time}</span>
        <Icon name={open ? 'chevron-up' : 'chevron-down'} size={14} className="text-icon-secondary flex-shrink-0" />
      </div>
      {open && (
        <div className="pl-9 pr-3 pb-3 pt-0.5 flex flex-col gap-2">
          <div>
            <div className="text-label-micro uppercase tracking-wider text-text-helper mb-1">parameters</div>
            <div className="bg-layer-01 border border-border-subtle-01 rounded-md px-2.5 py-2 flex flex-col gap-1">
              {Object.entries(c.params).map(([k, v]) => (
                <div key={k} className="flex gap-2.5 text-label-micro font-mono">
                  <span className="text-text-helper min-w-[64px] flex-shrink-0">{k}</span>
                  <span className="text-text-primary break-all">{String(v)}</span>
                </div>
              ))}
            </div>
          </div>
          <div>
            <div className="text-label-micro uppercase tracking-wider text-text-helper mb-1">result</div>
            <div className="text-caption-01 font-mono text-text-secondary flex items-center gap-1.5">
              <Icon name="arrow-right" size={12} />
              {c.result}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

// ── tool-call card (multi-step run) ──────────────────────────
const ToolCallCard: React.FC<{ m: Extract<ThreadMessage, { kind: 'toolrun' }>; onJumpSkill?: (id: string) => void }> = ({
  m,
  onJumpSkill,
}) => {
  const [open, setOpen] = useState(!!m.open);
  const running = m.status === 'running';
  const pct = m.reuse ? Math.round((1 - m.reuse.toSteps / m.reuse.fromSteps) * 100) : null;
  const { t } = useTranslation('chat');

  return (
    <div className="my-1.5 max-w-[640px]">
      {/* reuse banner — proves the loop made it faster */}
      {m.reuse && (
        <div className="flex items-center gap-2 px-3 py-1.5 bg-layer-02 border border-border-subtle-01 border-b-0 border-l-2 border-l-blue-50 rounded-t-lg">
          <Icon name="renew" size={14} className="text-icon-secondary flex-shrink-0" />
          <span className="text-caption-01 text-text-secondary">
            {t('message.usedSkillReuse')}{' '}
            <span className="text-text-primary font-semibold">
              {m.reuse.fromSteps} steps → {m.reuse.toSteps} steps
            </span>
          </span>
          <span className="ml-auto flex-shrink-0">
            <Tag type="green" size="sm">
              {t('message.fasterByPct', { pct })}
            </Tag>
          </span>
        </div>
      )}

      {/* header */}
      <div
        onClick={() => setOpen((o) => !o)}
        role="button"
        className={`flex items-center gap-2.5 px-3 py-2.5 cursor-pointer bg-layer-01 border border-border-subtle-01 hover:bg-layer-hover-01 transition-colors ${
          m.reuse ? '' : 'rounded-t-lg'
        } ${open ? 'border-b-0' : 'rounded-b-lg'}`}
      >
        <StepStatus status={running ? 'running' : 'done'} />
        <span className="text-body-short-01 font-semibold text-text-primary">{m.label}</span>
        {m.usedSkill && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onJumpSkill?.(m.usedSkill!.id);
            }}
            title={t('message.jumpToSkill')}
            className="inline-flex items-center gap-1 flex-shrink-0 border border-border-subtle-01 rounded px-1.5 py-0.5 text-caption-01 text-text-secondary hover:bg-layer-hover-02 transition-colors"
          >
            <Icon name="skill" size={12} />
            {m.usedSkill.label} {m.usedSkill.ver}
          </button>
        )}
        <span className="ml-auto text-caption-01 text-text-helper font-mono flex-shrink-0">
          {running ? t('message.toolRunning') : `${m.steps} steps · ${m.time}`}
        </span>
        <Icon name={open ? 'chevron-up' : 'chevron-down'} size={16} className="text-icon-secondary flex-shrink-0" />
      </div>

      {/* steps */}
      {open && (
        <div className="border border-border-subtle-01 border-t-0 rounded-b-lg bg-background overflow-hidden">
          {m.calls.map((c, i) => (
            <ToolStep key={i} c={c} />
          ))}
        </div>
      )}
    </div>
  );
};

// ── bash tool card (셸 명령 — IN/OUT, chat1 의 Bash 블록) ─────
const BashCard: React.FC<{ m: Extract<ThreadMessage, { kind: 'tool_bash' }> }> = ({ m }) => {
  const [open, setOpen] = useState(!m.collapsed);
  const running = m.status === 'running';
  const { t } = useTranslation('chat');
  return (
    <div className="my-1.5 max-w-[640px] border border-border-subtle-01 rounded-lg overflow-hidden bg-layer-01">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border-subtle-01">
        <Icon name="terminal" size={14} className="text-icon-secondary flex-shrink-0" />
        <span className="text-caption-01 font-semibold text-text-secondary flex-shrink-0">Bash</span>
        <span className="text-caption-01 text-text-helper truncate">{m.desc}</span>
        {running ? (
          <span className="ia-spinner w-3.5 h-3.5 ml-auto flex-shrink-0" aria-label={t('message.running')} />
        ) : m.status === 'error' ? (
          <Icon name="warning" size={13} filled className="text-support-error ml-auto flex-shrink-0" title={t('message.error')} />
        ) : (
          <Icon name="check-circle" size={13} className="text-support-success ml-auto flex-shrink-0" title={t('message.done')} />
        )}
      </div>
      <div className="font-mono text-label-micro leading-[18px]">
        <div className="flex gap-2.5 px-3 py-1.5">
          <span className="text-text-helper select-none w-7 flex-shrink-0">IN</span>
          <span className="text-text-primary whitespace-pre-wrap break-all">{m.cmd}</span>
        </div>
        {m.out !== undefined &&
          (open ? (
            <div className="flex gap-2.5 px-3 py-1.5 border-t border-border-subtle-01 bg-background">
              <span className="text-text-helper select-none w-7 flex-shrink-0">OUT</span>
              <span className="text-text-secondary whitespace-pre-wrap break-all">{m.out}</span>
            </div>
          ) : (
            <button
              onClick={() => setOpen(true)}
              className="flex items-center gap-1.5 w-full px-3 py-1.5 border-t border-border-subtle-01 text-caption-01 text-link-primary hover:bg-layer-hover-01 transition-colors"
            >
              <Icon name="chevron-down" size={13} /> {t('message.viewOutput')}
            </button>
          ))}
      </div>
    </div>
  );
};

// ── read tool card (파일/지식 읽기 — 파일 + 요약, chat1 의 Read 블록) ─
const ReadCard: React.FC<{ m: Extract<ThreadMessage, { kind: 'tool_read' }> }> = ({ m }) => (
  <div className="my-1.5 max-w-[640px] flex items-center gap-2 px-3 py-2 border border-border-subtle-01 rounded-lg bg-layer-01">
    <Icon name="document" size={14} className="text-icon-secondary flex-shrink-0" />
    <span className="text-caption-01 font-semibold text-text-secondary flex-shrink-0">Read</span>
    <span className="text-caption-01 font-mono text-blue-40 truncate flex-shrink-0 max-w-[46%]">{m.file}</span>
    <span className="text-caption-01 text-text-helper truncate">— {m.summary}</span>
    {m.meta && <span className="ml-auto text-label-micro font-mono text-text-helper flex-shrink-0">{m.meta}</span>}
  </div>
);

// ── one row of a side-by-side file-edit diff (chat2 의 Edit split diff) ─
const HATCH: React.CSSProperties = {
  backgroundImage: 'repeating-linear-gradient(45deg, transparent 0 5px, rgba(255,255,255,0.035) 5px 6px)',
};
const EditRow: React.FC<{ row: EditDiffRow }> = ({ row }) => {
  const leftEmpty = row.t === 'add';
  const rightEmpty = row.t === 'remove';
  const leftCls = row.t === 'remove' || row.t === 'change' ? 'bg-diff-remove-bg text-diff-remove-text' : 'text-text-helper';
  const rightCls = row.t === 'add' || row.t === 'change' ? 'bg-diff-add-bg text-diff-add-text' : 'text-text-helper';
  return (
    <div className="grid grid-cols-2">
      <div
        className={`px-2.5 py-0.5 border-r border-border-subtle-01 whitespace-pre-wrap break-all ${leftEmpty ? '' : leftCls}`}
        style={leftEmpty ? HATCH : undefined}
      >
        {row.before ?? ' '}
      </div>
      <div
        className={`px-2.5 py-0.5 whitespace-pre-wrap break-all ${rightEmpty ? '' : rightCls}`}
        style={rightEmpty ? HATCH : undefined}
      >
        {row.after ?? ' '}
      </div>
    </div>
  );
};

// ── edit tool card (파일 편집 — before/after split diff) ───────
const EditCard: React.FC<{ m: Extract<ThreadMessage, { kind: 'tool_edit' }> }> = ({ m }) => {
  const [open, setOpen] = useState(!m.collapsed);
  return (
    <div className="my-1.5 max-w-[640px] border border-border-subtle-01 rounded-lg overflow-hidden bg-layer-01">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 w-full px-3 py-2 text-left hover:bg-layer-hover-01 transition-colors"
      >
        <Icon name="edit" size={13} className="text-icon-secondary flex-shrink-0" />
        <span className="text-caption-01 font-semibold text-text-secondary flex-shrink-0">Edit</span>
        <span className="text-caption-01 font-mono text-blue-40 truncate">{m.file}</span>
        <span className="ml-auto text-label-micro font-mono text-text-helper flex-shrink-0">{m.summary}</span>
        <Icon name={open ? 'chevron-up' : 'chevron-down'} size={14} className="text-icon-secondary flex-shrink-0" />
      </button>
      {open && (
        <div className="border-t border-border-subtle-01 font-mono text-label-micro leading-[18px] max-h-[280px] overflow-y-auto">
          {m.diff.map((row, i) => (
            <EditRow key={i} row={row} />
          ))}
        </div>
      )}
    </div>
  );
};

// ── results table (chat3 의 "결과 (모드별 동작)") ─────────────
const TableBlock: React.FC<{ m: Extract<ThreadMessage, { kind: 'table' }> }> = ({ m }) => (
  <div className="my-2 max-w-[640px] border border-border-subtle-01 rounded-lg overflow-hidden bg-layer-01">
    {m.caption && (
      <div className="px-3 py-2 border-b border-border-subtle-01 text-caption-01 font-semibold text-text-secondary">
        {m.caption}
      </div>
    )}
    <table className="w-full border-collapse text-caption-01">
      <thead>
        <tr className="bg-layer-02">
          {m.columns.map((c, i) => (
            <th
              key={i}
              className="text-left px-3 py-1.5 font-semibold text-text-secondary border-b border-border-subtle-01"
            >
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {m.rows.map((r, i) => (
          <tr key={i} className="border-t border-border-subtle-01 first:border-t-0">
            {r.map((cell, j) => (
              <td key={j} className="align-top px-3 py-1.5 text-text-secondary">
                <Rich text={cell} />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

// ── used-skill badge (clickable → jumps to panel) ────────────
const UsedSkillBadge: React.FC<{ skill: SkillRef; onJump?: (id: string) => void }> = ({ skill, onJump }) => {
  const { t } = useTranslation('chat');
  return (
    <button
      onClick={() => onJump?.(skill.id)}
      title={t('message.usedSkillTitle')}
      className="inline-flex items-center gap-1.5 bg-layer-01 border border-border-subtle-01 rounded-md pl-1.5 pr-2 py-0.5 text-caption-01 text-text-secondary hover:bg-layer-hover-01 transition-colors"
    >
      <Icon name="skill" size={13} />
      <span className="whitespace-nowrap">
        {t('message.usedSkillBadge')}{' '}
        <span className="text-text-primary font-semibold">
          {skill.label} {skill.ver}
        </span>
      </span>
      <Icon name="arrow-up-right" size={12} className="opacity-70" />
    </button>
  );
};

// ── message action bar (copy / feedback / regenerate / teach) ─
const MessageActions: React.FC<{
  onRegenerate?: () => void;
  onCorrect?: (text: string) => void;
  copyText?: string;
  /** epoch ms — 있으면 교정 버튼 옆에 발신 시각을 표시한다 */
  sentAt?: number;
}> = ({ onRegenerate, onCorrect, copyText, sentAt }) => {
  const [vote, setVote] = useState<'up' | 'down' | null>(null);
  const [copied, setCopied] = useState(false);
  const [correcting, setCorrecting] = useState(false);
  const [inputText, setInputText] = useState('');
  const { t, i18n } = useTranslation('chat');

  const Btn: React.FC<{
    icon: Parameters<typeof Icon>[0]['name'];
    label: string;
    onClick: () => void;
    active?: boolean;
    tint: string;
    /** extra node rendered inside the hover wrapper (e.g. copy toast) */
    overlay?: React.ReactNode;
  }> = ({ icon, label, onClick, active, tint, overlay }) => (
    <div className="group relative">
      <button
        onClick={onClick}
        aria-label={label}
        style={{ ['--tint' as string]: tint } as React.CSSProperties}
        className={`action-btn w-6 h-6 flex items-center justify-center rounded-md ${active ? 'is-active' : ''}`}
      >
        <Icon name={icon} size={14} />
      </button>
      <span className="action-tip">{label}</span>
      {overlay}
    </div>
  );

  const copy = () => {
    if (copyText) navigator.clipboard?.writeText(copyText).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1900);
  };

  const submit = () => {
    const trimmed = inputText.trim();
    if (!trimmed) return;
    setCorrecting(false);
    setInputText('');
    onCorrect?.(trimmed);
  };

  const timeLabel = formatTime(sentAt, i18n.language);

  return (
    <div className="mt-1.5">
      <div className="flex items-center gap-px -ml-2">
        <Btn
          icon={copied ? 'check' : 'copy'}
          label={t('message.copyAnswer')}
          tint="#78a9ff"
          onClick={copy}
          overlay={copied ? <span className="copy-toast">{t('message.copied')}</span> : null}
        />
        <Btn
          icon="thumbs-up"
          label={t('message.thumbsUp')}
          tint="#42be65"
          active={vote === 'up'}
          onClick={() => setVote((v) => (v === 'up' ? null : 'up'))}
        />
        <Btn
          icon="thumbs-down"
          label={t('message.thumbsDown')}
          tint="#ff8389"
          active={vote === 'down'}
          onClick={() => {
            const isDown = vote === 'down';
            setVote(isDown ? null : 'down');
            setCorrecting(!isDown);
          }}
        />
        <Btn icon="renew" label={t('message.regenerate')} tint="#be95ff" onClick={() => onRegenerate?.()} />
        <div className="w-px h-4 bg-border-subtle-01 mx-1" />
        <Btn
          icon="edit"
          label={t('message.correctTitle')}
          tint="#3ddbd9"
          active={correcting}
          onClick={() => {
            setCorrecting((c) => !c);
            if (!correcting) setVote('down');
          }}
        />
        {timeLabel && (
          <>
            <div className="w-px h-3.5 bg-border-subtle-01 mx-1.5" />
            <span className="text-caption-01 text-text-helper select-none">{timeLabel}</span>
          </>
        )}
      </div>

      {correcting && (
        <div className="mt-2 border border-border-strong-01 bg-layer-01 rounded-lg overflow-hidden">
          <div className="flex items-center gap-1.5 px-2.5 pt-2">
            <Icon name="idea" size={14} className="text-icon-secondary" />
            <span className="text-caption-01 font-semibold text-text-primary">{t('message.correctTitle')}</span>
            <span className="ml-auto">
              <AILabel />
            </span>
          </div>
          <textarea
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder={t('message.correctPlaceholder')}
            className="w-full resize-none border-none outline-none bg-transparent text-text-primary text-body-short-01 leading-5 px-2.5 py-2 h-[52px]"
          />
          <div className="flex items-center gap-2 px-2 pb-2">
            <span className="text-caption-01 text-text-helper">{t('message.correctHint')}</span>
            <div className="flex-1" />
            <Button
              kind="ghost"
              size="lg"
              onClick={() => {
                setCorrecting(false);
                setInputText('');
              }}
            >
              {t('message.cancel')}
            </Button>
            <Button kind="primary" size="lg" icon={<Icon name="send" size={14} />} onClick={submit}>
              {t('message.teach')}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
};

// ── memory-written indicator ─────────────────────────────────
const MemoryIndicator: React.FC<{
  m: Extract<ThreadMessage, { kind: 'memory' }>;
  onJumpSkill?: (id: string) => void;
}> = ({ m, onJumpSkill }) => {
  const { t } = useTranslation('chat');
  return (
    <div className="my-1 max-w-[640px]">
      <div className="ai-tile flex gap-2.5 items-start px-3.5 py-3">
        <Icon name="bookmark" size={16} filled className="text-blue-40 mt-0.5 flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-body-short-01 font-semibold text-text-primary">{m.text}</span>
            <span className="text-caption-01 text-text-helper">{t('message.memoryRecorded')}</span>
          </div>
          <ul className="mt-1.5 pl-4 flex flex-col gap-1 list-disc">
            {m.facts.map((f, i) => (
              <li key={i} className="text-caption-02 leading-[18px] text-text-secondary">
                {f}
              </li>
            ))}
          </ul>
          {m.target && (
            <button
              onClick={() => onJumpSkill?.(m.target!.id)}
              className="mt-2 inline-flex items-center gap-1 text-caption-01 text-link-primary hover:underline whitespace-nowrap"
            >
              {t('message.memoryAppliedTo', { label: m.target.label })}{' '}
              <Icon name="arrow-up-right" size={12} className="opacity-80" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

// Live-ticking elapsed seconds since `startedAt`, re-rendering once per second
// while `active`. WHY: commercial models run with extended thinking OFF, so they
// stream no reasoning and can sit silent for many seconds before the first token.
// A visible "N초" counter tells the user RICE is working (not stuck) when there
// is no reasoning trace to show.
const useElapsedSeconds = (startedAt: number | undefined, active: boolean): number => {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active || startedAt == null) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active, startedAt]);
  if (startedAt == null) return 0;
  return Math.max(0, Math.floor((now - startedAt) / 1000));
};

// ── collapsible thinking trace ───────────────────────────────
const ThinkingBlock: React.FC<{ m: Extract<ThreadMessage, { kind: 'thinking' }> }> = ({ m }) => {
  const text = stripPlanningBreadcrumb(m.text ?? '');
  const hasTrace = text.trim().length > 0;
  // While streaming, default to open so the live reasoning is visible (the
  // "motion"); once done it auto-collapses — unless the user toggled it.
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const open = userOpen ?? !!m.streaming;
  const steps = m.steps ?? [];
  const elapsed = useElapsedSeconds(m.startedAt, !!m.streaming && !hasTrace);
  const { t } = useTranslation('chat');
  // With reasoning text (Ollama message.thinking) → show the live trace under a
  // plain "thinking…" header. Empty waiting blocks (SaaS) keep the elapsed counter.
  const liveLabel = hasTrace ? (
    <>
      {t('message.thinkingLive')}
      <AnimatedEllipsis />
    </>
  ) : (
    <>
      {t('message.thinkingLiveWaiting')}
      <AnimatedEllipsis />
      <span className="ml-1 tabular-nums">{t('message.thinkingElapsed', { count: elapsed })}</span>
    </>
  );
  if (!m.streaming && !hasTrace && steps.length === 0) return null;
  return (
    <div className="my-1.5 max-w-[640px]">
      <button
        onClick={() => setUserOpen(!open)}
        className="flex items-center gap-1.5 text-caption-01 text-text-helper hover:text-text-secondary transition-colors"
      >
        <Icon name="idea" size={14} className={`opacity-70 ${m.streaming ? 'animate-pulse' : ''}`} />
        <span className={m.streaming ? 'tabular-nums' : undefined}>
          {m.streaming ? liveLabel : m.duration ? t('message.thinkingDone', { duration: m.duration }) : t('message.thinkingTrace')}
        </span>
        <Icon name={open ? 'chevron-up' : 'chevron-down'} size={14} />
      </button>
      {open && m.text != null && (
        <div className="mt-2 pl-1 text-caption-01 text-text-helper whitespace-pre-wrap leading-relaxed border-l-2 border-border-subtle-01 pl-3">
          {text || '…'}
        </div>
      )}
      {open && m.text == null && (
        <div className="mt-2 pl-1 flex flex-col">
          {steps.map((s, i) => (
            <div key={i} className="flex gap-2.5">
              <div className="flex flex-col items-center flex-shrink-0">
                <div className="w-1.5 h-1.5 rounded-full bg-border-strong-01 mt-[7px]" />
                {i < steps.length - 1 && <div className="w-px flex-1 min-h-[14px] bg-border-subtle-01 my-0.5" />}
              </div>
              <div className="pb-2 min-w-0">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <span className="text-caption-01 text-text-secondary">{s.label}</span>
                  {s.tool && (
                    <span className="text-label-micro font-mono text-blue-40 border border-border-subtle-01 rounded px-1 py-px">
                      {s.tool}
                    </span>
                  )}
                </div>
                {s.detail && <div className="text-caption-01 text-text-helper mt-0.5">{s.detail}</div>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// ── plan-and-execute × ReAct 계획 카드 (RICE_AGENT_EXECUTION.md §3) ─────────
// 턴 계획의 살아있는 상태 — plan_updated 스냅샷마다 통째로 교체된다. 에이전트
// "작업 구조"의 문법(도구 카드와 같은 층)이지 AI 제안 순간이 아니다 → ai-tile 금지.
const PlanStepMarker: React.FC<{ status: PlanStepData['status'] }> = ({ status }) => {
  if (status === 'done') {
    return (
      <span className="w-4 h-4 rounded-[4px] border border-support-success/55 bg-support-success/10 flex items-center justify-center flex-shrink-0">
        <Icon name="check" size={11} className="text-support-success" />
      </span>
    );
  }
  if (status === 'failed') {
    return (
      <span className="w-4 h-4 rounded-[4px] border border-support-error/55 bg-support-error/10 flex items-center justify-center flex-shrink-0">
        <Icon name="close" size={11} className="text-support-error" />
      </span>
    );
  }
  if (status === 'in_progress') {
    return (
      <span className="w-4 h-4 flex items-center justify-center flex-shrink-0">
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full rounded-full bg-blue-40 opacity-60 animate-ping" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-blue-40" />
        </span>
      </span>
    );
  }
  if (status === 'skipped') {
    return (
      <span className="w-4 h-4 rounded-[4px] border border-border-strong-01/70 flex items-center justify-center flex-shrink-0">
        <span className="w-1.5 h-px bg-text-helper" />
      </span>
    );
  }
  return (
    <span className="w-4 h-4 rounded-[4px] border border-border-strong-01/70 flex items-center justify-center flex-shrink-0">
      <span className="w-1.5 h-1.5 rounded-full border border-border-strong-02/80" />
    </span>
  );
};

const PlanCard: React.FC<{ m: Extract<ThreadMessage, { kind: 'plan' }> }> = ({ m }) => {
  const doneCount = m.steps.filter((s) => s.status === 'done' || s.status === 'skipped').length;
  const failed = m.steps.some((s) => s.status === 'failed');
  const completed = m.status === 'completed';
  const { t } = useTranslation('chat');
  return (
    <div className="my-2 max-w-[680px]">
      <div className="border-l border-border-subtle-01 pl-4 py-0.5">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className={`w-2 h-2 rounded-full flex-shrink-0 ${
              completed ? 'bg-support-success' : failed ? 'bg-support-error' : 'bg-blue-40'
            }`}
          />
          <span className="text-body-short-01 font-semibold text-text-primary">{t('message.planTitle')}</span>
          {m.revision > 1 && <span className="text-caption-01 text-text-helper">{t('message.planRevision', { count: m.revision - 1 })}</span>}
          <span
            className={`ml-auto text-caption-01 tabular-nums flex-shrink-0 ${
              completed ? 'text-support-success' : failed ? 'text-support-error' : 'text-text-helper'
            }`}
          >
            {completed ? t('message.planCompleted') : `${doneCount}/${m.steps.length}`}
          </span>
        </div>
        <div className="mt-1 text-caption-01 text-text-helper truncate">{m.goal}</div>
        <div className="mt-2 flex flex-col gap-1.5">
          {m.steps.map((s) => {
            const secondary = s.note || (s.status === 'in_progress' || s.status === 'pending' ? s.detail : '');
            const muted = s.status === 'done' || s.status === 'skipped';
            return (
              <div key={s.id} className="flex items-start gap-2.5 min-w-0">
                <span className="mt-[1px]">
                  <PlanStepMarker status={s.status} />
                </span>
                <div className="min-w-0 flex-1">
                  <div
                    className={`text-body-short-01 leading-[19px] ${
                      s.status === 'in_progress'
                        ? 'text-text-primary font-medium'
                        : muted
                          ? 'text-text-helper/70 line-through decoration-border-strong-02'
                          : s.status === 'failed'
                            ? 'text-support-error'
                            : 'text-text-secondary'
                    }`}
                  >
                    {s.title}
                  </div>
                  {secondary ? <div className="text-caption-01 text-text-helper truncate">{secondary}</div> : null}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

// ── generic builtin/connector tool call trace ───────────────
// One card per live tool call: shows the tool + args while running, then the
// result/error once the runtime sends tool_result.

const isRecord = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);

const parseResultObject = (result?: string): Record<string, unknown> | null => {
  if (result == null) return null;
  try {
    const parsed = JSON.parse(result);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
};

const formatBytes = (v: unknown): string | null => {
  if (typeof v !== 'number' || !Number.isFinite(v)) return null;
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(v < 10 * 1024 ? 1 : 0)} KB`;
  return `${(v / (1024 * 1024)).toFixed(1)} MB`;
};

type TFn = (key: string, options?: Record<string, unknown>) => string;

const summarizeValue = (key: string, value: unknown, t: TFn): string => {
  if (value == null) return t('message.toolValueNone');
  if (key === 'bytes_written') return formatBytes(value) ?? String(value);
  if (typeof value === 'string') return value.length > 140 ? `${value.slice(0, 140)}…` : value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) {
    if (key === 'slides') {
      const titles = value
        .map((item) => (isRecord(item) && typeof item.title === 'string' ? item.title : null))
        .filter(Boolean)
        .slice(0, 3) as string[];
      if (titles.length > 0) {
        const joined = titles.join(', ');
        return value.length > titles.length
          ? t('message.toolValueSlidesWithTitlesMore', { count: value.length, titles: joined })
          : t('message.toolValueSlidesWithTitles', { count: value.length, titles: joined });
      }
      return t('message.toolValueSlides', { count: value.length });
    }
    if (key === 'blocks') return t('message.toolValueBlocks', { count: value.length });
    if (key === 'warnings') return value.length ? value.map(String).join(' · ') : t('message.toolValueNone');
    return t('message.toolValueItems', { count: value.length });
  }
  if (isRecord(value)) return t('message.toolValueFields', { count: Object.keys(value).length });
  return String(value);
};

const BuiltinToolCard: React.FC<{ m: Extract<ThreadMessage, { kind: 'toolcall' }> }> = ({ m }) => {
  const [open, setOpen] = useState(false);
  const [rawOpen, setRawOpen] = useState(false);
  const { openFile } = useFileViewer();
  const { t } = useTranslation('chat');
  const dot =
    m.status === 'running' ? 'bg-support-warning animate-pulse' : m.status === 'error' ? 'bg-support-error' : 'bg-support-success';

  const resultObj = parseResultObject(m.result);
  const path =
    (typeof resultObj?.path === 'string' ? resultObj.path : null) ??
    (typeof m.args.path === 'string' ? (m.args.path as string) : null);
  const title =
    (typeof m.args.title === 'string' ? (m.args.title as string) : null) ??
    (typeof resultObj?.title === 'string' ? resultObj.title : null);

  // 번역된 도구 이름. 카탈로그에 없으면 도구 이름을 사람이 읽기 좋게 변환.
  // toolNames 키는 런타임에 결정되므로 TFn (넓은 타입)으로 호출한다.
  const toolLabel = (t as TFn)(`message.toolNames.${m.tool}`, { defaultValue: m.tool.replace(/_/g, ' ') });

  const slideCount =
    typeof resultObj?.slide_count === 'number'
      ? resultObj.slide_count
      : Array.isArray(m.args.slides)
        ? m.args.slides.length
        : null;
  const sheetCount =
    typeof resultObj?.sheet_count === 'number'
      ? resultObj.sheet_count
      : Array.isArray(m.args.sheets)
        ? m.args.sheets.length
        : null;

  const rows: { label: string; value: React.ReactNode; mono?: boolean }[] = [];
  const push = (label: string, value: unknown, mono = false) => {
    if (value == null || value === '') return;
    rows.push({ label, value: summarizeValue(label, value, t as TFn), mono });
  };
  if (path) {
    rows.push({
      label: t('message.toolLabelFile'),
      mono: true,
      value: (
        <button onClick={() => openFile(path)} className="text-blue-40 font-mono break-all text-left hover:underline">
          {path}
        </button>
      ),
    });
  }
  push(t('message.toolLabelTitle'), title);
  push(t('message.toolLabelTheme'), resultObj?.theme ?? m.args.theme, true);
  push(t('message.toolLabelSlides'), slideCount != null ? t('message.toolValueSlides', { count: slideCount }) : null);
  push(t('message.toolLabelSheets'), sheetCount != null ? t('message.toolValueSheets', { count: sheetCount }) : null);
  push(t('message.toolLabelSize'), formatBytes(resultObj?.bytes_written) ?? null);
  push(t('message.toolLabelLayout'), m.args.slides ?? m.args.blocks ?? m.args.sheets);
  push(t('message.toolLabelWarnings'), resultObj?.warnings);

  if (rows.length === 0) {
    Object.entries(resultObj ?? m.args)
      .slice(0, 5)
      .forEach(([key, value]) => push(key, value, true));
  }

  const rawDetails = JSON.stringify(
    {
      arguments: m.args,
      ...(resultObj ? { result: resultObj } : m.result ? { result: m.result } : {}),
      ...(m.error ? { error: m.error } : {}),
    },
    null,
    2,
  );

  return (
    <div className="my-1.5 max-w-[680px]">
      {/* rationale(#1): 행동 직전 모델이 남긴 한 줄 "왜" — 접히는 추론과 달리 항상
          보이며 파란 좌측 강조(에이전트 목소리)로 카드에 묶인다. "생성·읽기·생성"의
          반복을 자기검토→개선 서사로 읽히게 하는 핵심 캡션. */}
      {m.rationale && (
        <p className="mb-1.5 border-l-2 border-blue-40/45 pl-2.5 text-caption-01 leading-relaxed text-text-secondary">
          {m.rationale}
        </p>
      )}
      {/* Header/result details shrink to their content. Previously the block took
          the full 680px cap and ml-auto pushed the chevron unnaturally far away
          even for a short "tool · file · done" line. */}
      <div className="w-fit min-w-[260px] max-w-full">
        <button
          onClick={() => setOpen((o) => !o)}
          className="flex w-full min-w-0 items-center gap-2 text-left text-caption-01 text-text-secondary transition-colors hover:text-text-primary"
        >
          <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dot}`} />
          <span className="font-semibold text-text-secondary flex-shrink-0">{toolLabel}</span>
          {path && <span className="min-w-0 truncate font-mono text-text-helper">{path}</span>}
          <span className="text-text-helper flex-shrink-0">
            {m.status === 'running' ? t('message.toolRunning') : m.status === 'error' ? t('message.toolError') : t('message.toolDone')}
          </span>
          <Icon name={open ? 'chevron-up' : 'chevron-down'} size={14} className="ml-auto flex-shrink-0" />
        </button>
        {open && (
          <div className="mt-2 ml-3.5 border-l border-border-subtle-01 pl-3 flex flex-col gap-2.5">
            {rows.length > 0 && (
              <div className="grid grid-cols-[72px_minmax(0,1fr)] gap-x-3 gap-y-1">
                {rows.map((row) => (
                  <React.Fragment key={row.label}>
                    <span className="text-caption-01 text-text-helper">{row.label}</span>
                    <span
                      className={`text-caption-01 text-text-secondary min-w-0 break-words ${
                        row.mono ? 'font-mono' : ''
                      }`}
                    >
                      {row.value}
                    </span>
                  </React.Fragment>
                ))}
              </div>
            )}
            {m.error != null && <span className="text-caption-01 font-mono text-support-error break-all">{m.error}</span>}
            {path && (
              <button
                onClick={() => openFile(path)}
                className="self-start inline-flex items-center gap-1.5 text-caption-01 text-link-primary hover:underline"
              >
                <Icon name="document" size={13} />
                {t('message.toolOpenFile', { path })}
              </button>
            )}
            <button
              type="button"
              onClick={() => setRawOpen((o) => !o)}
              className="self-start inline-flex items-center gap-1 text-caption-01 text-text-helper hover:text-text-secondary transition-colors"
            >
              <Icon name={rawOpen ? 'chevron-up' : 'chevron-down'} size={12} />
              {t('message.toolRawDetails')} {rawOpen ? t('message.toolRawCollapse') : t('message.toolRawExpand')}
            </button>
            {rawOpen && (
              <pre className="max-h-[260px] overflow-y-auto whitespace-pre-wrap break-words rounded-md border border-border-subtle-01 bg-background px-3 py-2 text-label-micro leading-[18px] text-text-helper font-mono m-0">
                {rawDetails}
              </pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

// ── tool-execution approval (human-in-the-loop, CT 1-E) ──────
// risky / external-effect tools pause for explicit user approval before running.
const ApprovalCard: React.FC<{
  m: Extract<ThreadMessage, { kind: 'approval' }>;
  /** post the decision back to the runtime (POST /sessions/{id}/tool-approval) */
  onRespond?: (decision: 'approve' | 'reject') => void;
}> = ({ m, onRespond }) => {
  const [state, setState] = useState<'pending' | 'approved' | 'denied'>('pending');
  const [previewOpen, setPreviewOpen] = useState(false);
  const hasPreview = !!m.fields?.length || !!m.body;
  const { t } = useTranslation('chat');
  return (
    <div className="my-1.5 max-w-[680px]">
      {/* 도구 실행 승인은 AI 문법이 아니라 warning 문법 — ai-tile 금지 */}
      <div className="rounded-lg border border-border-subtle-01 border-l-2 border-l-support-warning bg-layer-01 px-3 py-2.5">
        <div className="flex items-start gap-2.5">
          <span className="mt-0.5 w-6 h-6 rounded-md bg-support-warning/12 flex items-center justify-center flex-shrink-0">
            <Icon name="warning" size={14} filled className="text-orange-40" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 min-w-0 flex-wrap">
              <span className="text-body-short-01 font-semibold text-text-primary">{t('message.approvalRequired')}</span>
              <span className="text-caption-01 font-mono text-blue-40 truncate max-w-[180px]">{m.tool}</span>
              <span className="text-body-short-01 text-text-secondary truncate">{m.label}</span>
              <span className="inline-flex items-center gap-1 flex-shrink-0 border border-border-subtle-01 px-1.5 py-0.5 rounded text-label-micro font-mono text-text-helper">
                <Icon name="lock" size={10} className="opacity-70" />
                {m.scope.mode}
              </span>
            </div>
            <div className="mt-0.5 text-caption-01 text-text-helper line-clamp-2">{m.reason}</div>
            {hasPreview && (
              <button
                type="button"
                onClick={() => setPreviewOpen((o) => !o)}
                className="mt-1.5 inline-flex items-center gap-1 text-caption-01 text-link-primary hover:underline"
              >
                <Icon name={previewOpen ? 'chevron-up' : 'chevron-down'} size={12} />
                {t('message.approvalExecutionPreview')} {previewOpen ? t('message.approvalCollapse') : t('message.approvalExpand')}
              </button>
            )}
          </div>
          {state === 'pending' ? (
            <div className="flex items-center gap-1.5 flex-shrink-0 pt-0.5">
              <Button
                kind="ghost"
                size="xs"
                onClick={() => {
                  setState('denied');
                  onRespond?.('reject');
                }}
              >
                {t('message.reject')}
              </Button>
              <Button
                kind="primary"
                size="xs"
                icon={<Icon name="check" size={14} />}
                onClick={() => {
                  setState('approved');
                  onRespond?.('approve');
                }}
              >
                {t('message.approve')}
              </Button>
            </div>
          ) : state === 'approved' ? (
            <span className="inline-flex items-center gap-1.5 text-caption-01 text-support-success flex-shrink-0 pt-1">
              <Icon name="check-circle" size={13} /> {t('message.approvedStatus')}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 text-caption-01 text-text-helper flex-shrink-0 pt-1">
              <Icon name="close" size={13} /> {t('message.deniedStatus')}
            </span>
          )}
        </div>
        {previewOpen && hasPreview && (
          <div className="mt-2 ml-8 border border-border-subtle-01 rounded-md bg-background overflow-hidden">
            {m.fields && (
              <div className="px-3 py-2 flex flex-col gap-1">
                {m.fields.map((f) => (
                  <div key={f.label} className="flex gap-2 text-caption-01">
                    <span className="text-text-helper min-w-[56px] flex-shrink-0">{f.label}</span>
                    <span className="text-text-secondary break-all">{f.value}</span>
                  </div>
                ))}
              </div>
            )}
            {m.body !== undefined && (
              <pre className="border-t border-border-subtle-01 first:border-t-0 max-h-[160px] overflow-y-auto whitespace-pre-wrap break-words m-0 px-3 py-2 text-caption-01 leading-relaxed text-text-secondary font-sans">
                {m.body}
              </pre>
            )}
          </div>
        )}
        {state === 'pending' ? (
          null
        ) : state === 'approved' ? (
          <div className="mt-2 ml-8 text-caption-01 text-support-success">{m.approvedText ?? t('message.approvedDefault')}</div>
        ) : (
          <div className="mt-2 ml-8 text-caption-01 text-text-helper">{m.deniedText ?? t('message.deniedDefault')}</div>
        )}
      </div>
    </div>
  );
};

// ── skill-improvement proposal (patch diff card, LEARNING_LOOP 5.2) ──
// the loop proposes editing a skill's body (version++); the user approves,
// rejects, or edits before applying. Mirrors the tool-approval parking flow
// (skill_patch_proposed → POST /skills/{id}/patch with approval_id).
const DiffLine: React.FC<{ line: { t: 'add' | 'remove' | 'ctx'; text: string } }> = ({ line }) => {
  const gutter = line.t === 'add' ? '+' : line.t === 'remove' ? '−' : ' ';
  const cls =
    line.t === 'add'
      ? 'bg-diff-add-bg text-diff-add-text border-l-2 border-l-diff-add-accent'
      : line.t === 'remove'
        ? 'bg-diff-remove-bg text-diff-remove-text border-l-2 border-l-diff-remove-accent'
        : 'text-text-helper border-l-2 border-l-transparent';
  return (
    <div className={`flex gap-2 px-2.5 py-0.5 ${cls}`}>
      <span className="select-none opacity-70 w-2 flex-shrink-0 text-center">{gutter}</span>
      <span className="whitespace-pre-wrap break-all">{line.text || ' '}</span>
    </div>
  );
};

const PatchProposalCard: React.FC<{
  m: Extract<ThreadMessage, { kind: 'patch' }>;
  onApprove?: (editedBody?: string) => void;
  /** 명시적 거부 — 서버 파킹을 즉시 해소한다(없으면 타임아웃까지 서버가 대기하다
   *  제안함으로 이동해버린다 — 사용자가 이미 '아니'라고 한 제안인데도). */
  onReject?: () => void;
  onJumpSkill?: (id: string) => void;
}> = ({ m, onApprove, onReject, onJumpSkill }) => {
  const [state, setState] = useState<'pending' | 'approved' | 'denied'>('pending');
  const [editing, setEditing] = useState(false);
  const [body, setBody] = useState(m.proposedBody);
  const edited = editing && body !== m.proposedBody;
  const { t } = useTranslation('chat');
  const descriptionValue = m.isCreation ? m.description : m.newDescription;
  const tagsValue = m.isCreation ? m.tags : m.newTags;
  const showDescription = m.isCreation || m.newDescription !== undefined;
  const showTags = m.isCreation || m.newTags !== undefined;
  const showMetadata = showDescription || showTags;

  return (
    <div className="my-2 max-w-[680px]">
      {/* 판단해야 하는 정보(무엇/왜/어떻게 바뀌는지)를 헤더 한 덩어리로 묶고,
          긴 diff는 차분한 작업 표면으로 분리한다. */}
      <div className="skill-patch-card overflow-hidden rounded-xl border border-border-subtle-02 bg-layer-01">
        <div className="flex items-start gap-3 border-b border-border-subtle-01 bg-layer-02/45 px-4 py-3.5">
          <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border border-purple-50/30 bg-purple-60/15">
            <Icon name="sparkle" size={15} className="text-purple-40" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="text-body-short-01 font-semibold text-text-primary">{t('message.patchTitle')}</span>
              <Tag type="purple" size="sm">{m.triggerLabel}</Tag>
            </div>
            <div className="mt-1 flex min-w-0 items-center gap-1.5">
              <span className="truncate text-caption-01 font-medium text-text-secondary">{m.skillName}</span>
              <span className="inline-flex flex-shrink-0 items-center gap-1 font-mono text-label-micro">
                <span className="text-text-helper line-through">{m.fromVer}</span>
                <Icon name="arrow-right" size={11} className="text-icon-secondary" />
                <span className="font-semibold text-blue-30">{m.toVer}</span>
              </span>
            </div>
          </div>
          <span className="flex-shrink-0 rounded-full border border-border-subtle-02 bg-background/40 px-2 py-1 text-label-micro text-text-helper">
            {t('message.patchReviewNeeded')}
          </span>
        </div>

        {/* changelog */}
        <div className="px-4 pt-3.5">
          <p className="text-body-short-01 text-text-secondary flex items-start gap-2 leading-relaxed">
            <Icon name="edit" size={13} className="text-icon-secondary mt-0.5 flex-shrink-0" />
            {m.changelog}
          </p>

        {/* Phase D 가드 경고 — 시크릿 자동 마스킹 등 */}
        {!!m.warnings?.length && (
          <div className="mt-3 flex items-start gap-1.5 px-2.5 py-1.5 rounded-md bg-support-warning bg-opacity-10 border border-support-warning border-opacity-30">
            <Icon name="warning" size={13} className="text-support-warning mt-0.5 flex-shrink-0" />
            <p className="text-caption-01 text-text-secondary">
              {m.warnings.map((warning) => localizeLearningWarning(warning, t)).join(' · ')}
            </p>
          </div>
        )}
        </div>

        {/* Metadata is part of the same approved write as the body. Keep the
            exact values visible instead of reducing them to a generic warning. */}
        {showMetadata && (
          <div className="mx-4 mt-3 rounded-lg border border-border-subtle-01 bg-background/35 px-3 py-2.5">
            <div className="mb-2 text-caption-01 font-semibold text-text-secondary">
              {t('message.patchMetadataTitle')}
            </div>
            <dl className="space-y-2 text-caption-01">
              {showDescription && (
                <div>
                  <dt className="text-text-helper">
                    {t(m.isCreation ? 'message.patchMetadataDescription' : 'message.patchMetadataNewDescription')}
                  </dt>
                  <dd className="mt-0.5 whitespace-pre-wrap break-words font-mono text-text-primary">
                    {descriptionValue || t('message.patchMetadataEmpty')}
                  </dd>
                </div>
              )}
              {showTags && (
                <div>
                  <dt className="text-text-helper">
                    {t(m.isCreation ? 'message.patchMetadataTags' : 'message.patchMetadataNewTags')}
                  </dt>
                  <dd className="mt-1 flex flex-wrap gap-1">
                    {(tagsValue ?? []).length > 0 ? (
                      (tagsValue ?? []).map((tag, index) => (
                        <span
                          key={`${tag}-${index}`}
                          className="max-w-full break-all rounded border border-border-subtle-01 bg-layer-02 px-1.5 py-0.5 font-mono text-text-primary"
                        >
                          {tag}
                        </span>
                      ))
                    ) : (
                      <span className="font-mono text-text-helper">{t('message.patchMetadataNoTags')}</span>
                    )}
                  </dd>
                </div>
              )}
            </dl>
          </div>
        )}

        {/* diff (or editable body) */}
        <div className="mx-4 mb-3 mt-3 overflow-hidden rounded-lg border border-border-subtle-01 bg-background/35">
          <div className="px-3 py-2 border-b border-border-subtle-01 flex items-center gap-1.5">
            <Icon name="renew" size={12} className="text-icon-secondary opacity-70" />
            <span className="text-caption-01 font-semibold text-text-secondary">
              {editing ? t('message.patchBodyEdit') : t('message.patchBodyDiff')}
            </span>
            {state === 'pending' && (
              <button
                onClick={() => setEditing((e) => !e)}
                className="ml-auto text-caption-01 text-link-primary hover:underline inline-flex items-center gap-1"
              >
                <Icon name={editing ? 'view' : 'edit'} size={12} />
                {editing ? t('message.patchViewDiff') : t('message.patchEdit')}
              </button>
            )}
          </div>
          {editing ? (
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              disabled={state !== 'pending'}
              spellCheck={false}
              className="w-full px-3 py-2.5 text-label-micro font-mono text-text-secondary leading-relaxed resize-none bg-transparent border-0 outline-none focus:ring-1 focus:ring-focus"
              style={{ minHeight: '160px', maxHeight: '260px' }}
            />
          ) : (
            <div className="py-1 text-label-micro font-mono leading-[18px] max-h-[210px] overflow-y-auto">
              {m.diff.map((line, i) => (
                <DiffLine key={i} line={line} />
              ))}
            </div>
          )}
        </div>

        {state === 'pending' ? (
          <div className="flex items-center gap-2 flex-wrap border-t border-border-subtle-01 bg-layer-02/25 px-4 py-3">
            <span className="text-caption-01 text-text-helper">
              {t('message.patchApproveHint')}
            </span>
            <div className="flex-1" />
            <Button
              kind="ghost"
              size="sm"
              onClick={() => {
                setState('denied');
                onReject?.();
              }}
            >
              {t('message.reject')}
            </Button>
            <Button
              kind="primary"
              size="sm"
              icon={<Icon name="check" size={14} />}
              onClick={() => {
                setState('approved');
                onApprove?.(edited ? body : undefined);
              }}
            >
              {edited ? t('message.patchApplyEdited') : t('message.patchApply')}
            </Button>
          </div>
        ) : state === 'approved' ? (
          <div className="flex items-center gap-2.5 flex-wrap border-t border-border-subtle-01 px-4 py-3">
            <span className="inline-flex items-center gap-1.5 text-caption-01 text-support-success">
              <Icon name="check-circle" size={13} /> {edited ? t('message.patchApprovedStatusEdited', { toVer: m.toVer }) : t('message.patchApprovedStatus', { toVer: m.toVer })}
            </span>
            <div className="flex-1" />
            <Button kind="ghost" size="sm" icon={<Icon name="arrow-up-right" size={14} />} onClick={() => onJumpSkill?.(m.skillId)}>
              {t('message.patchViewInSkills')}
            </Button>
          </div>
        ) : (
          <span className="inline-flex items-center gap-1.5 border-t border-border-subtle-01 px-4 py-3 text-caption-01 text-text-helper">
            <Icon name="close" size={13} /> {t('message.patchDeniedStatus', { fromVer: m.fromVer })}
          </span>
        )}
      </div>
    </div>
  );
};

// ── user-bubble affordance (copy on hover, beside timestamp) ─
const UserCopyButton: React.FC<{ text: string }> = ({ text }) => {
  const [copied, setCopied] = useState(false);
  const { t } = useTranslation('chat');

  const copy = () => {
    navigator.clipboard?.writeText(text).catch(() => {});
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return (
    <div className="group relative">
      <button
        type="button"
        onClick={copy}
        aria-label={t('message.copyUserMessage')}
        className="w-6 h-6 inline-flex items-center justify-center rounded-md text-icon-secondary opacity-0 transition-all duration-150 hover:bg-layer-hover-01 hover:text-icon-primary group-hover/user:opacity-100 focus-visible:opacity-100"
      >
        <Icon name={copied ? 'check' : 'copy'} size={13} className={copied ? 'text-support-success' : undefined} />
      </button>
      <span className="action-tip">{copied ? t('message.copiedShort') : t('message.copyShort')}</span>
    </div>
  );
};

// ── one assistant block body (no avatar/label — the turn carries identity) ─
type AssistantMessage = Exclude<ThreadMessage, { kind: 'user' }>;

const AssistantBlock: React.FC<{
  m: AssistantMessage;
  onCorrect?: (text: string) => void;
  onJumpSkill?: (id: string) => void;
  onRegenerate?: () => void;
  onApprovePatch?: (m: Extract<ThreadMessage, { kind: 'patch' }>, editedBody?: string) => void;
  onRejectPatch?: (m: Extract<ThreadMessage, { kind: 'patch' }>) => void;
  onRespondApproval?: (m: Extract<ThreadMessage, { kind: 'approval' }>, decision: 'approve' | 'reject') => void;
  showActions?: boolean;
  actionsCopyText?: string;
  /** epoch ms for the action bar timestamp */
  actionsSentAt?: number;
}> = ({ m, onCorrect, onJumpSkill, onRegenerate, onApprovePatch, onRejectPatch, onRespondApproval, showActions, actionsCopyText, actionsSentAt }) => {
  const { openFile } = useFileViewer();
  if (m.kind === 'thinking') return <ThinkingBlock m={m} />;
  if (m.kind === 'toolrun') return <ToolCallCard m={m} onJumpSkill={onJumpSkill} />;
  if (m.kind === 'memory') {
    return (
      <>
        <MemoryIndicator m={m} onJumpSkill={onJumpSkill} />
        {showActions && (
          <MessageActions onRegenerate={onRegenerate} onCorrect={onCorrect} copyText={actionsCopyText} sentAt={actionsSentAt} />
        )}
      </>
    );
  }

  if (m.kind === 'approval')
    return (
      <>
        <ApprovalCard m={m} onRespond={(d) => onRespondApproval?.(m, d)} />
        {showActions && (
          <MessageActions onRegenerate={onRegenerate} onCorrect={onCorrect} copyText={actionsCopyText} sentAt={actionsSentAt} />
        )}
      </>
    );

  if (m.kind === 'patch')
    return (
      <>
        <PatchProposalCard m={m} onApprove={(b) => onApprovePatch?.(m, b)} onReject={() => onRejectPatch?.(m)} onJumpSkill={onJumpSkill} />
        {showActions && (
          <MessageActions onRegenerate={onRegenerate} onCorrect={onCorrect} copyText={actionsCopyText} sentAt={actionsSentAt} />
        )}
      </>
    );

  if (m.kind === 'plan') return <PlanCard m={m} />;
  if (m.kind === 'toolcall') return <BuiltinToolCard m={m} />;
  if (m.kind === 'tool_bash') return <BashCard m={m} />;
  if (m.kind === 'tool_read') return <ReadCard m={m} />;
  if (m.kind === 'tool_edit') return <EditCard m={m} />;
  if (m.kind === 'table') return <TableBlock m={m} />;

  // agent answer
  return (
    <div className="my-1">
      {m.usedSkill && (
        <div className="mb-1.5">
          <UsedSkillBadge skill={m.usedSkill} onJump={onJumpSkill} />
        </div>
      )}
      {/* Markdown render (bold/lists/headings/code blocks). The arbitrary-variant
          overrides lift the shared Markdown's body text to primary + trim the
          trailing block margin so it sits flush above the action bar. */}
      <div className="text-body-short-02 text-text-primary leading-relaxed break-words [&_p]:text-text-primary [&_li]:text-text-secondary [&_p]:mb-2 [&_p:last-child]:mb-0 [&>div>*:last-child]:mb-0">
        {m.text ? <Markdown source={m.text} onFileClick={openFile} streaming={m.streaming} /> : <TypingDots />}
      </div>
      {m.bullets && (
        <ul className="mt-2.5 pl-5 flex flex-col gap-1.5 list-disc">
          {m.bullets.map((b, i) => (
            <li key={i} className="text-body-short-02 text-text-secondary">
              <Rich text={b} />
            </li>
          ))}
        </ul>
      )}
      {m.action && (
        <div className="mt-3">
          <button className="inline-flex items-center gap-2 h-8 px-3 rounded-lg border border-border-strong-01 bg-transparent text-text-primary text-body-short-01 hover:bg-layer-hover-01 hover:border-border-strong-02 transition-colors">
            <Icon name={m.action.icon} size={15} />
            {m.action.label}
          </button>
        </div>
      )}
      {showActions && (
        <MessageActions onRegenerate={onRegenerate} onCorrect={onCorrect} copyText={actionsCopyText ?? m.text} sentAt={actionsSentAt} />
      )}
    </div>
  );
};

// ── turn (user bubble, or a grouped assistant turn under one identity) ─────
export interface ChatTurnProps {
  /** messages in this turn, with their original thread index */
  items: { m: ThreadMessage; index: number }[];
  onCorrect?: (text: string) => void;
  onJumpSkill?: (id: string) => void;
  onRegenerate?: () => void;
  /** approve a skill-improvement (patch) proposal — bumps version, keeps history */
  onApprovePatch?: (m: Extract<ThreadMessage, { kind: 'patch' }>, editedBody?: string) => void;
  /** reject a patch proposal — resolves the server-side parking immediately */
  onRejectPatch?: (m: Extract<ThreadMessage, { kind: 'patch' }>) => void;
  /** respond to a tool-execution approval (HITL) — posts back to the runtime */
  onRespondApproval?: (m: Extract<ThreadMessage, { kind: 'approval' }>, decision: 'approve' | 'reject') => void;
  /** thread index of the message to highlight (jumped to from a proposal source) */
  highlightIndex?: number | null;
  /** index → originating answer text; renders the action bar at that index */
  actionsCopyAt?: Record<number, string>;
  /** index → epoch ms of the agent answer that owns that action bar */
  actionsTimeAt?: Record<number, number>;
  /** Local LLM의 thinking trace를 대화에 표시할지 여부 */
  showThinking?: boolean;
}

export const ChatTurn: React.FC<ChatTurnProps> = ({
  items,
  onCorrect,
  onJumpSkill,
  onRegenerate,
  onApprovePatch,
  onRejectPatch,
  onRespondApproval,
  highlightIndex,
  actionsCopyAt = {},
  actionsTimeAt = {},
  showThinking = true,
}) => {
  const { i18n } = useTranslation('chat');
  const visibleItems = showThinking ? items : items.filter(({ m }) => m.kind !== 'thinking');
  const first = visibleItems[0]?.m;

  // thinking만 있던 중간 프레임은 설정이 꺼져 있으면 완전히 숨긴다.
  if (!first) return null;

  // user turn — a right-aligned bubble, no avatar/label (normal chat convention)
  if (first.kind === 'user') {
    const timeLabel = formatTime(first.sentAt, i18n.language);
    return (
      <div className="group/user flex flex-col items-end py-2.5" data-msg-index={visibleItems[0].index}>
        <div className="max-w-[76%]">
          <div className="rounded-xl border border-border-subtle-01 bg-layer-02/80 px-4 py-2.5 text-body-short-02 text-text-primary leading-relaxed break-words whitespace-pre-wrap">
            {first.text}
          </div>
          <div className="mt-1 flex items-center justify-between gap-2 pr-1">
            <UserCopyButton text={first.text} />
            {timeLabel && <span className="text-caption-01 text-text-helper select-none">{timeLabel}</span>}
          </div>
        </div>
      </div>
    );
  }

  // assistant turn — one "RICE" header over all its blocks
  return (
    <div className="flex gap-3 py-2.5 items-start">
      <Avatar kind="agent" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1.5">
          <span className="text-label-02 font-semibold text-text-primary">RICE</span>
          <AILabel />
        </div>
        <div className="flex flex-col">
          {visibleItems.map(({ m, index }) => (
            <div
              key={index}
              data-msg-index={index}
              className={highlightIndex === index ? 'ia-msg-hot rounded-lg' : ''}
            >
              <AssistantBlock
                m={m as AssistantMessage}
                onCorrect={onCorrect}
                onJumpSkill={onJumpSkill}
                onRegenerate={onRegenerate}
                onApprovePatch={onApprovePatch}
                onRejectPatch={onRejectPatch}
                onRespondApproval={onRespondApproval}
                showActions={index in actionsCopyAt}
                actionsCopyText={actionsCopyAt[index]}
                actionsSentAt={actionsTimeAt[index]}
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

// ── single-message dispatcher (kept for backward compatibility) ───────────
export interface ChatMessageProps {
  m: ThreadMessage;
  onCorrect?: (text: string) => void;
  onJumpSkill?: (id: string) => void;
  onRegenerate?: () => void;
  /** render the per-answer action bar below this message */
  showActions?: boolean;
  /** text the action bar's copy button should copy (the originating answer) */
  actionsCopyText?: string;
}

export const ChatMessage: React.FC<ChatMessageProps> = ({ m, showActions, actionsCopyText, ...handlers }) => (
  <ChatTurn
    items={[{ m, index: 0 }]}
    actionsCopyAt={showActions ? { 0: actionsCopyText ?? (m.kind === 'agent' ? m.text : '') } : {}}
    {...handlers}
  />
);

// Made with Bob
