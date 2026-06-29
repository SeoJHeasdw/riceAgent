/**
 * 온보딩 전용 mock 데이터.
 *
 * 실제 런타임 없이 온보딩 화면을 풍부하게 보여주기 위한 정적 데이터다.
 * 실제 API/SSE와 완전히 분리되어 있으며, 온보딩 스텝 컴포넌트만 참조한다.
 */

/** welcomeStep 채팅 시뮬레이션 메시지 */
export interface MockChatMessage {
  id: string;
  role: 'user' | 'agent';
  /** 표시 텍스트 */
  text: string;
  /** tool call 칩으로 표시할 때 (agent 메시지 내) */
  toolChip?: { name: string; label: string };
  /** AI 그라디언트 테두리(skill 저장 알림 등) */
  aiTile?: boolean;
  /** 스킬 저장 제안 카드 */
  saveProposal?: { skillName: string; ver: string; desc: string; meta: string };
  /** fade-in 딜레이 (ms) — CSS animation-delay에 사용 */
  delay: number;
}

export const WELCOME_CHAT: MockChatMessage[] = [
  {
    id: 'u1',
    role: 'user',
    text: '분기별 팀 성과 보고서 요약해줘',
    delay: 0,
  },
  {
    id: 'a1',
    role: 'agent',
    text: '보고서를 읽고 있어요',
    toolChip: { name: 'read_file', label: 'Q3_report.xlsx 분석 중...' },
    delay: 250,
  },
  {
    id: 'a2',
    role: 'agent',
    text: '3분기 성과를 요약했어요. 매출 목표 대비 112% 달성, 신규 고객 23건.',
    delay: 550,
  },
  {
    id: 'skill',
    role: 'agent',
    saveProposal: {
      skillName: '분기_보고서_요약',
      ver: 'v1',
      desc: '분기별 팀 성과 보고서를 요약하고 주요 지표를 추출해요.',
      meta: '3회 이상 반복 패턴 감지',
    },
    text: '',
    delay: 900,
  },
];

/** conceptStep 다이어그램 노드 */
export interface LoopNode {
  id: string;
  label: string;
  sublabel: string;
  /** 노드 색상 계열 */
  accent: 'blue' | 'teal' | 'purple' | 'orange';
  /** 노드가 활성화될 때 오른쪽에 표시할 설명 */
  description: string;
}

export const LOOP_NODES: LoopNode[] = [
  {
    id: 'chat',
    label: '대화',
    sublabel: '자연어로 지시',
    accent: 'blue',
    description: '자연어로 작업을 지시하면 에이전트가 도구와 커넥터를 활용해 끝까지 처리해요.',
  },
  {
    id: 'learn',
    label: '학습',
    sublabel: '패턴 추출',
    accent: 'teal',
    description: '대화 패턴, 선호 말투, 반복 흐름을 분석해 내 업무 방식을 기억해요.',
  },
  {
    id: 'skill',
    label: '스킬 생성',
    sublabel: '자동 저장',
    accent: 'purple',
    description: '반복되는 작업은 스킬로 자동 저장돼요. 다음엔 슬래시 명령 한 번으로 실행돼요.',
  },
  {
    id: 'improve',
    label: '자기 개선',
    sublabel: '스킬 업그레이드',
    accent: 'orange',
    description: '스킬을 쓸수록 더 나은 버전을 제안해요. 승인하면 즉시 반영되고 히스토리로 남아요.',
  },
];

// Made with Bob
