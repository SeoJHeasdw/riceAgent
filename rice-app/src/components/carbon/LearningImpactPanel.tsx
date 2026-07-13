import { useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { useI18n } from '../../lib/i18n/I18nContext';
import { Button, Icon, Tag } from '../ui';
import type { ImpactReport, ImpactMetricSet } from '../../lib/types';

/**
 * LearningImpactPanel — the quantitative "쓸수록 똑똑해짐" proof surface
 * (RICE_LEARNING_EVAL 6.2 wireframe). Renders an eval results report (6.3
 * schema): same tasks run cold (empty profile) vs warm (accumulated skills),
 * showing fewer turns / tool calls and higher success. Depends only on the
 * ImpactReport schema, so a seed fixture now swaps for harness output later.
 */

const roundPct = (v: number) => Math.round(Math.abs(v) * 100);
const fmtRate = (r: number) => `${Math.round(r * 100)}%`;

/** 앱 언어 코드 → Intl locale 태그 */
const LOCALE_MAP: Record<string, string> = {
  ko: 'ko-KR',
  en: 'en-US',
};

// one metric compared cold → warm, as two scaled bars
const MetricRow: React.FC<{
  label: string;
  cold: number;
  warm: number;
  format: (n: number) => string;
  /** lower is better (turns, tool calls) vs higher is better (success) */
  betterIsLower: boolean;
  /** preformatted delta, e.g. "54% ↓" or "+20pt" */
  delta: string;
  barColdLabel: string;
  barWarmLabel: string;
}> = ({ label, cold, warm, format, betterIsLower, delta, barColdLabel, barWarmLabel }) => {
  const max = Math.max(cold, warm, 0.0001);
  const improved = betterIsLower ? warm <= cold : warm >= cold;
  const Bar: React.FC<{ value: number; tone: 'cold' | 'warm' }> = ({ value, tone }) => (
    <div className="flex items-center gap-2">
      <span className="text-label-micro text-text-helper w-9 flex-shrink-0">{tone === 'cold' ? barColdLabel : barWarmLabel}</span>
      <div className="flex-1 h-3 bg-layer-02 rounded-sm overflow-hidden">
        <div
          className={`h-full rounded-sm ${tone === 'warm' ? 'bg-blue-50' : 'bg-layer-03'}`}
          style={{ width: `${Math.max((value / max) * 100, 3)}%` }}
        />
      </div>
      <span className={`text-caption-01 font-mono w-12 text-right flex-shrink-0 ${tone === 'warm' ? 'text-text-primary font-semibold' : 'text-text-helper'}`}>
        {format(value)}
      </span>
    </div>
  );
  return (
    <div>
      <div className="flex items-center mb-1">
        <span className="text-caption-01 text-text-secondary">{label}</span>
        <span className={`ml-auto text-caption-01 font-semibold ${improved ? 'text-support-success' : 'text-text-helper'}`}>
          {delta}
        </span>
      </div>
      <div className="flex flex-col gap-1">
        <Bar value={cold} tone="cold" />
        <Bar value={warm} tone="warm" />
      </div>
    </div>
  );
};

const TaskMetrics: React.FC<{
  cold: ImpactMetricSet;
  warm: ImpactMetricSet;
  delta: { turns_pct: number; tool_calls_pct: number; success_pp: number };
  barColdLabel: string;
  barWarmLabel: string;
}> = ({ cold, warm, delta, barColdLabel, barWarmLabel }) => (
  <div className="flex flex-col gap-3">
    <MetricRow
      label="Turns"
      cold={cold.turns}
      warm={warm.turns}
      format={(n) => `${n}`}
      betterIsLower
      delta={`${roundPct(delta.turns_pct)}% ↓`}
      barColdLabel={barColdLabel}
      barWarmLabel={barWarmLabel}
    />
    <MetricRow
      label="Tool calls"
      cold={cold.tool_calls}
      warm={warm.tool_calls}
      format={(n) => `${n}`}
      betterIsLower
      delta={`${roundPct(delta.tool_calls_pct)}% ↓`}
      barColdLabel={barColdLabel}
      barWarmLabel={barWarmLabel}
    />
    <MetricRow
      label="Success"
      cold={cold.success_rate}
      warm={warm.success_rate}
      format={fmtRate}
      betterIsLower={false}
      delta={`+${Math.round(delta.success_pp * 100)}pt`}
      barColdLabel={barColdLabel}
      barWarmLabel={barWarmLabel}
    />
  </div>
);

// aggregate headline stat tile
const StatTile: React.FC<{ label: string; value: string; sub: string; good?: boolean }> = ({ label, value, sub, good }) => (
  <div className="flex-1 min-w-[120px] bg-layer-01 border border-border-subtle-01 rounded-lg px-3.5 py-3">
    <div className="text-caption-01 text-text-helper">{label}</div>
    <div className={`text-heading-03 font-light mt-0.5 ${good ? 'text-support-success' : 'text-text-primary'}`}>{value}</div>
    <div className="text-caption-01 text-text-helper mt-0.5">{sub}</div>
  </div>
);

export const LearningImpactPanel: React.FC<{
  report: ImpactReport;
  onRerun?: () => void;
  /** true = eval harness 실측(GET /eval/impact) 아님 — 데모 fixture 표시 */
  demo?: boolean;
}> = ({ report, onRerun, demo }) => {
  const { t } = useTranslation('chat');
  const { language } = useI18n();
  const [showTasks, setShowTasks] = useState(true);
  const a = report.aggregate;
  const locale = LOCALE_MAP[language] ?? 'en-US';
  const when = new Date(report.generated_at).toLocaleDateString(locale, { month: 'long', day: 'numeric' });

  const barColdLabel = t('impact.barCold');
  const barWarmLabel = t('impact.barWarm');

  return (
    <div className="flex-1 min-w-0 overflow-y-auto">
      {/* header */}
      <div className="px-7 pt-6 pb-5 border-b border-border-subtle-01">
        <div className="flex items-center gap-2.5 flex-wrap">
          <div className="w-11 h-11 flex-shrink-0 flex items-center justify-center rounded-lg bg-gradient-to-br from-blue-60 to-purple-60">
            <Icon name="growth" size={22} className="text-white" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-heading-03 font-semibold text-text-primary">{t('impact.title')}</h2>
              {demo ? (
                <span title={t('impact.tagDemoTitle')}>
                  <Tag type="yellow" size="sm">{t('impact.tagDemo')}</Tag>
                </span>
              ) : (
                <span title={t('impact.tagLiveTitle')}>
                  <Tag type="green" size="sm">{t('impact.tagLive')}</Tag>
                </span>
              )}
            </div>
            <p className="text-caption-01 text-text-helper mt-0.5">
              {t('impact.headerSub', { suite: report.task_suite, runs: report.runs_per_task, model: report.model, when })}
            </p>
          </div>
        </div>
        <p className="mt-3 text-body-short-01 leading-5 text-text-secondary">
          <Trans
            i18nKey="impact.description"
            ns="chat"
            components={{
              highlight1: <span className="text-text-primary font-semibold" />,
              highlight2: <span className="text-text-primary font-semibold" />,
            }}
          />
        </p>
      </div>

      <div className="px-7 pt-5 pb-10 flex flex-col gap-7">
        {/* aggregate hero */}
        <div>
          <div className="flex items-center mb-2.5">
            <span className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">{t('impact.aggregateHeading')}</span>
          </div>
          <div className="flex gap-3 flex-wrap">
            <StatTile label="Turns" value={`${roundPct(a.turns_pct)}% ↓`} sub={t('impact.statTurnsSub')} good />
            <StatTile label="Tool calls" value={`${roundPct(a.tool_calls_pct)}% ↓`} sub={t('impact.statToolCallsSub')} good />
            <StatTile label="Success" value={`+${Math.round(a.success_pp * 100)}pt`} sub={t('impact.statSuccessSub')} good />
            <StatTile label={t('impact.statSkillHitLabel')} value={fmtRate(a.skill_hit_rate)} sub={t('impact.statSkillHitSub')} good={a.skill_hit_rate >= 0.99} />
          </div>
          <p className="mt-2.5 text-caption-01 text-text-helper flex items-start gap-1.5">
            <Icon name="idea" size={13} className="mt-0.5 flex-shrink-0 opacity-80" />
            <span>
              <span className="font-mono">skill_hit</span> {t('impact.skillHitNote')}
            </span>
          </p>
        </div>

        {/* per-task breakdown */}
        <div>
          <div className="flex items-center mb-2.5">
            <span className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">{t('impact.perTaskHeading', { count: report.tasks.length })}</span>
            <button
              onClick={() => setShowTasks((s) => !s)}
              className="ml-auto inline-flex items-center gap-1 text-caption-01 text-link-primary hover:underline"
            >
              {showTasks ? t('impact.collapseToggle') : t('impact.expandToggle')}
              <Icon name={showTasks ? 'chevron-up' : 'chevron-down'} size={13} />
            </button>
          </div>
          {showTasks && (
            <div className="flex flex-col gap-3">
              {report.tasks.map((taskItem) => (
                <div key={taskItem.id} className="bg-background border border-border-subtle-01 rounded-lg px-4 py-3.5">
                  <div className="flex items-center gap-2 mb-3 flex-wrap">
                    <span className="text-caption-01 font-mono text-text-helper">{taskItem.id}</span>
                    <span className="text-body-short-01 font-semibold text-text-primary">{taskItem.title}</span>
                    <span className="ml-auto flex-shrink-0">
                      {taskItem.warm.skill_hit ? (
                        <Tag type="green" size="sm">
                          {t('impact.tagSkillHit')}
                        </Tag>
                      ) : (
                        <Tag type="gray" size="sm">
                          {t('impact.tagSkillMiss')}
                        </Tag>
                      )}
                    </span>
                  </div>
                  <TaskMetrics cold={taskItem.cold} warm={taskItem.warm} delta={taskItem.delta} barColdLabel={barColdLabel} barWarmLabel={barWarmLabel} />
                  {taskItem.warm.skills_used && taskItem.warm.skills_used.length > 0 && (
                    <div className="mt-3 flex items-center gap-1.5 flex-wrap">
                      <span className="text-caption-01 text-text-helper">{t('impact.injectedSkillsLabel')}</span>
                      {taskItem.warm.skills_used.map((s) => (
                        <span
                          key={s}
                          className="inline-flex items-center gap-1 bg-layer-01 border border-border-subtle-01 rounded px-1.5 py-0.5 text-caption-01 font-mono text-text-secondary"
                        >
                          <Icon name="skill" size={11} />
                          {s}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* footer — provenance + rerun */}
        <div className="border-t border-border-subtle-01 pt-4 flex items-center gap-3 flex-wrap">
          <span className="text-caption-01 text-text-helper flex items-start gap-1.5">
            <Icon name="check-circle" size={14} className="text-icon-secondary mt-0.5 flex-shrink-0" />
            {t('impact.footerNote', { runs: report.runs_per_task })}
          </span>
          <div className="flex-1" />
          <Button kind="secondary" size="sm" icon={<Icon name="renew" size={15} />} onClick={onRerun} disabled={!onRerun}>
            {t('impact.rerunButton')}
          </Button>
        </div>
      </div>
    </div>
  );
};

export default LearningImpactPanel;

// Made with Bob
