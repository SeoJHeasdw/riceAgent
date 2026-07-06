import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Icon, Tag } from '../ui';
import { connectorIdForScope, type ThreadMessage, type ToolCall, type SkillRef, type EditDiffRow } from '../../lib/learningData';
import type { PlanStepData } from '../../lib/types';
import { useFileViewer } from '../../lib/fileViewer/FileViewerContext';
import { Markdown } from './Markdown';

// A workspace-file-looking inline code chip (e.g. `hello.txt`, `./src/a.ts`) →
// rendered clickable to open the shared file viewer. Tightly scoped to real file
// extensions so command/identifier chips (`db2.query`) stay plain.
const FILE_CHIP_RE = /^\.?\/?[\w.-]+\.(txt|md|markdown|json|jsonl|csv|tsv|log|ya?ml|py|js|ts|tsx|jsx|sh|bash|html?|css|scss|sql|toml|ini|env|xml|svg|pptx?|docx?|xlsx?|pdf)$/i;

// epoch ms → 시간 라벨 — undefined/0은 빈 문자열로 조용히 처리.
// 오늘: "HH:MM" / 올해 다른 날: "M월 D일 HH:MM" / 작년 이전: "YYYY년 M월 D일 HH:MM"
const formatTime = (ms?: number): string => {
  if (!ms) return '';
  const d = new Date(ms);
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  const sameYear = d.getFullYear() === now.getFullYear();
  const time = d.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });
  if (sameDay) return time;
  if (sameYear) return `${d.getMonth() + 1}월 ${d.getDate()}일 ${time}`;
  return `${d.getFullYear()}년 ${d.getMonth() + 1}월 ${d.getDate()}일 ${time}`;
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
                title="파일 열기"
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

// ── streaming typing indicator ───────────────────────────────
// Shown in an assistant bubble that has no text yet (live SSE turn in flight),
// so the user can tell RICE is generating a response and isn't stuck.
const TypingDots: React.FC = () => (
  <span className="inline-flex items-center gap-1 py-1" aria-label="응답 생성 중">
    {[0, 1, 2].map((i) => (
      <span
        key={i}
        className="w-1.5 h-1.5 rounded-full bg-icon-secondary animate-bounce"
        style={{ animationDelay: `${i * 0.15}s` }}
      />
    ))}
  </span>
);

// ── avatar ───────────────────────────────────────────────────
const Avatar: React.FC<{ kind: 'user' | 'agent' }> = ({ kind }) => {
  const agent = kind === 'agent';
  return (
    <div
      className={`w-7 h-7 flex-shrink-0 flex items-center justify-center rounded-lg text-white text-caption-01 font-semibold font-mono ${
        agent ? 'bg-blue-60' : 'bg-layer-03'
      }`}
    >
      {agent ? 'R' : 'U'}
    </div>
  );
};

// ── status glyph for a tool step ─────────────────────────────
const StepStatus: React.FC<{ status: ToolCall['status'] }> = ({ status }) => {
  if (status === 'running') {
    return <span className="ia-spinner w-3.5 h-3.5 flex-shrink-0" aria-label="실행 중" />;
  }
  if (status === 'error') {
    return <Icon name="warning" size={14} filled className="text-support-error flex-shrink-0" title="오류" />;
  }
  return <Icon name="check-circle" size={14} className="text-support-success flex-shrink-0" title="완료" />;
};

