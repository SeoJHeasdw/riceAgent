/**
 * 글로벌 커맨드 팔레트(Cmd+K) → ChatScreen 브리지.
 *
 * 팔레트는 App 레벨(어느 화면에서든 열림)에서 렌더되지만 "스킬 실행"·"새 대화"는
 * ChatScreen 로컬 상태(draft/command/thread)를 다룬다. tutorial.ts의 온보딩/투어
 * 재실행 브리지와 같은 방식으로 window 커스텀 이벤트를 얇게 얹는다.
 */

export const RUN_SKILL_EVENT = 'rice:run-skill';
export const NEW_CHAT_EVENT = 'rice:new-chat';

/** 채팅 컴포저에 "/command"를 주입한다 (ChatScreen이 리스닝). */
export const runSkillCommand = (command: string) =>
  window.dispatchEvent(new CustomEvent<{ command: string }>(RUN_SKILL_EVENT, { detail: { command } }));

/** 현재 세션을 접고 새 대화를 시작한다 (ChatScreen이 리스닝). */
export const startNewChat = () => window.dispatchEvent(new CustomEvent(NEW_CHAT_EVENT));

// Made with Bob
