import { ModelPicker } from '../ModelPicker';
import { BrandMark, FeatureCard, LineIcon } from './primitives';
import { icons } from './iconPaths';
import type { OnboardingStep } from './types';

/**
 * 온보딩 스텝 정의 모음.
 *
 * 각 스텝은 순수한 설정 객체다. 새 튜토리얼을 추가하려면 여기에 OnboardingStep을
 * 하나 더 정의해 buildSteps() 배열에 끼워 넣기만 하면 된다(셸 수정 불필요).
 */

/* ── 1. 환영 ── */
const welcomeStep: OnboardingStep = {
  id: 'welcome',
  navLabel: '환영',
  navDescription: 'RICE 소개',
  render: () => (
    <div className="flex flex-col h-full justify-center max-w-[440px]">
      <span className="flex items-center justify-center w-16 h-16 rounded-2xl bg-interactive text-white shadow-elevation-2 mb-6">
        <BrandMark size={34} />
      </span>
      <h1 className="text-heading-04 font-semibold text-text-primary leading-tight">
        나와 함께 똑똑해지는
        <br />
        AI 워크스페이스, RICE
      </h1>
      <p className="text-body-long-01 text-text-secondary mt-4">
        RICE는 단순한 챗봇이 아니에요. 대화하며 일할수록 <span className="text-text-primary font-medium">내 작업
        방식을 학습</span>하고, 반복되는 일은 <span className="text-text-primary font-medium">스킬로 자동화</span>해
        주는 자기개선형 에이전트입니다.
      </p>
      <p className="text-caption-01 text-text-helper mt-4">
        모든 데이터와 설정은 이 기기에만 저장돼요. 1분이면 설정이 끝납니다.
      </p>
    </div>
  ),
};

/* ── 2. 동작 원리 ── */
const conceptStep: OnboardingStep = {
  id: 'concept',
  navLabel: '동작 원리',
  navDescription: '3가지 핵심',
  render: () => (
    <div className="flex flex-col h-full justify-center max-w-[480px]">
      <h2 className="text-heading-03 font-semibold text-text-primary">RICE는 이렇게 동작해요</h2>
      <p className="text-body-short-01 text-text-secondary mt-2 mb-6">
        쓰면 쓸수록 나에게 맞춰지는 세 가지 흐름입니다.
      </p>
      <div className="flex flex-col gap-3">
        <FeatureCard
          accent="blue"
          icon={<LineIcon size={20}>{icons.chat}</LineIcon>}
          title="대화로 일하기"
          desc="자연어로 작업을 지시하면 에이전트가 도구·커넥터를 활용해 끝까지 처리해요."
        />
        <FeatureCard
          accent="teal"
          icon={<LineIcon size={20}>{icons.brain}</LineIcon>}
          title="사용할수록 학습"
          desc="내 말투·선호·반복 패턴을 기억해 다음 작업부터 더 정확하게 도와줘요."
        />
        <FeatureCard
          accent="purple"
          icon={<LineIcon size={20}>{icons.spark}</LineIcon>}
          title="반복은 스킬로 자동화"
          desc="자주 하는 작업은 스킬로 저장돼, 다음엔 슬래시 명령 한 번으로 실행돼요."
        />
      </div>
    </div>
  ),
};

/* ── 3. 모델 연결 (필수) ── */
const modelStep: OnboardingStep = {
  id: 'model',
  navLabel: '모델 연결',
  navDescription: '필수 단계',
  primaryLabel: '연결 완료',
  canAdvance: (ctx) => ctx.draft.canSave,
  blockedHint: (ctx) =>
    ctx.draft.mode === 'local'
      ? '로컬 모델을 확인하고 선택하면 계속할 수 있어요'
      : 'API 모델을 추가하면 계속할 수 있어요',
  render: (ctx) => (
    <div className="flex flex-col max-w-[480px]">
      <h2 className="text-heading-03 font-semibold text-text-primary">AI 모델 연결</h2>
      <p className="text-body-short-01 text-text-secondary mt-2 mb-5">
        클라우드 API(SaaS) 또는 내 기기의 로컬 모델(Ollama) 중 하나를 선택하세요. 나중에 설정에서 언제든 바꿀 수 있어요.
      </p>
      <div className="bg-layer-01 border border-border-subtle-01 rounded-xl p-5 shadow-elevation-1">
        <ModelPicker
          currentModel={ctx.draft.modelId || ctx.initial.currentModel}
          modelMode={ctx.draft.mode}
          settings={ctx.draft.settings}
          onboarding
          onChange={ctx.setDraft}
        />
      </div>
    </div>
  ),
};

/* ── 4. 준비 완료 ── */
const readyStep: OnboardingStep = {
  id: 'ready',
  navLabel: '준비 완료',
  navDescription: '시작하기',
  primaryLabel: '시작하기',
  render: (ctx) => (
    <div className="flex flex-col h-full justify-center max-w-[460px]">
      <span className="flex items-center justify-center w-14 h-14 rounded-2xl bg-support-success/15 text-support-success mb-5">
        <LineIcon size={28}>{icons.check}</LineIcon>
      </span>
      <h2 className="text-heading-03 font-semibold text-text-primary">모두 준비됐어요</h2>
      <p className="text-body-short-01 text-text-secondary mt-2">
        <span className="font-mono text-text-primary">{ctx.draft.modelId || ctx.initial.currentModel}</span>
        {' '}모델이 연결되었습니다. 이제 바로 대화를 시작할 수 있어요.
      </p>

      <div className="mt-6 flex flex-col gap-2.5">
        <div className="flex items-center gap-3 text-body-short-01 text-text-secondary">
          <span className="flex items-center justify-center w-8 h-8 rounded-lg bg-layer-02 text-blue-50 flex-shrink-0">
            <LineIcon size={17}>{icons.slash}</LineIcon>
          </span>
          <span>
            <span className="text-text-primary font-medium">슬래시(/) 명령</span>으로 저장된 스킬을 즉시 실행해요
          </span>
        </div>
        <div className="flex items-center gap-3 text-body-short-01 text-text-secondary">
          <span className="flex items-center justify-center w-8 h-8 rounded-lg bg-layer-02 text-teal-60 flex-shrink-0">
            <LineIcon size={17}>{icons.brain}</LineIcon>
          </span>
          <span>
            대화를 이어갈수록 <span className="text-text-primary font-medium">학습 패널</span>에 내 작업 패턴이 쌓여요
          </span>
        </div>
        <div className="flex items-center gap-3 text-body-short-01 text-text-secondary">
          <span className="flex items-center justify-center w-8 h-8 rounded-lg bg-layer-02 text-purple-60 flex-shrink-0">
            <LineIcon size={17}>{icons.shield}</LineIcon>
          </span>
          <span>
            모든 데이터는 <span className="text-text-primary font-medium">이 기기에만</span> 저장돼요
          </span>
        </div>
      </div>
    </div>
  ),
};

/** 기본 온보딩 스텝 순서 */
export const buildSteps = (): OnboardingStep[] => [welcomeStep, conceptStep, modelStep, readyStep];

// Made with Bob
