import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button, ConfirmDialog, Icon } from '../ui';
import type { Skill, SkillDetail } from '../../lib/learningData';
import { ownedSkillFiles } from '../../lib/skillBridge';
import { api } from '../../lib/api';

/**
 * SkillEditDrawer — 채팅 매개 스킬 편집 (Phase 4).
 *
 * 원시 md를 자유 편집하게 두면 학습한 규칙·버전·상태가 뭉개지므로, 모든 소스 변경을
 * "지시 → LLM patch 제안 → 승인" 단일 경로로 통과시킨다(채팅 중 에이전트가 거는
 * 기존 patch 흐름과 같은 의미). 지시는 런타임(POST /skills/{id}/edit-assist)의
 * LLM 1회 호출로 대상 파일의 새 전문을 만들고, 승인 시 version++ 되고 이전 버전은
 * 히스토리에 보관된다. 출력은 항상 patch 카드(commit 게이트)이고, 그 위의 대화는
 * 반복 지시를 위한 것.
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

const nextVer = (v: string): string => {
  const m = v.match(/\d+/);
  return m ? v.replace(/\d+/, String(Number(m[0]) + 1)) : `${v}.1`;
};

const isSkillMdPath = (p: string) => p.toLowerCase().endsWith('skill.md');

/** 현재 본문 → 제안 본문의 라인 단위 diff (LCS). 파일이 20k자 상한이라 O(n·m)로 충분. */
function lineDiff(before: string, after: string): EditPatch['diff'] {
  const a = before.replace(/\s*$/, '').split('\n');
  const b = after.replace(/\s*$/, '').split('\n');
  const n = a.length;
  const m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const out: EditPatch['diff'] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ t: 'ctx', text: a[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ t: 'remove', text: a[i++] });
    } else {
      out.push({ t: 'add', text: b[j++] });
    }
  }
  while (i < n) out.push({ t: 'remove', text: a[i++] });
  while (j < m) out.push({ t: 'add', text: b[j++] });
  return out;
}

