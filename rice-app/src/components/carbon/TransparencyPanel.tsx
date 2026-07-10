import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Icon, Modal, Tag } from '../ui';
import type { IconName } from '../ui/Icon';
import { api } from '../../lib/api';
import type { LearningEvent, RuntimeMemory } from '../../lib/types';

/**
 * TransparencyPanel — "학습 투명성"
 *
 * 그동안 만든 감사 원천(learning_events, 컨텍스트 렌더 로그, 메모리 격리)을 한
 * 화면에 모아 "이 에이전트가 무엇을 봤고 / 못 봤고 / 압축했고 / 차단했고 / 제안했고
 * / 되돌릴 수 있는가"를 사용자에게 보여준다. 학습이 마술이 아니라 감사 가능한
 * 시스템임을 드러내는 것이 이 제품의 핵심 서사다.
 *
 * 데이터는 전부 실제 런타임 API에서 온다:
 *   - GET /learning/events  — 제안·결정·메모리·컨텍스트 렌더·차단 이력
 *   - GET /memories         — 격리(quarantined) 메모리 승인/삭제
 */

export interface TransparencyPanelProps {
  open: boolean;
  onClose: () => void;
}

interface ContextRenderSummary {
  ts: string;
  llmCalls: number;
  managedCalls: number;
  tokensBefore: number;
  tokensAfter: number;
  omitted: number;
  droppedMsgs: number;
  droppedToolCalls: number;
  distilled: number;
  masked: number;
}

// learning_events.kind → 사람이 읽는 라벨 + 아이콘 + 톤
const EVENT_META: Record<string, { label: string; icon: IconName; tone: 'pos' | 'neg' | 'warn' | 'neutral' }> = {
  proposed: { label: '스킬 제안됨', icon: 'idea', tone: 'neutral' },
  approved: { label: '승인·반영됨', icon: 'check-circle', tone: 'pos' },
  rejected: { label: '거부됨', icon: 'close', tone: 'neutral' },
  timeout: { label: '보류(제안함으로 이동)', icon: 'renew', tone: 'warn' },
  disconnected: { label: '연결 끊김(제안함 보존)', icon: 'renew', tone: 'warn' },
  conflict: { label: '버전 충돌(적용 취소)', icon: 'warning', tone: 'warn' },
  memory_written: { label: '메모리 기록', icon: 'bookmark', tone: 'pos' },
  memory_quarantined: { label: '메모리 격리(승인 대기)', icon: 'lock', tone: 'warn' },
  memory_blocked: { label: '메모리 차단(위험 신호)', icon: 'warning', tone: 'neg' },
  memory_approved: { label: '격리 메모리 승인', icon: 'check', tone: 'pos' },
  memory_deleted: { label: '메모리 삭제', icon: 'trash', tone: 'neutral' },
  context_rendered: { label: '컨텍스트 렌더', icon: 'flow', tone: 'neutral' },
  suggestion_added: { label: '제안함에 추가', icon: 'add', tone: 'neutral' },
  discovery_run: { label: '패턴 발굴 실행', icon: 'search', tone: 'neutral' },
};

const toneClass: Record<'pos' | 'neg' | 'warn' | 'neutral', string> = {
  pos: 'text-text-primary',
  neg: 'text-text-error',
  warn: 'text-text-primary',
  neutral: 'text-text-secondary',
};

function fmtTime(ts: string): string {
  // ts는 "2026-07-03T05:37:34Z" 형태 — 시:분:초만 컴팩트하게.
  const t = ts.includes('T') ? ts.split('T')[1]?.replace('Z', '') : ts;
  return t?.slice(0, 8) ?? ts;
}

const StatTile: React.FC<{ label: string; value: React.ReactNode; hint?: string; danger?: boolean }> = ({
  label,
  value,
  hint,
  danger,
}) => (
  <div className="flex flex-col gap-0.5 bg-layer-01 border border-border-subtle-01 rounded-md px-3 py-2">
    <span className="text-label-micro text-text-helper uppercase tracking-wide">{label}</span>
    <span className={`text-heading-03 tabular-nums ${danger ? 'text-text-error' : 'text-text-primary'}`}>{value}</span>
    {hint && <span className="text-caption-01 text-text-helper">{hint}</span>}
  </div>
);

const SectionTitle: React.FC<{ icon: IconName; children: React.ReactNode; count?: number }> = ({
  icon,
  children,
  count,
}) => (
  <div className="flex items-center gap-2 mb-2 mt-1">
    <Icon name={icon} className="w-4 h-4 text-text-secondary" />
    <h3 className="text-heading-02 text-text-primary">{children}</h3>
    {count !== undefined && count > 0 && (
      <span className="text-caption-01 text-text-helper tabular-nums">({count})</span>
    )}
  </div>
);

