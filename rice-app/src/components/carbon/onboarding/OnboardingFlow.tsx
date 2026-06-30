import React, { useMemo, useState } from 'react';
import { Button } from '../../ui';
import type { ModelDraft, ModelSettings } from '../ModelPicker';
import { BrandMark, LineIcon } from './primitives';
import { icons } from './iconPaths';
import { buildSteps } from './steps';
import type { OnboardingStep, OnboardingStepContext } from './types';

/**
 * OnboardingFlow — 최초 실행 시 보여주는 전체 화면 다단계 튜토리얼.
 *
 * 셸은 "진행 상태(현재 스텝/네비게이션/진행 레일)"만 관리하고, 각 화면은
 * steps.tsx의 OnboardingStep 정의가 그린다. 새 튜토리얼은 스텝을 추가하면 끝.
 *
 * 모델 연결은 필수 스텝(canAdvance)으로 게이팅되며, 완료 시 onComplete로
 * 선택 결과를 올려보낸다(=기존 ModelOnboarding과 동일한 계약).
 */

export interface OnboardingFlowProps {
  currentModel: string;
  modelMode: 'saas' | 'local';
  settings: ModelSettings;
  onComplete: (payload: { modelId: string; settings: ModelSettings; mode: 'saas' | 'local' }) => void;
  /** 스텝 구성을 커스터마이즈하고 싶을 때(미지정 시 기본 4스텝) */
  steps?: OnboardingStep[];
}

export const OnboardingFlow: React.FC<OnboardingFlowProps> = ({
  currentModel,
  modelMode,
  settings,
  onComplete,
  steps: stepsProp,
}) => {
  const steps = useMemo(() => stepsProp ?? buildSteps(), [stepsProp]);
  const [index, setIndex] = useState(0);
  // 모델 선택 draft는 스텝 간 이동에도 유지되도록 셸이 보관한다.
  const [draft, setDraft] = useState<ModelDraft>({
    modelId: currentModel,
    settings,
    mode: modelMode,
    canSave: false,
  });

  const step = steps[index];
  const isLast = index === steps.length - 1;
  const initial = { currentModel, modelMode, settings };

  const complete = () => onComplete({ modelId: draft.modelId, settings: draft.settings, mode: draft.mode });

  const goTo = (stepId: string) => {
    const next = steps.findIndex((s) => s.id === stepId);
    if (next >= 0) setIndex(next);
  };

  const goNext = () => {
    if (isLast) complete();
    else setIndex((i) => Math.min(i + 1, steps.length - 1));
  };

  const ctx: OnboardingStepContext = { draft, setDraft, initial, goNext, goTo };

  const canAdvance = step.canAdvance ? step.canAdvance(ctx) : true;
  const blockedHint = !canAdvance ? step.blockedHint?.(ctx) : undefined;
  const primaryLabel = step.primaryLabel ?? (isLast ? '시작하기' : '다음');
  // 스텝 점프 규칙: 뒤로(방문한 스텝)는 항상 가능. 앞으로는 소개~모델 단계까지만
  // 자유롭게 가능하고, 모델 연결 이후 단계(준비 완료)는 모델이 연결됐을 때만 갈 수 있다.
  // (필수 단계인 모델 연결을 건너뛰고 "시작하기"에 도달하는 구멍을 막는다.)
  const modelIdx = steps.findIndex((s) => s.id === 'model');
  const modelConnected = draft.canSave;
  const canJump = (i: number) =>
    i !== index && (i < index || (modelIdx >= 0 && i <= modelIdx) || modelConnected);

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-background p-4 sm:p-6"
      role="dialog"
      aria-modal="true"
      aria-label="RICE 시작하기"
    >
      <div
        className="flex w-full max-w-[920px] h-full max-h-[640px] rounded-2xl border border-border-subtle-01 bg-layer-01 shadow-overlay overflow-hidden"
        style={{ animation: 'onbIn 240ms cubic-bezier(0.16, 1, 0.3, 1)' }}
      >
        {/* ── 좌측 진행 레일 ── */}
        <aside className="hidden md:flex flex-col w-[280px] flex-shrink-0 bg-layer-02 border-r border-border-subtle-01 pt-6 px-6 pb-2">
          <div className="flex items-center gap-2.5 mb-9">
            <span className="flex items-center justify-center w-9 h-9 rounded-xl bg-interactive text-white shadow-elevation-1">
              <BrandMark size={20} />
            </span>
            <span className="text-heading-02 font-semibold text-text-primary">RICE</span>
          </div>

          <Stepper steps={steps} index={index} canJump={canJump} onJump={setIndex} />

          <div className="mt-auto flex items-center gap-2 text-caption-01 text-text-helper py-4 border-t border-border-subtle-01">
            <LineIcon size={14}>{icons.shield}</LineIcon>
            <span>설정은 이 기기에 저장됩니다</span>
          </div>
        </aside>

        {/* ── 우측 본문 ── */}
        <div className="flex flex-col flex-1 min-w-0">
          {/* 본문 (스텝 전환 시 페이드/슬라이드) */}
          <div key={step.id} className="flex-1 overflow-y-auto px-6 py-6" style={{ animation: 'onbStep 220ms ease' }}>
            {step.render(ctx)}
          </div>

          {/* 푸터 네비게이션 */}
          <div className="flex items-center gap-2 px-6 py-2 border-t border-border-subtle-01">
            {index > 0 ? (
              <Button
                kind="ghost"
                size="sm"
                onClick={() => setIndex((i) => Math.max(i - 1, 0))}
                className="!px-2"
              >
                뒤로
              </Button>
            ) : (
              <span className="md:hidden flex items-center gap-2 text-heading-02 font-semibold text-text-primary">
                <BrandMark size={18} className="text-interactive" />
                RICE
              </span>
            )}

            <div className="ml-auto flex items-center gap-2">
              {blockedHint && (
                <span className="text-caption-01 text-text-helper">{blockedHint}</span>
              )}
              <Button
                kind="primary"
                size="sm"
                onClick={goNext}
                disabled={!canAdvance}
                title={blockedHint}
              >
                {primaryLabel}
                {!isLast && <LineIcon size={14} className="ml-0.5">{icons.arrow}</LineIcon>}
              </Button>
            </div>
          </div>
        </div>
      </div>

      <style>{`
        @keyframes onbIn { from { opacity: 0; transform: translateY(10px) scale(0.99); } to { opacity: 1; transform: translateY(0) scale(1); } }
        @keyframes onbStep { from { opacity: 0; transform: translateX(8px); } to { opacity: 1; transform: translateX(0); } }
        @keyframes onbSketch { to { stroke-dashoffset: 0; } }
      `}</style>
    </div>
  );
};