/** 변경 주변 pad줄만 남기고 긴 무변경 구간은 "⋯ N줄 ⋯"로 접는다(카드 가독성). */
function collapseContext(diff: EditPatch['diff'], pad = 3): EditPatch['diff'] {
  const keep = new Array(diff.length).fill(false);
  diff.forEach((d, i) => {
    if (d.t === 'ctx') return;
    for (let k = Math.max(0, i - pad); k <= Math.min(diff.length - 1, i + pad); k++) keep[k] = true;
  });
  const out: EditPatch['diff'] = [];
  let hidden = 0;
  const flush = () => {
    if (hidden > 0) out.push({ t: 'ctx', text: `⋯ 변경 없는 ${hidden}줄 ⋯` });
    hidden = 0;
  };
  for (let i = 0; i < diff.length; i++) {
    if (diff[i].t !== 'ctx' || keep[i]) {
      flush();
      out.push(diff[i]);
    } else hidden++;
  }
  flush();
  return out;
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
  const [thinking, setThinking] = useState(false);
  const [confirmLeaveOpen, setConfirmLeaveOpen] = useState(false);
  const [selectedTargetPath, setSelectedTargetPath] = useState(targetPath ?? '');
  const scrollRef = useRef<HTMLDivElement>(null);
  // in-flight 무효화 토큰 — 대화가 리셋(스킬/파일 변경, 재오픈)되면 올라가고,
  // 그 전에 떠난 요청의 늦은 응답은 새 대화에 꽂히지 않게 버린다.
  const reqSeq = useRef(0);

  // 드로어 안에서도 저장소의 모든 파일을 오갈 수 있다. 상세 화면에서 바로 열면
  // SKILL.md를, 파일 작업공간에서 열면 현재 보던 파일을 첫 대상으로 삼는다.
  const { files } = ownedSkillFiles(skill, detail);
  const defaultTargetPath = (targetPath && files.some((file) => file.path === targetPath))
    ? targetPath
    : files.find((file) => isSkillMdPath(file.path))?.path ?? files[0]?.path ?? '';
  const targetFile = files.find((f) => f.path === selectedTargetPath) ?? files.find((f) => isSkillMdPath(f.path)) ?? files[0];
  const targetName = targetFile?.path.split('/').pop() ?? 'SKILL.md';
  const targetIsSkillMd = isSkillMdPath(targetFile?.path ?? 'SKILL.md');

  // 스킬·대상 파일이 바뀌거나 드로어를 다시 열면 대화 초기화
  useEffect(() => {
    setSelectedTargetPath(defaultTargetPath);
    reqSeq.current += 1; // 떠 있던 제안 요청 무효화
    setMessages([]);
    setInput('');
    setPending(null);
    setEditing(false);
    setThinking(false);
    setConfirmLeaveOpen(false);
  }, [skill.id, open, targetPath, defaultTargetPath]);

  const requestClose = useCallback(() => {
    if (thinking) {
      setConfirmLeaveOpen(true);
      return;
    }
    onClose();
  }, [thinking, onClose]);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') requestClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [open, requestClose]);

  // 새로고침·탭/창 닫기는 커스텀 다이얼로그를 띄울 수 없으므로 브라우저의
  // 기본 이탈 경고를 사용한다. 응답이 끝나는 즉시 경고도 해제한다.
  useEffect(() => {
    if (!open || !thinking) return;
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [open, thinking]);

  useEffect(() => {
    if (!thinking) setConfirmLeaveOpen(false);
  }, [thinking]);

  // 새 메시지/제안/생각중 이 생기면 맨 아래로 스크롤
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, pending, thinking]);

  if (!open) return null;

  // 지시 → 런타임 LLM 이 대상 파일의 새 전문을 만들고, 여기서 현재본과 라인 diff 를
  // 계산해 patch 카드로 보여준다. 저장은 승인 시 onApply(updateSkill/_file) 가 한다.
  const propose = async () => {
    const text = input.trim();
    if (!text || pending || thinking) return;
    const target = targetFile;
    const history = messages.slice(-8); // 직전 대화 맥락(반복 지시 이해용)
    const seq = reqSeq.current;
    setMessages((m) => [...m, { role: 'user', text }]);
    setInput('');
    setThinking(true);
    const res = await api.proposeSkillEdit(skill.id, {
      targetPath: target?.path ?? 'SKILL.md',
      instruction: text,
      history,
    });
    if (seq !== reqSeq.current) return; // 그 사이 대화가 리셋됨 — 늦은 응답 폐기
    setThinking(false);
    if (!res.success || !res.data) {
      const offline = res.code === 'provider_unavailable' || !api.isEnabled;
      const msg = offline
        ? 'AI 편집은 런타임이 모델에 연결돼 있어야 해요. 런타임을 켠 뒤 다시 시도해 주세요.'
        : `제안을 만들지 못했어요${res.error ? ` — ${res.error}` : ''}. 지시를 바꿔 다시 시도해 주세요.`;
      setMessages((m) => [...m, { role: 'agent', text: msg }]);
      return;
    }
    const d = res.data;
    const diff = collapseContext(lineDiff(d.current_body, d.proposed_body));
    if (!diff.some((l) => l.t !== 'ctx')) {
      setMessages((m) => [
        ...m,
        { role: 'agent', text: '요청을 반영해도 바뀌는 내용이 없었어요. 조금 더 구체적으로 지시해 주세요.' },
      ]);
      return;
    }
    const patch: EditPatch = {
      fromVer: skill.ver,
      toVer: nextVer(skill.ver),
      changelog: d.changelog,
      diff,
      proposedBody: d.proposed_body,
      targetPath: d.target_path,
      targetLabel: d.target_path.split('/').pop() ?? d.target_path,
    };
    setMessages((m) => [
      ...m,
      { role: 'agent', text: `이렇게 바꾸면 다음 실행부터 반영돼요. 검토하고 적용하세요 — ${patch.fromVer} → ${patch.toVer}.` },
    ]);
    setBody(patch.proposedBody);
    setEditing(false);
    setPending(patch);
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
      {/* 파일 맥락을 잃지 않도록 블러 없이 가볍게만 어둡힌다. */}
      <div onClick={requestClose} className="absolute inset-0 bg-black/25 animate-fade-in" />

      {/* right drawer */}
      <div className="skill-ai-scope relative ml-auto h-full w-[520px] max-w-full bg-layer-01 border-l border-border-subtle-01 shadow-elevation-3 z-[61] flex flex-col animate-slide-in-right">
        {/* header */}
        <div className="flex-shrink-0 px-5 py-4 border-b border-border-subtle-01">
          <div className="flex items-start gap-2.5">
          <div className="w-9 h-9 flex-shrink-0 flex items-center justify-center rounded-xl border border-blue-50/25 bg-blue-60/15">
            <Icon name="sparkle" size={17} className="text-blue-30" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-body-short-02 font-semibold text-text-primary">AI로 스킬 수정</div>
            <div className="text-caption-01 text-text-helper truncate">
              {skill.name} · 제안 확인 후 {skill.ver}의 다음 버전으로 저장
            </div>
          </div>
          <button
            onClick={requestClose}
            aria-label="닫기"
            className="w-7 h-7 flex items-center justify-center rounded-md text-icon-secondary hover:bg-layer-hover-01 hover:text-text-primary transition-colors flex-shrink-0"
          >
            <Icon name="close" size={16} />
          </button>
          </div>

          <label className="mt-4 block">
            <span className="mb-1.5 block text-caption-01 font-semibold text-text-secondary">수정할 파일</span>
            <div className="relative">
              <Icon name="document" size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-icon-secondary" />
              <select
                value={targetFile?.path ?? ''}
                onChange={(event) => {
                  setSelectedTargetPath(event.target.value);
                  setMessages([]);
                  setInput('');
                  setPending(null);
                  setEditing(false);
                  setThinking(false);
                }}
                disabled={!!pending || thinking}
                className="h-9 w-full appearance-none rounded-lg border border-border-subtle-01 bg-background pl-9 pr-9 font-mono text-body-short-01 text-text-primary outline-none transition-colors hover:bg-layer-hover-01 focus:border-blue-50 disabled:opacity-50"
              >
                {files.map((file) => (
                  <option key={file.path} value={file.path}>
                    {file.path}{isSkillMdPath(file.path) ? ' — 핵심 지침' : ''}
                  </option>
                ))}
              </select>
              <Icon name="chevron-down" size={13} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-icon-secondary" />
            </div>
          </label>
        </div>

        {/* conversation */}
        <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto px-5 py-4 flex flex-col gap-3">
          {messages.length === 0 && !pending && (
            <div className="rounded-lg border border-border-subtle-01 bg-background px-3.5 py-3">
              <div className="flex items-start gap-2">
                <Icon name="idea" size={15} className="text-icon-primary flex-shrink-0 mt-0.5" />
                <div className="text-caption-01 text-text-secondary leading-5">
                  {targetIsSkillMd ? (
                    <>스킬이 언제 어떻게 행동해야 하는지 자연어로 알려주세요. AI 제안을 <span className="text-text-primary font-semibold">변경 내용으로 검토한 뒤 적용</span>하면 새 버전으로 안전하게 저장돼요.</>
                  ) : (
                    <><span className="font-mono text-text-secondary">{targetFile?.path}</span>가 어떤 역할을 해야 하는지 알려주세요. 다른 파일은 그대로 두고 이 파일의 변경만 먼저 검토할 수 있어요.</>
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

          {/* AI가 새 본문을 만드는 중 */}
          {thinking && (
            <div className="self-start max-w-[90%] text-body-short-01 text-text-helper leading-5 flex items-center gap-2">
              <Icon name="sparkle" size={15} className="text-blue-30 flex-shrink-0 animate-pulse" />
              <span className="inline-flex items-center gap-1">
                변경 내용을 만드는 중이에요
                <span className="inline-flex gap-0.5">
                  <span className="w-1 h-1 rounded-full bg-icon-secondary animate-bounce" style={{ animationDelay: '0ms' }} />
                  <span className="w-1 h-1 rounded-full bg-icon-secondary animate-bounce" style={{ animationDelay: '150ms' }} />
                  <span className="w-1 h-1 rounded-full bg-icon-secondary animate-bounce" style={{ animationDelay: '300ms' }} />
                </span>
              </span>
            </div>
          )}

          {/* pending patch card */}
          {pending && (
            <div className="ai-tile overflow-hidden">
              <div className="flex items-center gap-2 px-3.5 py-2.5 border-b border-border-subtle-01">
                <Icon name="renew" size={14} className="text-blue-40 flex-shrink-0" />
                <div className="min-w-0">
                  <div className="text-body-short-01 font-semibold text-text-primary">변경 제안</div>
                  <div className="truncate font-mono text-label-micro text-text-helper">{pending.targetPath}</div>
                </div>
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
                  <span className="text-caption-01 font-semibold text-text-secondary">{editing ? `${pending.targetLabel} 직접 조정` : '바뀌는 내용'}</span>
                  <button
                    onClick={() => setEditing((e) => !e)}
                    className="ml-auto text-caption-01 text-link-primary hover:underline inline-flex items-center gap-1"
                  >
                    <Icon name={editing ? 'view' : 'edit'} size={12} />
                    {editing ? '변경 보기' : '직접 조정'}
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
                  제안 버리기
                </Button>
                <div className="flex-1" />
                <Button kind="primary" size="sm" icon={<Icon name="check" size={14} />} onClick={apply}>
                  {editing && body !== pending.proposedBody ? '조정한 내용 적용' : '변경 적용'}
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
                  void propose();
                }
              }}
              placeholder={pending ? '먼저 위 제안을 적용하거나 버려주세요' : thinking ? '변경 내용을 만드는 중이에요…' : targetIsSkillMd ? '원하는 동작을 설명해 주세요… (예: 결과는 항상 한국어로 요약해줘)' : `${targetName}에서 무엇을 바꿀까요?`}
              disabled={!!pending || thinking}
              rows={2}
              className="flex-1 resize-none rounded-lg border border-border-subtle-01 bg-background px-3 py-2 text-body-short-01 text-text-primary placeholder:text-text-helper outline-none focus:ring-1 focus:ring-focus disabled:opacity-50"
            />
            <Button kind="primary" size="sm" icon={<Icon name="send" size={14} />} onClick={() => void propose()} disabled={!input.trim() || !!pending || thinking}>
              변경 제안
            </Button>
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={confirmLeaveOpen}
        tone="danger"
        title="AI 답변을 기다리지 않고 나갈까요?"
        description="지금 나가면 AI가 준비하고 있는 변경 내용이 사라져요."
        cancelLabel="계속 기다리기"
        confirmLabel="나가기"
        confirmIcon="close"
        stackingClassName="z-[70]"
        onClose={() => setConfirmLeaveOpen(false)}
        onConfirm={() => {
          reqSeq.current += 1;
          setConfirmLeaveOpen(false);
          onClose();
        }}
      />
    </div>
  );
};

// Made with Bob
