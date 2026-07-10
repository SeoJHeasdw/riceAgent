import type { Language } from '../../../../lib/i18n/translations';
import type { CapabilitySpec } from './types';

export interface CinematicCopy {
  skip: string;
  privacy: string;
  awakening: {
    eyebrow: string;
    assembling: string;
    awake: string;
    title: string;
    description: string;
  };
  loop: {
    eyebrow: string;
    title: string;
    description: string;
    nodes: Array<{ label: string; caption: string }>;
    dragHint: string;
    next: string;
  };
  ecosystem: {
    eyebrow: string;
    title: string;
    description: string;
    capabilities: CapabilitySpec[];
    add: string;
    continue: string;
    assembled: string;
  };
  model: {
    eyebrow: string;
    title: string;
    description: string;
    local: string;
    blocked: string;
    connect: string;
    back: string;
  };
  handoff: {
    eyebrow: string;
    title: string;
    description: string;
  };
}

const copy: Record<Language, CinematicCopy> = {
  ko: {
    skip: '설정으로 건너뛰기',
    privacy: '학습 데이터는 이 기기에 안전하게 저장됩니다',
    awakening: {
      eyebrow: 'RICE / FIRST LIGHT',
      assembling: '인지 코어 조립 중',
      awake: '코어 온라인',
      title: '당신과 함께 진화하는 지능',
      description: 'RICE는 정답만 말하지 않습니다. 함께 일하는 방식을 배우고, 다음 작업을 더 잘하는 능력으로 바꿉니다.',
    },
    loop: {
      eyebrow: 'SELF-IMPROVEMENT LOOP',
      title: '쓸수록, 더 나다운 에이전트',
      description: '대화가 끝나도 배움은 남습니다. 반복되는 방식은 스킬이 되고, 매 실행마다 더 정교해집니다.',
      nodes: [
        { label: '대화', caption: '일을 맡기면' },
        { label: '학습', caption: '패턴을 이해하고' },
        { label: '스킬', caption: '능력으로 저장해' },
        { label: '개선', caption: '다음엔 더 잘합니다' },
      ],
      dragHint: '드래그해서 학습 우주를 움직여보세요',
      next: '능력 확장하기',
    },
    ecosystem: {
      eyebrow: 'EXPAND THE ORBIT',
      title: '필요한 능력을 궤도에 올리세요',
      description: 'RICE의 코어는 그대로, 당신에게 필요한 능력만 가볍게 연결됩니다.',
      capabilities: [
        {
          id: 'skill',
          eyebrow: '01 / WORKFLOW MEMORY',
          title: 'Skill',
          description: '반복되는 작업 방식을 기억하고, 다음에는 한 번에 실행합니다.',
          metric: '반복 작업을 재사용 가능한 능력으로',
          accent: '#5b8cff',
        },
        {
          id: 'connector',
          eyebrow: '02 / REAL-WORLD ACCESS',
          title: 'Connector',
          description: '메일, 캘린더, 문서처럼 매일 쓰는 서비스와 안전하게 연결됩니다.',
          metric: '허용한 데이터만 필요한 순간에',
          accent: '#36d8c4',
        },
        {
          id: 'plugin',
          eyebrow: '03 / DOMAIN EXPERTISE',
          title: 'Plugin',
          description: '검증된 스킬과 커넥터를 묶어, 특정 업무의 전문가로 확장합니다.',
          metric: '설치 한 번으로 완성되는 전문성',
          accent: '#aa7dff',
        },
      ],
      add: '다음 모듈 결합',
      continue: 'AI 모델 연결',
      assembled: '3개의 능력 모듈이 코어와 동기화되었습니다',
    },
    model: {
      eyebrow: 'FINAL LINK',
      title: 'RICE를 움직일 AI를 선택하세요',
      description: '클라우드 API 또는 내 기기의 로컬 모델을 연결할 수 있습니다. 언제든 설정에서 바꿀 수 있어요.',
      local: '키와 학습 설정은 이 기기에만 저장됩니다',
      blocked: '사용할 수 있는 모델을 하나 연결해 주세요',
      connect: 'RICE 시작하기',
      back: '이전',
    },
    handoff: {
      eyebrow: 'SYSTEM READY',
      title: '무엇부터 함께 배워볼까요?',
      description: '첫 대화를 시작하세요. RICE는 지금부터 당신의 방식으로 성장합니다.',
    },
  },
  en: {
    skip: 'Skip to setup',
    privacy: 'Learning data stays securely on this device',
    awakening: {
      eyebrow: 'RICE / FIRST LIGHT',
      assembling: 'Assembling cognition core',
      awake: 'Core online',
      title: 'Intelligence that evolves with you',
      description: 'RICE does more than answer. It learns how you work and turns each task into an ability it can use better next time.',
    },
    loop: {
      eyebrow: 'SELF-IMPROVEMENT LOOP',
      title: 'The more you use it, the more it becomes yours',
      description: 'The work ends, but the learning remains. Repeated patterns become skills that improve with every run.',
      nodes: [
        { label: 'Talk', caption: 'Delegate a task' },
        { label: 'Learn', caption: 'Understand patterns' },
        { label: 'Build', caption: 'Save an ability' },
        { label: 'Improve', caption: 'Do it better next time' },
      ],
      dragHint: 'Drag to move through the learning field',
      next: 'Expand its abilities',
    },
    ecosystem: {
      eyebrow: 'EXPAND THE ORBIT',
      title: 'Put the abilities you need into orbit',
      description: 'The RICE core stays focused while new capabilities connect around the way you work.',
      capabilities: [
        {
          id: 'skill',
          eyebrow: '01 / WORKFLOW MEMORY',
          title: 'Skill',
          description: 'Remember repeatable workflows and run them in one move next time.',
          metric: 'Turn repetition into a reusable ability',
          accent: '#5b8cff',
        },
        {
          id: 'connector',
          eyebrow: '02 / REAL-WORLD ACCESS',
          title: 'Connector',
          description: 'Securely reach the mail, calendar, and documents you use every day.',
          metric: 'Only the data you allow, only when needed',
          accent: '#36d8c4',
        },
        {
          id: 'plugin',
          eyebrow: '03 / DOMAIN EXPERTISE',
          title: 'Plugin',
          description: 'Combine proven skills and connectors into domain expertise.',
          metric: 'Specialized capability in one install',
          accent: '#aa7dff',
        },
      ],
      add: 'Attach next module',
      continue: 'Connect an AI model',
      assembled: 'Three capability modules are synchronized with the core',
    },
    model: {
      eyebrow: 'FINAL LINK',
      title: 'Choose the AI that powers RICE',
      description: 'Connect a cloud API or a model running locally on your device. You can change it anytime in Settings.',
      local: 'Keys and learning preferences remain on this device',
      blocked: 'Connect one available model to continue',
      connect: 'Start with RICE',
      back: 'Back',
    },
    handoff: {
      eyebrow: 'SYSTEM READY',
      title: 'What should we learn together first?',
      description: 'Start your first conversation. From this moment, RICE grows around the way you work.',
    },
  },
};

export const getCinematicCopy = (language: Language) => copy[language];