/* ── 좌측 세로 스텝 인디케이터 ── */
const Stepper: React.FC<{
  steps: OnboardingStep[];
  index: number;
  /** i 스텝으로 점프 가능한지 (앞/뒤 모두 포함) */
  canJump: (i: number) => boolean;
  onJump: (i: number) => void;
}> = ({ steps, index, canJump, onJump }) => (
  <ol className="flex flex-col">
    {steps.map((s, i) => {
      const state = i < index ? 'done' : i === index ? 'active' : 'upcoming';
      const clickable = canJump(i);
      const isLast = i === steps.length - 1;
      return (
        <li key={s.id} className="flex flex-col">
          <button
            onClick={() => onJump(i)}
            disabled={!clickable}
            className={`group flex items-center gap-3 w-full text-left px-2 py-1.5 rounded-lg transition-colors ${
              clickable ? 'hover:bg-layer-hover-02 cursor-pointer' : 'cursor-default'
            }`}
          >
            <span
              className={`flex items-center justify-center w-7 h-7 rounded-full text-caption-01 font-semibold flex-shrink-0 transition-colors ${
                state === 'done'
                  ? 'bg-interactive text-white'
                  : state === 'active'
                  ? 'bg-interactive/15 text-interactive ring-2 ring-interactive'
                  : 'bg-layer-03 text-text-helper'
              }`}
            >
              {state === 'done' ? <LineIcon size={15}>{icons.check}</LineIcon> : i + 1}
            </span>
            <span className="min-w-0">
              <span
                className={`block text-body-short-01 font-medium truncate ${
                  state === 'upcoming' ? 'text-text-helper' : 'text-text-primary'
                }`}
              >
                {s.navLabel}
              </span>
              {s.navDescription && (
                <span className="block text-caption-01 text-text-helper truncate">{s.navDescription}</span>
              )}
            </span>
          </button>

          {/* 원과 원 사이 점선 — 마지막 스텝엔 없음
              수평 위치: px-2(8px) + w-7 반지름(20px) = 28px → 선 중앙 보정 -1px = 27px
              높이: button py-1.5(12px) × 2 = 24px (하단 패딩 + 다음 버튼 상단 패딩) */}
          {!isLast && (
            <div style={{ paddingLeft: '27px' }} aria-hidden>
              <div
                style={{
                  width: '2px',
                  height: '16px',
                  backgroundImage: 'repeating-linear-gradient(to bottom, var(--cds-border-strong-02) 0px, var(--cds-border-strong-02) 2px, transparent 2px, transparent 5px)',
                }}
              />
            </div>
          )}
        </li>
      );
    })}
  </ol>
);

// Made with Bob
