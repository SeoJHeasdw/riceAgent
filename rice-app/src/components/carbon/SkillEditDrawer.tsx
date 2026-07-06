import React, { useEffect, useRef, useState } from 'react';
import { Button, Icon } from '../ui';
import { dialogBackdropCls } from '../ui/surfaces';
import type { Skill, SkillDetail } from '../../lib/learningData';
import type { SkillFileLanguage } from '../../lib/types';
import { ownedSkillFiles } from '../../lib/skillBridge';

/**
 * SkillEditDrawer — 채팅 매개 스킬 편집 (Phase 4).
 *
 * 원시 md를 자유 편집하게 두면 학습한 규칙·버전·상태가 뭉개지므로, 모든 소스 변경을
 * "지시 → patch diff 제안 → 승인" 단일 경로로 통과시킨다(채팅 중 에이전트가 거는
 * 기존 patch 흐름과 같은 의미). 승인 시 version++ 되고 이전 버전은 히스토리에 보관된다.
 * 출력은 항상 patch 카드(commit 게이트)이고, 그 위의 대화는 반복 지시를 위한 것.
 */

export interface EditPatch {
  fromVer: string;
  toVer: string;
  /** 한 줄 changelog (= 사용자 지시) */
  changelog: string;
  /** 본문 변경 diff (렌더용) */
  diff: { t: 'add' | 'remove' | 'ctx'; text: string }[];
  /** 승인 시 저장될 대상 파일 전체 (편집 모드에서 수정 가능) */
  proposedBody: string;
  /** 편집 대상 파일 경로 (SKILL.md 또는 번들 내 다른 파일) */
  targetPath: string;
  /** 표시용 파일명 */
  targetLabel: string;
}

type Msg = { role: 'user' | 'agent'; text: string };

const SECTION = '## 내 개선 (편집 어시스턴트)';

const nextVer = (v: string): string => {
  const m = v.match(/\d+/);
  return m ? v.replace(/\d+/, String(Number(m[0]) + 1)) : `${v}.1`;
};

const isSkillMdPath = (p: string) => p.toLowerCase().endsWith('skill.md');

/** 파일 언어에 맞는 한 줄 주석 — 코드/문서 파일에 지시를 안전하게 주석으로 남긴다. */
const commentLine = (lang: SkillFileLanguage, text: string): string => {
  switch (lang) {
    case 'html':
    case 'markdown':
      return `<!-- ${text} -->`;
    case 'json':
      return `// ${text}`;
    default:
      return `# ${text}`; // shell · python · text
  }
};

/**
 * 지시 → 대상 파일에 대한 patch 제안을 합성한다.
 * SKILL.md면 "내 개선" 섹션에 지침 한 줄을 더하고, 그 외 파일이면 해당 언어 주석으로
 * 지시를 남긴다(실 LLM 편집의 mock — 흐름: 지시→diff→승인→version++).
 */