export const TransparencyPanel: React.FC<TransparencyPanelProps> = ({ open, onClose }) => {
  const [events, setEvents] = useState<LearningEvent[]>([]);
  const [memories, setMemories] = useState<RuntimeMemory[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [ev, mem] = await Promise.all([api.getLearningEvents(200), api.getMemories()]);
      if (ev.success) setEvents((ev.data as LearningEvent[]) ?? []);
      if (mem.success) {
        const raw = mem.data as { memories?: RuntimeMemory[] } | RuntimeMemory[];
        setMemories(Array.isArray(raw) ? raw : (raw?.memories ?? []));
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);

  const quarantined = useMemo(() => memories.filter((m) => m.status === 'quarantined'), [memories]);
  const activeCount = memories.length - quarantined.length;

  const latestRender: ContextRenderSummary | null = useMemo(() => {
    const ev = events.find((e) => e.kind === 'context_rendered');
    if (!ev || !ev.detail) return null;
    const calls = (ev.detail.calls as Array<Record<string, unknown>>) ?? [];
    const last = calls[calls.length - 1] ?? {};
    const sum = (key: string) =>
      calls.reduce((acc, c) => acc + ((c[key] as unknown[] | undefined)?.length ?? 0), 0);
    return {
      ts: ev.ts,
      llmCalls: (ev.detail.llm_calls as number) ?? calls.length,
      managedCalls: (ev.detail.managed_calls as number) ?? 0,
      tokensBefore: (last.tokens_before as number) ?? 0,
      tokensAfter: (last.tokens_after as number) ?? 0,
      omitted: sum('omitted_message_ids'),
      droppedMsgs: sum('dropped_message_ids'),
      droppedToolCalls: sum('dropped_tool_call_ids'),
      distilled: sum('distilled_message_ids'),
      masked: sum('masked_message_ids'),
    };
  }, [events]);

  const blocked = useMemo(() => events.filter((e) => e.kind === 'memory_blocked').slice(0, 8), [events]);

  const approveMemory = async (id: string) => {
    setBusyId(id);
    await api.approveMemory(id);
    await refresh();
    setBusyId(null);
  };
  const dismissMemory = async (id: string) => {
    setBusyId(id);
    await api.deleteMemory(id);
    await refresh();
    setBusyId(null);
  };

  return (
    <Modal open={open} onClose={onClose} title="학습 투명성" size="xl">
      <div className="flex flex-col gap-5 max-h-[76vh] overflow-y-auto pr-1">
        {/* 1. 컨텍스트 렌더 — 모델이 본 것 / 못 본 것 / 압축한 것 */}
        <section>
          <SectionTitle icon="flow">모델이 본 것 · 못 본 것</SectionTitle>
          {latestRender ? (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <StatTile
                  label="컨텍스트 토큰"
                  value={`${latestRender.tokensAfter.toLocaleString()}`}
                  hint={
                    latestRender.tokensBefore !== latestRender.tokensAfter
                      ? `압축 전 ${latestRender.tokensBefore.toLocaleString()}`
                      : '압축 없음'
                  }
                />
                <StatTile label="LLM 호출" value={latestRender.llmCalls} hint={`관리 ${latestRender.managedCalls}회`} />
                <StatTile
                  label="요약으로 대체"
                  value={latestRender.omitted}
                  hint="오래된 메시지"
                  danger={latestRender.omitted > 0}
                />
                <StatTile
                  label="wire 정리로 삭제"
                  value={latestRender.droppedMsgs + latestRender.droppedToolCalls}
                  hint={`메시지 ${latestRender.droppedMsgs} · 도구 ${latestRender.droppedToolCalls}`}
                  danger={latestRender.droppedMsgs + latestRender.droppedToolCalls > 0}
                />
              </div>
              <p className="text-caption-01 text-text-helper mt-2">
                가장 최근 턴의 컨텍스트 렌더 기록 · {fmtTime(latestRender.ts)} · 증류 {latestRender.distilled} · 시크릿
                마스킹 {latestRender.masked}
              </p>
            </>
          ) : (
            <p className="text-body-short-01 text-text-helper">
              {loading ? '불러오는 중…' : '아직 컨텍스트 렌더 기록이 없습니다. 대화를 한 번 진행해 보세요.'}
            </p>
          )}
        </section>

        {/* 2. 격리된 메모리 — 외부 콘텐츠 유래, 승인 전까지 주입 안 됨 */}
        <section>
          <SectionTitle icon="lock" count={quarantined.length}>
            승인 대기 메모리 (격리됨)
          </SectionTitle>
          {quarantined.length === 0 ? (
            <p className="text-body-short-01 text-text-helper">
              격리된 메모리가 없습니다. 외부 콘텐츠(메일·웹·도구 결과)에서 유도된 메모리는 여기서 승인해야
              주입됩니다.
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {quarantined.map((m) => (
                <li
                  key={m.id}
                  className="flex items-start gap-2 bg-layer-01 border border-border-subtle-01 rounded-md px-3 py-2"
                >
                  <Icon name="warning" className="w-4 h-4 text-text-error mt-0.5 flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5 mb-0.5">
                      <Tag size="sm">{m.kind === 'fact' ? '사실' : '선호'}</Tag>
                      <span className="text-label-micro text-text-helper">외부 콘텐츠 유래</span>
                    </div>
                    <p className="text-body-short-01 text-text-primary break-words">{m.content}</p>
                  </div>
                  <div className="flex flex-col gap-1 flex-shrink-0">
                    <button
                      type="button"
                      disabled={busyId === m.id}
                      onClick={() => void approveMemory(m.id)}
                      className="text-caption-01 px-2 py-1 rounded bg-layer-selected-01 text-text-primary hover:bg-layer-hover-02 disabled:opacity-50"
                    >
                      승인
                    </button>
                    <button
                      type="button"
                      disabled={busyId === m.id}
                      onClick={() => void dismissMemory(m.id)}
                      className="text-caption-01 px-2 py-1 rounded text-text-secondary hover:bg-layer-hover-01 disabled:opacity-50"
                    >
                      삭제
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* 3. 차단된 메모리 — 위험 신호로 저장 자체가 거부됨 */}
        {blocked.length > 0 && (
          <section>
            <SectionTitle icon="warning" count={blocked.length}>
              차단된 메모리 (위험 신호)
            </SectionTitle>
            <ul className="flex flex-col gap-1.5">
              {blocked.map((e) => {
                const risks = (e.detail?.risks as string[]) ?? [];
                const content = (e.detail?.content as string) ?? '';
                return (
                  <li key={e.id} className="bg-layer-01 border border-border-subtle-01 rounded-md px-3 py-2">
                    <p className="text-body-short-01 text-text-secondary break-words line-through decoration-text-error/40">
                      {content}
                    </p>
                    <div className="flex flex-wrap gap-1 mt-1">
                      {risks.map((r, i) => (
                        <span key={i} className="text-label-micro text-text-error bg-text-error/10 rounded px-1.5 py-0.5">
                          {r}
                        </span>
                      ))}
                    </div>
                  </li>
                );
              })}
            </ul>
          </section>
        )}

        {/* 4. 학습 타임라인 — 되돌릴 수 있는 결정 이력 */}
        <section>
          <SectionTitle icon="growth">학습 타임라인</SectionTitle>
          {events.length === 0 ? (
            <p className="text-body-short-01 text-text-helper">{loading ? '불러오는 중…' : '아직 기록이 없습니다.'}</p>
          ) : (
            <ul className="flex flex-col">
              {events
                .filter((e) => e.kind !== 'context_rendered')
                .slice(0, 40)
                .map((e) => {
                  const meta = EVENT_META[e.kind] ?? {
                    label: e.kind,
                    icon: 'chat' as IconName,
                    tone: 'neutral' as const,
                  };
                  const version = e.detail?.version as number | undefined;
                  return (
                    <li key={e.id} className="flex items-center gap-2 py-1.5 border-b border-border-subtle-01 last:border-0">
                      <Icon name={meta.icon} className={`w-3.5 h-3.5 flex-shrink-0 ${toneClass[meta.tone]}`} />
                      <span className={`text-body-short-01 ${toneClass[meta.tone]}`}>{meta.label}</span>
                      {e.skill_id && (
                        <span className="text-caption-01 text-text-secondary truncate">· {e.skill_id}</span>
                      )}
                      {version !== undefined && version !== null && (
                        <Tag size="sm">v{version}</Tag>
                      )}
                      <span className="text-label-micro text-text-helper tabular-nums ml-auto flex-shrink-0">
                        {fmtTime(e.ts)}
                      </span>
                    </li>
                  );
                })}
            </ul>
          )}
        </section>

        <p className="text-caption-01 text-text-helper border-t border-border-subtle-01 pt-3">
          활성 메모리 {activeCount}개 · 격리 {quarantined.length}개 · 이벤트 {events.length}건. 모든 학습 변경은
          여기서 감사·되돌리기가 가능합니다.
        </p>
      </div>
    </Modal>
  );
};