// ── one tool step (expandable: params + result) ──────────────
const ToolStep: React.FC<{ c: ToolCall }> = ({ c }) => {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
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
            title={`커넥터에서 보기: ${c.scope.name} · ${c.scope.mode}`}
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

  return (
    <div className="my-1.5 max-w-[640px]">
      {/* reuse banner — proves the loop made it faster */}
      {m.reuse && (
        <div className="flex items-center gap-2 px-3 py-1.5 bg-layer-02 border border-border-subtle-01 border-b-0 border-l-2 border-l-blue-50 rounded-t-lg">
          <Icon name="renew" size={14} className="text-icon-secondary flex-shrink-0" />
          <span className="text-caption-01 text-text-secondary">
            저장된 스킬 재사용 —{' '}
            <span className="text-text-primary font-semibold">
              {m.reuse.fromSteps} steps → {m.reuse.toSteps} steps
            </span>
          </span>
          <span className="ml-auto flex-shrink-0">
            <Tag type="green" size="sm">
              {pct}% 빠름
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
            title="이 스킬로 이동"
            className="inline-flex items-center gap-1 flex-shrink-0 border border-border-subtle-01 rounded px-1.5 py-0.5 text-caption-01 text-text-secondary hover:bg-layer-hover-02 transition-colors"
          >
            <Icon name="skill" size={12} />
            {m.usedSkill.label} {m.usedSkill.ver}
          </button>
        )}
        <span className="ml-auto text-caption-01 text-text-helper font-mono flex-shrink-0">
          {running ? '실행 중…' : `${m.steps} steps · ${m.time}`}
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
  return (
    <div className="my-1.5 max-w-[640px] border border-border-subtle-01 rounded-lg overflow-hidden bg-layer-01">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border-subtle-01">
        <Icon name="terminal" size={14} className="text-icon-secondary flex-shrink-0" />
        <span className="text-caption-01 font-semibold text-text-secondary flex-shrink-0">Bash</span>
        <span className="text-caption-01 text-text-helper truncate">{m.desc}</span>
        {running ? (
          <span className="ia-spinner w-3.5 h-3.5 ml-auto flex-shrink-0" aria-label="실행 중" />
        ) : m.status === 'error' ? (
          <Icon name="warning" size={13} filled className="text-support-error ml-auto flex-shrink-0" title="오류" />
        ) : (
          <Icon name="check-circle" size={13} className="text-support-success ml-auto flex-shrink-0" title="완료" />
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
              <Icon name="chevron-down" size={13} /> 출력 보기
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
const UsedSkillBadge: React.FC<{ skill: SkillRef; onJump?: (id: string) => void }> = ({ skill, onJump }) => (
  <button
    onClick={() => onJump?.(skill.id)}
    title="이 스킬을 학습 패널에서 보기"
    className="inline-flex items-center gap-1.5 bg-layer-01 border border-border-subtle-01 rounded-md pl-1.5 pr-2 py-0.5 text-caption-01 text-text-secondary hover:bg-layer-hover-01 transition-colors"
  >
    <Icon name="skill" size={13} />
    <span className="whitespace-nowrap">
      배운 스킬 사용 ·{' '}
      <span className="text-text-primary font-semibold">
        {skill.label} {skill.ver}
      </span>
    </span>
    <Icon name="arrow-up-right" size={12} className="opacity-70" />
  </button>
);

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
  const [text, setText] = useState('');

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
    const t = text.trim();
    if (!t) return;
    setCorrecting(false);
    setText('');
    onCorrect?.(t);
  };

  const timeLabel = formatTime(sentAt);

  return (
    <div className="mt-1.5">
      <div className="flex items-center gap-px -ml-2">
        <Btn
          icon={copied ? 'check' : 'copy'}
          label="답변 복사하기"
          tint="#78a9ff"
          onClick={copy}
          overlay={copied ? <span className="copy-toast">클립보드에 복사되었습니다</span> : null}
        />
        <Btn
          icon="thumbs-up"
          label="좋아요"
          tint="#42be65"
          active={vote === 'up'}
          onClick={() => setVote((v) => (v === 'up' ? null : 'up'))}
        />
        <Btn
          icon="thumbs-down"
          label="고칠 점 알려주기"
          tint="#ff8389"
          active={vote === 'down'}
          onClick={() => {
            const isDown = vote === 'down';
            setVote(isDown ? null : 'down');
            setCorrecting(!isDown);
          }}
        />
        <Btn icon="renew" label="답변 재생성" tint="#be95ff" onClick={() => onRegenerate?.()} />
        <div className="w-px h-4 bg-border-subtle-01 mx-1" />
        <Btn
          icon="edit"
          label="교정해서 가르치기"
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
            <span className="text-caption-01 font-semibold text-text-primary">교정해서 가르치기</span>
            <span className="ml-auto">
              <AILabel />
            </span>
          </div>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder="어떻게 고치면 좋을까요? (예: APAC을 항상 맨 위에)"
            className="w-full resize-none border-none outline-none bg-transparent text-text-primary text-body-short-01 leading-5 px-2.5 py-2 h-[52px]"
          />
          <div className="flex items-center gap-2 px-2 pb-2">
            <span className="text-caption-01 text-text-helper">저장하면 에이전트가 기억하고 다음부터 반영합니다.</span>
            <div className="flex-1" />
            <Button
              kind="ghost"
              size="sm"
              className="!h-9"
              onClick={() => {
                setCorrecting(false);
                setText('');
              }}
            >
              취소
            </Button>
            <Button kind="primary" size="sm" className="!h-9" icon={<Icon name="send" size={14} />} onClick={submit}>
              가르치기
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
}> = ({ m, onJumpSkill }) => (
  <div className="my-1 max-w-[640px]">
    <div className="ai-tile flex gap-2.5 items-start px-3.5 py-3">
      <Icon name="bookmark" size={16} filled className="text-blue-40 mt-0.5 flex-shrink-0" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-body-short-01 font-semibold text-text-primary">{m.text}</span>
          <span className="text-caption-01 text-text-helper">AI 메모리에 기록됨</span>
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
            {m.target.label}에 반영됨 <Icon name="arrow-up-right" size={12} className="opacity-80" />
          </button>
        )}
      </div>
    </div>
  </div>
);

// ── collapsible thinking trace ───────────────────────────────
const ThinkingBlock: React.FC<{ m: Extract<ThreadMessage, { kind: 'thinking' }> }> = ({ m }) => {
  // While streaming, default to open so the live reasoning is visible (the
  // "motion"); once done it auto-collapses — unless the user toggled it.
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const open = userOpen ?? !!m.streaming;
  const steps = m.steps ?? [];
  return (
    <div className="my-1.5 max-w-[640px]">
      <button
        onClick={() => setUserOpen(!open)}
        className="flex items-center gap-1.5 text-caption-01 text-text-helper hover:text-text-secondary transition-colors"
      >
        <Icon name="idea" size={14} className={`opacity-70 ${m.streaming ? 'animate-pulse' : ''}`} />
        <span>{m.streaming ? '생각 중…' : m.duration ? `${m.duration} 동안 생각함` : '추론 과정'}</span>
        <Icon name={open ? 'chevron-up' : 'chevron-down'} size={14} />
      </button>
      {open && m.text != null && (
        <div className="mt-2 pl-1 text-caption-01 text-text-helper whitespace-pre-wrap leading-relaxed border-l-2 border-border-subtle-01 pl-3">
          {m.text || (m.streaming ? <TypingDots /> : '…')}
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
  if (status === 'done') return <Icon name="check" size={13} className="text-support-success flex-shrink-0" />;
  if (status === 'failed') return <Icon name="close" size={13} className="text-support-error flex-shrink-0" />;
  return (
    <span className="w-[13px] h-[13px] flex items-center justify-center flex-shrink-0">
      {status === 'in_progress' ? (
        <span className="w-2 h-2 rounded-full bg-blue-40 animate-pulse" />
      ) : status === 'skipped' ? (
        <span className="w-1.5 h-px bg-text-helper" />
      ) : (
        <span className="w-2 h-2 rounded-full border border-border-strong-01" />
      )}
    </span>
  );
};

const PlanCard: React.FC<{ m: Extract<ThreadMessage, { kind: 'plan' }> }> = ({ m }) => {
  const doneCount = m.steps.filter((s) => s.status === 'done' || s.status === 'skipped').length;
  const failed = m.steps.some((s) => s.status === 'failed');
  const completed = m.status === 'completed';
  return (
    <div className="my-1.5 max-w-[640px]">
      <div className="rounded-xl border border-border-subtle-01 border-l-2 border-l-blue-40 bg-layer-01 px-4 py-3">
        <div className="flex items-center gap-2">
          <Icon name="flow" size={15} className="text-blue-40 flex-shrink-0" />
          <span className="text-body-short-01 font-semibold text-text-primary">실행 계획</span>
          {m.revision > 1 && (
            <span className="text-label-micro text-text-helper border border-border-subtle-01 rounded px-1 py-px">
              수정 {m.revision - 1}회
            </span>
          )}
          <span
            className={`ml-auto text-caption-01 flex-shrink-0 ${
              completed ? 'text-support-success' : failed ? 'text-support-error' : 'text-text-helper'
            }`}
          >
            {completed ? '완료' : `${doneCount}/${m.steps.length}`}
          </span>
        </div>
        <div className="mt-1 text-caption-01 text-text-secondary">{m.goal}</div>
        <div className="mt-2.5 flex flex-col gap-1.5">
          {m.steps.map((s) => {
            const secondary = s.note || (s.status === 'in_progress' || s.status === 'pending' ? s.detail : '');
            return (
              <div key={s.id} className="flex items-start gap-2 min-w-0">
                <span className="mt-[3px]">
                  <PlanStepMarker status={s.status} />
                </span>
                <div className="min-w-0">
                  <span
                    className={`text-caption-01 ${
                      s.status === 'in_progress'
                        ? 'text-text-primary font-medium'
                        : s.status === 'done'
                          ? 'text-text-secondary'
                          : s.status === 'skipped'
                            ? 'text-text-helper line-through'
                            : s.status === 'failed'
                              ? 'text-support-error'
                              : 'text-text-secondary'
                    }`}
                  >
                    {s.title}
                  </span>
                  {secondary ? <div className="text-label-micro text-text-helper truncate">{secondary}</div> : null}
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
// Render a flat object as aligned key→value rows (replaces a raw JSON dump). A
// `path` value is rendered as a clickable file link.
const KeyVals: React.FC<{ obj: Record<string, unknown>; onFile: (p: string) => void }> = ({ obj, onFile }) => (
  <div className="flex flex-col gap-1">
    {Object.entries(obj).map(([k, v]) => {
      const val = typeof v === 'string' ? v : JSON.stringify(v);
      return (
        <div key={k} className="flex gap-2 text-caption-01">
          <span className="text-text-helper font-mono min-w-[80px] flex-shrink-0">{k}</span>
          {k === 'path' && typeof v === 'string' ? (
            <button onClick={() => onFile(v)} className="text-blue-40 font-mono break-all text-left hover:underline">
              {val}
            </button>
          ) : (
            <span className="text-text-secondary font-mono break-all whitespace-pre-wrap">{val}</span>
          )}
        </div>
      );
    })}
  </div>
);

const BuiltinToolCard: React.FC<{ m: Extract<ThreadMessage, { kind: 'toolcall' }> }> = ({ m }) => {
  const [open, setOpen] = useState(false);
  const { openFile } = useFileViewer();
  const path = typeof m.args.path === 'string' ? (m.args.path as string) : null;
  const dot =
    m.status === 'running' ? 'bg-support-warning animate-pulse' : m.status === 'error' ? 'bg-support-error' : 'bg-support-success';

  // tool results arrive as a string; pretty-render it as key→values when it's a JSON object
  const resultObj: Record<string, unknown> | null = (() => {
    if (m.result == null) return null;
    try {
      const o = JSON.parse(m.result);
      return o && typeof o === 'object' && !Array.isArray(o) ? (o as Record<string, unknown>) : null;
    } catch {
      return null;
    }
  })();

  return (
    <div className="my-1.5 max-w-[640px]">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 w-full text-left text-caption-01 text-text-secondary hover:text-text-primary transition-colors"
      >
        <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dot}`} />
        <span className="font-mono text-blue-40">{m.tool}</span>
        {path && <span className="font-mono text-text-helper truncate">{path}</span>}
        <span className="text-text-helper">
          {m.status === 'running' ? '실행 중…' : m.status === 'error' ? '실패' : '완료'}
        </span>
        <Icon name={open ? 'chevron-up' : 'chevron-down'} size={14} className="ml-auto flex-shrink-0" />
      </button>
      {open && (
        <div className="mt-2 ml-3.5 border-l-2 border-border-subtle-01 pl-3 flex flex-col gap-2.5">
          {Object.keys(m.args).length > 0 && (
            <div>
              <div className="text-label-micro uppercase tracking-wider text-text-helper mb-1">arguments</div>
              <KeyVals obj={m.args} onFile={openFile} />
            </div>
          )}
          {(m.result != null || m.error != null) && (
            <div>
              <div className="text-label-micro uppercase tracking-wider text-text-helper mb-1">
                {m.error != null ? 'error' : 'result'}
              </div>
              {m.error != null ? (
                <span className="text-caption-01 font-mono text-support-error break-all">{m.error}</span>
              ) : resultObj ? (
                <KeyVals obj={resultObj} onFile={openFile} />
              ) : (
                <pre className="text-caption-01 font-mono text-text-secondary whitespace-pre-wrap break-all m-0">
                  {m.result}
                </pre>
              )}
            </div>
          )}
          {path && (
            <button
              onClick={() => openFile(path)}
              className="self-start inline-flex items-center gap-1.5 text-caption-01 text-link-primary hover:underline"
            >
              <Icon name="document" size={13} />
              {path} 열어보기
            </button>
          )}
        </div>
      )}
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
  const [body, setBody] = useState(m.body ?? '');
  return (
    <div className="my-1.5 max-w-[620px]">
      {/* 도구 실행 승인은 AI 문법이 아니라 warning 문법 — ai-tile 금지 */}
      <div className="rounded-xl border border-border-subtle-01 border-l-2 border-l-support-warning bg-layer-01 p-4">
        <div className="flex items-center gap-2 mb-2">
          <Icon name="warning" size={15} filled className="text-orange-40 flex-shrink-0" />
          <span className="text-body-short-01 font-semibold text-text-primary">실행 승인이 필요해요</span>
        </div>
        <div className="flex items-center gap-2 mb-2 flex-wrap">
          <span className="text-caption-01 font-mono text-blue-40 flex-shrink-0">{m.tool}</span>
          <span className="text-body-short-01 text-text-primary">{m.label}</span>
          <span className="inline-flex items-center gap-1 flex-shrink-0 border border-border-subtle-01 px-1.5 py-0.5 rounded text-label-micro font-mono text-text-helper">
            <Icon name="lock" size={10} className="opacity-70" />
            {m.scope.name} · {m.scope.mode}
          </span>
        </div>
        <p className="text-caption-01 text-text-secondary mb-3">{m.reason}</p>
        {(m.fields || m.body) && (
          <div className="mb-3 border border-border-subtle-01 rounded-lg overflow-hidden bg-layer-01">
            <div className="px-3 py-2 border-b border-border-subtle-01 flex items-center gap-1.5">
              <Icon name="send" size={12} className="text-icon-secondary opacity-70" />
              <span className="text-caption-01 font-semibold text-text-secondary">메일 미리보기</span>
            </div>
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
              <div className="border-t border-border-subtle-01">
                <textarea
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                  disabled={state !== 'pending'}
                  className="w-full px-3 py-2.5 text-caption-01 text-text-secondary font-sans leading-relaxed resize-none bg-transparent border-0 outline-none focus:ring-1 focus:ring-focus disabled:text-text-helper disabled:cursor-default"
                  style={{ minHeight: '120px', maxHeight: '220px' }}
                  rows={6}
                  spellCheck={false}
                />
              </div>
            )}
          </div>
        )}
        {state === 'pending' ? (
          <div className="flex items-center gap-2">
            <span className="text-caption-01 text-text-helper">외부에 영향을 주는 작업입니다.</span>
            <div className="flex-1" />
            <Button
              kind="ghost"
              size="sm"
              onClick={() => {
                setState('denied');
                onRespond?.('reject');
              }}
            >
              거부
            </Button>
            <Button
              kind="primary"
              size="sm"
              icon={<Icon name="check" size={14} />}
              onClick={() => {
                setState('approved');
                onRespond?.('approve');
              }}
            >
              승인하고 실행
            </Button>
          </div>
        ) : state === 'approved' ? (
          <span className="inline-flex items-center gap-1.5 text-caption-01 text-support-success">
            <Icon name="check-circle" size={13} /> 승인됨 · {m.approvedText ?? '실행했어요'}
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-caption-01 text-text-helper">
            <Icon name="close" size={13} /> 거부됨 · {m.deniedText ?? '실행하지 않았어요'}
          </span>
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

  return (
    <div className="my-1.5 max-w-[640px]">
      {/* 스킬 개선 제안 = AI 순간 — ai-tile 그라디언트 보더 고정(색 오버라이드 금지) */}
      <div className="ai-tile p-4">
        <div className="flex items-center gap-2 mb-2">
          <Icon name="renew" size={15} className="text-purple-40 flex-shrink-0" />
          <span className="text-body-short-01 font-semibold text-text-primary">스킬 개선 제안</span>
          <Tag type="purple" size="sm">
            {m.triggerLabel}
          </Tag>
        </div>

        {/* skill + version transition */}
        <div className="flex items-center gap-2 mb-2 flex-wrap">
          <Icon name="skill" size={14} className="text-icon-secondary flex-shrink-0" />
          <span className="text-body-short-01 text-text-primary">{m.skillName}</span>
          <span className="inline-flex items-center gap-1.5 flex-shrink-0 font-mono text-caption-01">
            <span className="text-text-helper line-through">{m.fromVer}</span>
            <Icon name="arrow-right" size={12} className="text-icon-secondary" />
            <span className="text-link-primary font-semibold">{m.toVer}</span>
          </span>
        </div>

        {/* changelog */}
        <p className="text-caption-01 text-text-secondary mb-3 flex items-start gap-1.5">
          <Icon name="edit" size={13} className="text-icon-secondary mt-0.5 flex-shrink-0 opacity-80" />
          {m.changelog}
        </p>

        {/* Phase D 가드 경고 — 시크릿 자동 마스킹 등 */}
        {!!m.warnings?.length && (
          <div className="mb-3 flex items-start gap-1.5 px-2.5 py-1.5 rounded-md bg-support-warning bg-opacity-10 border border-support-warning border-opacity-30">
            <Icon name="warning" size={13} className="text-support-warning mt-0.5 flex-shrink-0" />
            <p className="text-caption-01 text-text-secondary">{m.warnings.join(' · ')}</p>
          </div>
        )}

        {/* diff (or editable body) */}
        <div className="mb-3 border border-border-subtle-01 rounded-lg overflow-hidden bg-layer-01">
          <div className="px-3 py-2 border-b border-border-subtle-01 flex items-center gap-1.5">
            <Icon name="renew" size={12} className="text-icon-secondary opacity-70" />
            <span className="text-caption-01 font-semibold text-text-secondary">
              {editing ? '본문 편집' : '본문 변경 (diff)'}
            </span>
            {state === 'pending' && (
              <button
                onClick={() => setEditing((e) => !e)}
                className="ml-auto text-caption-01 text-link-primary hover:underline inline-flex items-center gap-1"
              >
                <Icon name={editing ? 'view' : 'edit'} size={12} />
                {editing ? 'diff 보기' : '편집'}
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
            <div className="py-1 text-label-micro font-mono leading-[18px] max-h-[260px] overflow-y-auto">
              {m.diff.map((line, i) => (
                <DiffLine key={i} line={line} />
              ))}
            </div>
          )}
        </div>

        {state === 'pending' ? (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-caption-01 text-text-helper">
              승인하면 이전 버전은 히스토리에 보관돼요 (롤백 가능).
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
              거부
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
              {edited ? '편집 후 적용' : '승인하고 적용'}
            </Button>
          </div>
        ) : state === 'approved' ? (
          <div className="flex items-center gap-2.5 flex-wrap">
            <span className="inline-flex items-center gap-1.5 text-caption-01 text-support-success">
              <Icon name="check-circle" size={13} /> {m.toVer}로 개선됨{edited ? ' (편집 반영)' : ''} · 이전 버전 보관
            </span>
            <div className="flex-1" />
            <Button kind="ghost" size="sm" icon={<Icon name="arrow-up-right" size={14} />} onClick={() => onJumpSkill?.(m.skillId)}>
              내 스킬에서 보기
            </Button>
          </div>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-caption-01 text-text-helper">
            <Icon name="close" size={13} /> 거부됨 · 본문은 그대로예요 ({m.fromVer})
          </span>
        )}
      </div>
    </div>
  );
};

// ── skill proposal (human-in-the-loop save, LL-2) ────────────
// the loop proposes a new skill; the user confirms before it is saved.
const SkillProposalCard: React.FC<{
  m: Extract<ThreadMessage, { kind: 'skill' }>;
  onJumpSkill?: (id: string) => void;
  /** actually persist the skill to the learning store */
  onSave?: () => void;
}> = ({ m, onJumpSkill, onSave }) => {
  const [saved, setSaved] = useState<boolean | null>(null);
  return (
    <div className="ai-tile p-4">
      <div className="flex items-center gap-2 mb-1.5">
        <Icon name="skill" size={14} className="text-icon-primary" />
        <span className="font-semibold text-text-primary text-body-short-01">
          {m.skill} {m.ver}
        </span>
        {saved !== false && (
          <Tag type={saved === true ? 'green' : 'purple'} size="sm">
            {saved === true ? '저장됨' : '제안됨'}
          </Tag>
        )}
      </div>
      <div className="text-body-short-01 font-semibold text-text-primary mb-1.5">
        {saved === null ? '이 작업을 스킬로 저장할까요?' : m.title}
      </div>
      <p className="text-body-short-01 text-text-secondary mb-2.5">{m.desc}</p>
      {saved === null ? (
        <div className="flex items-center gap-2">
          <span className="text-caption-01 text-text-helper">{m.meta}</span>
          <div className="flex-1" />
          <Button kind="ghost" size="sm" onClick={() => setSaved(false)}>
            이번만 쓰기
          </Button>
          <Button
            kind="primary"
            size="sm"
            icon={<Icon name="bookmark" size={14} />}
            onClick={() => {
              setSaved(true);
              onSave?.();
            }}
          >
            스킬로 저장
          </Button>
        </div>
      ) : saved === true ? (
        <div className="flex items-center gap-2.5">
          <span className="inline-flex items-center gap-1.5 text-caption-01 text-support-success">
            <Icon name="check-circle" size={13} /> 스킬로 저장됨 · 다음 세션에서도 사용 가능
          </span>
          <div className="flex-1" />
          <Button kind="ghost" size="sm" icon={<Icon name="arrow-up-right" size={14} />} onClick={() => onJumpSkill?.(m.skillId)}>
            패널에서 보기
          </Button>
        </div>
      ) : (
        <span className="text-caption-01 text-text-helper">저장하지 않았어요 · 비슷한 작업이 반복되면 다시 제안할게요.</span>
      )}
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
  onSaveSkill?: (m: Extract<ThreadMessage, { kind: 'skill' }>) => void;
  onApprovePatch?: (m: Extract<ThreadMessage, { kind: 'patch' }>, editedBody?: string) => void;
  onRejectPatch?: (m: Extract<ThreadMessage, { kind: 'patch' }>) => void;
  onRespondApproval?: (m: Extract<ThreadMessage, { kind: 'approval' }>, decision: 'approve' | 'reject') => void;
  showActions?: boolean;
  actionsCopyText?: string;
  /** epoch ms for the action bar timestamp */
  actionsSentAt?: number;
}> = ({ m, onCorrect, onJumpSkill, onRegenerate, onSaveSkill, onApprovePatch, onRejectPatch, onRespondApproval, showActions, actionsCopyText, actionsSentAt }) => {
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

  if (m.kind === 'skill') {
    const skillMsg = m;
    return (
      <div className="my-1 max-w-[620px]">
        <SkillProposalCard m={skillMsg} onJumpSkill={onJumpSkill} onSave={() => onSaveSkill?.(skillMsg)} />
        {showActions && (
          <MessageActions onRegenerate={onRegenerate} onCorrect={onCorrect} copyText={actionsCopyText} sentAt={actionsSentAt} />
        )}
      </div>
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
        {m.text ? <Markdown source={m.text} onFileClick={openFile} /> : <TypingDots />}
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
          <button className="inline-flex items-center gap-2 h-8 px-3 rounded-lg border border-white/30 bg-transparent text-text-primary text-body-short-01 hover:bg-white/5 hover:border-white/50 transition-colors">
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
  onSaveSkill?: (m: Extract<ThreadMessage, { kind: 'skill' }>) => void;
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
}

export const ChatTurn: React.FC<ChatTurnProps> = ({
  items,
  onCorrect,
  onJumpSkill,
  onRegenerate,
  onSaveSkill,
  onApprovePatch,
  onRejectPatch,
  onRespondApproval,
  highlightIndex,
  actionsCopyAt = {},
  actionsTimeAt = {},
}) => {
  const first = items[0].m;

  // user turn — a right-aligned bubble, no avatar/label (normal chat convention)
  if (first.kind === 'user') {
    const timeLabel = formatTime(first.sentAt);
    return (
      <div className="flex flex-col items-end gap-1 py-2.5" data-msg-index={items[0].index}>
        <div className="max-w-[80%] bg-layer-02 rounded-2xl px-4 py-2.5 text-body-short-02 text-text-primary leading-relaxed break-words whitespace-pre-wrap">
          {first.text}
        </div>
        {timeLabel && (
          <span className="text-caption-01 text-text-helper select-none pr-1">{timeLabel}</span>
        )}
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
          {items.map(({ m, index }) => (
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
                onSaveSkill={onSaveSkill}
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
