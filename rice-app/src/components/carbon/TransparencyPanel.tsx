import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
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

// learning_events.kind → 아이콘 + 톤 (라벨은 카탈로그에서 가져옴 — 렌더링 시 t()로)
const EVENT_META: Record<string, { icon: IconName; tone: 'pos' | 'neg' | 'warn' | 'neutral' }> = {
  proposed: { icon: 'idea', tone: 'neutral' },
  approved: { icon: 'check-circle', tone: 'pos' },
  rejected: { icon: 'close', tone: 'neutral' },
  timeout: { icon: 'renew', tone: 'warn' },
  disconnected: { icon: 'renew', tone: 'warn' },
  conflict: { icon: 'warning', tone: 'warn' },
  memory_written: { icon: 'bookmark', tone: 'pos' },
  memory_quarantined: { icon: 'lock', tone: 'warn' },
  memory_blocked: { icon: 'warning', tone: 'neg' },
  memory_approved: { icon: 'check', tone: 'pos' },
  memory_deleted: { icon: 'trash', tone: 'neutral' },
  context_rendered: { icon: 'flow', tone: 'neutral' },
  suggestion_added: { icon: 'add', tone: 'neutral' },
  discovery_run: { icon: 'search', tone: 'neutral' },
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

// t()로 eventKind 라벨을 가져올 때 동적 키라 넓혀야 함 — apiError.ts와 동일한 패턴
type TranslateWide = (key: string, opts?: Record<string, unknown>) => string;

export const TransparencyPanel: React.FC<TransparencyPanelProps> = ({ open, onClose }) => {
  const { t } = useTranslation('chat');
  // 동적 eventKind 키(카탈로그에 있지만 TS 타입이 좁음)를 위한 우회 — tour/TourOverlay.tsx 와 동일 패턴
  const tt = t as unknown as TranslateWide;

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
    <Modal open={open} onClose={onClose} title={t('transparency.modalTitle')} size="xl">
      <div className="flex flex-col gap-5 max-h-[76vh] overflow-y-auto pr-1">
        {/* 1. 컨텍스트 렌더 — 모델이 본 것 / 못 본 것 / 압축한 것 */}
        <section>
          <SectionTitle icon="flow">{t('transparency.sectionContext')}</SectionTitle>
          {latestRender ? (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <StatTile
                  label={t('transparency.statContextTokens')}
                  value={`${latestRender.tokensAfter.toLocaleString()}`}
                  hint={
                    latestRender.tokensBefore !== latestRender.tokensAfter
                      ? t('transparency.statContextTokensHintCompressed', { before: latestRender.tokensBefore.toLocaleString() })
                      : t('transparency.statContextTokensHintNone')
                  }
                />
                <StatTile
                  label={t('transparency.statLlmCalls')}
                  value={latestRender.llmCalls}
                  hint={t('transparency.statLlmCallsHint', { count: latestRender.managedCalls })}
                />
                <StatTile
                  label={t('transparency.statOmitted')}
                  value={latestRender.omitted}
                  hint={t('transparency.statOmittedHint')}
                  danger={latestRender.omitted > 0}
                />
                <StatTile
                  label={t('transparency.statDropped')}
                  value={latestRender.droppedMsgs + latestRender.droppedToolCalls}
                  hint={t('transparency.statDroppedHint', { msgs: latestRender.droppedMsgs, tools: latestRender.droppedToolCalls })}
                  danger={latestRender.droppedMsgs + latestRender.droppedToolCalls > 0}
                />
              </div>
              <p className="text-caption-01 text-text-helper mt-2">
                {t('transparency.renderCaption', {
                  time: fmtTime(latestRender.ts),
                  distilled: latestRender.distilled,
                  masked: latestRender.masked,
                })}
              </p>
            </>
          ) : (
            <p className="text-body-short-01 text-text-helper">
              {loading ? t('transparency.loading') : t('transparency.noContext')}
            </p>
          )}
        </section>

        {/* 2. 격리된 메모리 — 외부 콘텐츠 유래, 승인 전까지 주입 안 됨 */}
        <section>
          <SectionTitle icon="lock" count={quarantined.length}>
            {t('transparency.sectionQuarantine')}
          </SectionTitle>
          {quarantined.length === 0 ? (
            <p className="text-body-short-01 text-text-helper">
              {t('transparency.quarantineEmpty')}
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
                      <Tag size="sm">{m.kind === 'fact' ? t('transparency.memoryKindFact') : t('transparency.memoryKindPref')}</Tag>
                      <span className="text-label-micro text-text-helper">{t('transparency.memoryOriginExternal')}</span>
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
                      {t('transparency.approveBtn')}
                    </button>
                    <button
                      type="button"
                      disabled={busyId === m.id}
                      onClick={() => void dismissMemory(m.id)}
                      className="text-caption-01 px-2 py-1 rounded text-text-secondary hover:bg-layer-hover-01 disabled:opacity-50"
                    >
                      {t('transparency.deleteBtn')}
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
              {t('transparency.sectionBlocked')}
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
          <SectionTitle icon="growth">{t('transparency.sectionTimeline')}</SectionTitle>
          {events.length === 0 ? (
            <p className="text-body-short-01 text-text-helper">
              {loading ? t('transparency.loading') : t('transparency.timelineEmpty')}
            </p>
          ) : (
            <ul className="flex flex-col">
              {events
                .filter((e) => e.kind !== 'context_rendered')
                .slice(0, 40)
                .map((e) => {
                  const meta = EVENT_META[e.kind] ?? {
                    icon: 'chat' as IconName,
                    tone: 'neutral' as const,
                  };
                  // 카탈로그에 있는 kind는 t()로, 없는 unknown kind는 원본 코드를 그대로 표시
                  const label = tt(`transparency.eventKind.${e.kind}`, {
                    defaultValue: e.kind,
                  });
                  const version = e.detail?.version as number | undefined;
                  return (
                    <li key={e.id} className="flex items-center gap-2 py-1.5 border-b border-border-subtle-01 last:border-0">
                      <Icon name={meta.icon} className={`w-3.5 h-3.5 flex-shrink-0 ${toneClass[meta.tone]}`} />
                      <span className={`text-body-short-01 ${toneClass[meta.tone]}`}>{label}</span>
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
          {t('transparency.footer', {
            active: activeCount,
            quarantine: quarantined.length,
            events: events.length,
          })}
        </p>
      </div>
    </Modal>
  );
};