function buildPatch(instruction: string, skill: Skill, detail: SkillDetail, targetPath?: string): EditPatch {
  const text = instruction.trim();
  const { files } = ownedSkillFiles(skill, detail);
  const skillMd = files.find((f) => isSkillMdPath(f.path));
  const target = (targetPath ? files.find((f) => f.path === targetPath) : undefined) ?? skillMd ?? files[0];
  const current = (target?.content ?? '').replace(/\s*$/, '');
  const diff: EditPatch['diff'] = [];
  const base = {
    fromVer: skill.ver,
    toVer: nextVer(skill.ver),
    changelog: text,
    targetPath: target?.path ?? 'SKILL.md',
    targetLabel: target?.path.split('/').pop() ?? 'SKILL.md',
  };

  // ── SKILL.md: 지침 한 줄을 "내 개선" 섹션에 추가 (분석 sidecar는 저장 후 재생성) ──
  if (target && isSkillMdPath(target.path)) {
    const bullet = `- ${text}`;
    const lines = current.split('\n');
    const headerIdx = lines.findIndex((l) => l.trim() === SECTION);
    let proposedBody: string;
    if (headerIdx !== -1) {
      let at = headerIdx + 1;
      while (at < lines.length && (lines[at].startsWith('- ') || lines[at].trim() === '')) at++;
      lines.splice(at, 0, bullet);
      proposedBody = lines.join('\n') + '\n';
      diff.push({ t: 'ctx', text: SECTION });
      diff.push({ t: 'add', text: bullet });
    } else {
      proposedBody = `${current}\n\n${SECTION}\n\n${bullet}\n`;
      lines.slice(-2).forEach((l) => diff.push({ t: 'ctx', text: l }));
      diff.push({ t: 'add', text: '' });
      diff.push({ t: 'add', text: SECTION });
      diff.push({ t: 'add', text: '' });
      diff.push({ t: 'add', text: bullet });
    }
    return { ...base, diff, proposedBody };
  }

  // ── 그 외 번들 파일: 언어 주석으로 지시를 남긴다 ──
  const comment = commentLine(target?.language ?? 'text', `(편집 어시스턴트) ${text}`);
  current.split('\n').slice(-2).forEach((l) => diff.push({ t: 'ctx', text: l }));
  diff.push({ t: 'add', text: comment });
  return { ...base, diff, proposedBody: `${current}\n${comment}\n` };
}

const DiffLine: React.FC<{ line: EditPatch['diff'][number] }> = ({ line }) => {
  const gutter = line.t === 'add' ? '+' : line.t === 'remove' ? '−' : ' ';
  const cls =
    line.t === 'add'
      ? 'bg-diff-add-bg text-diff-add-text border-l-2 border-l-diff-add-accent'
      : line.t === 'remove'
        ? 'bg-diff-remove-bg text-diff-remove-text border-l-2 border-l-diff-remove-accent'
        : 'text-text-helper border-l-2 border-l-transparent';
  return (
    <div className={`flex gap-2 px-2.5 py-0.5 ${cls}`}>
      <span className="select-none opacity-70 w-2 flex-shrink-0 text-center">{gutter}</span>
      <span className="whitespace-pre-wrap break-all">{line.text || ' '}</span>
    </div>
  );
};

const SUGGESTIONS = ['출력은 항상 한국어로', '결과 맨 위에 요약 한 줄 추가', '실행 전 확인 단계 추가'];

export const SkillEditDrawer: React.FC<{
  open: boolean;
  skill: Skill;
  detail: SkillDetail;
  /** 편집 대상 파일 경로 — 워크스페이스에서 열린 파일. 미지정 시 SKILL.md(지침). */
  targetPath?: string;
  onClose: () => void;
  /** 승인 → 버전++·대상 파일 갱신. editedBody가 있으면 그 본문으로 저장. */
  onApply: (patch: EditPatch, editedBody?: string) => void;
}> = ({ open, skill, detail, targetPath, onClose, onApply }) => {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [pending, setPending] = useState<EditPatch | null>(null);
  const [editing, setEditing] = useState(false);
  const [body, setBody] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);

  // 어떤 파일을 편집 중인지 (헤더·안내·제안에 사용)
  const { files } = ownedSkillFiles(skill, detail);
  const targetFile = (targetPath ? files.find((f) => f.path === targetPath) : undefined) ?? files.find((f) => isSkillMdPath(f.path)) ?? files[0];
  const targetName = targetFile?.path.split('/').pop() ?? 'SKILL.md';
  const targetIsSkillMd = isSkillMdPath(targetFile?.path ?? 'SKILL.md');

  // 스킬·대상 파일이 바뀌거나 드로어를 다시 열면 대화 초기화
  useEffect(() => {
    setMessages([]);
    setInput('');
    setPending(null);
    setEditing(false);
  }, [skill.id, open, targetPath]);

  // 새 메시지/제안이 생기면 맨 아래로 스크롤
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, pending]);

  if (!open) return null;

  const propose = () => {
    const text = input.trim();
    if (!text || pending) return;
    const patch = buildPatch(text, skill, detail, targetPath);
    setMessages((m) => [
      ...m,
      { role: 'user', text },
      { role: 'agent', text: `이렇게 바꾸면 다음 실행부터 반영돼요. 검토하고 적용하세요 — ${patch.fromVer} → ${patch.toVer}.` },
    ]);
    setBody(patch.proposedBody);
    setEditing(false);
    setPending(patch);
    setInput('');
  };

  const apply = () => {
    if (!pending) return;
    const edited = editing && body !== pending.proposedBody;
    onApply(pending, edited ? body : undefined);
    setMessages((m) => [
      ...m,
      { role: 'agent', text: `${pending.toVer}로 적용했어요${edited ? ' (편집 반영)' : ''}. 이전 버전은 히스토리에 보관됐어요.` },
    ]);
    setPending(null);
    setEditing(false);
  };

  const dismiss = () => {
    setMessages((m) => [...m, { role: 'agent', text: '제안을 취소했어요. 다시 지시해 주세요.' }]);
    setPending(null);
    setEditing(false);
  };

  return (
    <div className="fixed inset-0 z-[60] flex">
      {/* backdrop */}
      <div onClick={onClose} className={dialogBackdropCls} />

      {/* right drawer */}
      <div className="relative ml-auto h-full w-[440px] max-w-full bg-layer-01 border-l border-border-subtle-01 shadow-elevation-3 z-[61] flex flex-col animate-slide-in-right">
        {/* header */}
        <div className="flex-shrink-0 flex items-start gap-2.5 px-5 py-4 border-b border-border-subtle-01">
          <div className="w-8 h-8 flex-shrink-0 flex items-center justify-center rounded-lg bg-purple-60/15">
            <Icon name="edit" size={16} className="text-purple-60" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-body-short-01 font-semibold text-text-primary">스킬 편집 어시스턴트</div>
            <div className="text-caption-01 text-text-helper truncate">
              {skill.name} · 편집 대상 <span className="font-mono text-text-secondary">{targetIsSkillMd ? 'SKILL.md(지침)' : targetName}</span> · 새 버전으로 저장
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="닫기"
            className="w-7 h-7 flex items-center justify-center rounded-md text-icon-secondary hover:bg-layer-hover-01 hover:text-text-primary transition-colors flex-shrink-0"
          >
            <Icon name="close" size={16} />
          </button>
        </div>

        {/* conversation */}
        <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto px-5 py-4 flex flex-col gap-3">
          {messages.length === 0 && !pending && (
            <div className="rounded-lg border border-border-subtle-01 bg-background px-3.5 py-3">
              <div className="flex items-start gap-2">
                <Icon name="idea" size={15} className="text-icon-primary flex-shrink-0 mt-0.5" />
                <div className="text-caption-01 text-text-secondary leading-5">
                  {targetIsSkillMd ? (
                    <>바꾸고 싶은 점을 자연어로 적어주세요. 제안을 <span className="text-text-primary font-semibold">diff로 검토한 뒤 적용</span>하면 새 버전으로 저장돼요. 작동 방식 분석은 저장 후 다시 만들 수 있어요.</>
                  ) : (
                    <><span className="font-mono text-text-secondary">{targetName}</span> 를 어떻게 바꿀지 적어주세요. 제안을 <span className="text-text-primary font-semibold">diff로 검토한 뒤 적용</span>하면 새 버전으로 저장돼요.</>
                  )}
                </div>
              </div>
            </div>
          )}

          {messages.map((m, i) =>
            m.role === 'user' ? (
              <div key={i} className="self-end max-w-[85%] rounded-2xl rounded-br-sm bg-interactive text-text-on-color px-3.5 py-2 text-body-short-01">
                {m.text}
              </div>
            ) : (
              <div key={i} className="self-start max-w-[90%] text-body-short-01 text-text-secondary leading-5 flex items-start gap-2">
                <Icon name="skill" size={15} className="text-icon-primary flex-shrink-0 mt-0.5" />
                <span>{m.text}</span>
              </div>
            ),
          )}

          {/* pending patch card */}
          {pending && (
            <div className="ai-tile overflow-hidden">
              <div className="flex items-center gap-2 px-3.5 py-2.5 border-b border-border-subtle-01">
                <Icon name="renew" size={14} className="text-purple-40 flex-shrink-0" />
                <span className="text-body-short-01 font-semibold text-text-primary">스킬 개선 제안</span>
                <span className="ml-auto inline-flex items-center gap-1.5 font-mono text-caption-01">
                  <span className="text-text-helper line-through">{pending.fromVer}</span>
                  <Icon name="arrow-right" size={12} className="text-icon-secondary" />
                  <span className="text-link-primary font-semibold">{pending.toVer}</span>
                </span>
              </div>

              <p className="text-caption-01 text-text-secondary px-3.5 pt-2.5 flex items-start gap-1.5">
                <Icon name="edit" size={13} className="text-icon-secondary mt-0.5 flex-shrink-0 opacity-80" />
                {pending.changelog}
              </p>

              {/* diff / editable body */}
              <div className="m-3.5 border border-border-subtle-01 rounded-lg overflow-hidden bg-layer-01">
                <div className="px-3 py-1.5 border-b border-border-subtle-01 flex items-center gap-1.5">
                  <Icon name="renew" size={12} className="text-icon-secondary opacity-70" />
                  <span className="text-caption-01 font-semibold text-text-secondary">{editing ? 'SKILL.md 편집' : '본문 변경 (diff)'}</span>
                  <button
                    onClick={() => setEditing((e) => !e)}
                    className="ml-auto text-caption-01 text-link-primary hover:underline inline-flex items-center gap-1"
                  >
                    <Icon name={editing ? 'view' : 'edit'} size={12} />
                    {editing ? 'diff 보기' : '편집'}
                  </button>
                </div>
                {editing ? (
                  <textarea
                    value={body}
                    onChange={(e) => setBody(e.target.value)}
                    spellCheck={false}
                    className="w-full px-3 py-2.5 text-label-micro font-mono text-text-secondary leading-relaxed resize-none bg-transparent border-0 outline-none focus:ring-1 focus:ring-focus"
                    style={{ minHeight: '160px', maxHeight: '300px' }}
                  />
                ) : (
                  <div className="py-1 text-label-micro font-mono leading-[18px] max-h-[300px] overflow-y-auto">
                    {pending.diff.map((line, i) => (
                      <DiffLine key={i} line={line} />
                    ))}
                  </div>
                )}
              </div>

              <div className="flex items-center gap-2 px-3.5 pb-3.5">
                <Button kind="ghost" size="sm" onClick={dismiss}>
                  취소
                </Button>
                <div className="flex-1" />
                <Button kind="primary" size="sm" icon={<Icon name="check" size={14} />} onClick={apply}>
                  {editing && body !== pending.proposedBody ? '편집 후 적용' : '적용'}
                </Button>
              </div>
            </div>
          )}
        </div>

        {/* composer */}
        <div className="flex-shrink-0 border-t border-border-subtle-01 px-4 py-3">
          {messages.length === 0 && !pending && targetIsSkillMd && (
            <div className="flex flex-wrap gap-1.5 mb-2.5">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => setInput(s)}
                  className="text-caption-01 text-text-secondary border border-border-subtle-01 rounded-full px-2.5 py-1 hover:bg-layer-hover-01 hover:text-text-primary transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          )}
          <div className="flex items-end gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  propose();
                }
              }}
              placeholder={pending ? '먼저 위 제안을 적용하거나 취소하세요' : targetIsSkillMd ? '바꿀 점을 적어주세요… (예: 출력은 항상 한국어로)' : `${targetName} 를 어떻게 바꿀까요…`}
              disabled={!!pending}
              rows={2}
              className="flex-1 resize-none rounded-lg border border-border-subtle-01 bg-background px-3 py-2 text-body-short-01 text-text-primary placeholder:text-text-helper outline-none focus:ring-1 focus:ring-focus disabled:opacity-50"
            />
            <Button kind="primary" size="sm" icon={<Icon name="send" size={14} />} onClick={propose} disabled={!input.trim() || !!pending}>
              제안
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
};

// Made with Bob
