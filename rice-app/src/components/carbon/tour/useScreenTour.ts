/**
 * useScreenTour — 화면별 단발성 투어 완료 상태를 관리하는 hook.
 *
 * - `isDone(screenId)`: 해당 화면 투어가 완료됐는지 확인
 * - `markDone(screenId)`: 완료 키를 localStorage에 저장
 * - `resetAll()`: 세 화면의 완료 키를 모두 제거 (전체 투어 다시 보기·데이터 초기화용)
 */
import { useCallback, useEffect, useState } from 'react';
import { REPLAY_TOUR_EVENT } from '../../../lib/tutorial';
import { SCREEN_TOUR_KEYS, type ScreenTourId } from './screenTours';

const readDone = (): Set<ScreenTourId> => {
  const done = new Set<ScreenTourId>();
  try {
    for (const [screenId, key] of Object.entries(SCREEN_TOUR_KEYS) as Array<[ScreenTourId, string]>) {
      if (localStorage.getItem(key)) done.add(screenId);
    }
  } catch {
    // An in-memory set still keeps dismissal working in restricted contexts.
  }
  return done;
};

export function useScreenTour() {
  // localStorage writes do not trigger a React render in the same window. Mirror
  // the flags in state so completing a tour immediately unmounts its dim mask
  // and click blocker. This also works when storage is unavailable.
  const [done, setDone] = useState<Set<ScreenTourId>>(readDone);

  const isDone = useCallback((screenId: ScreenTourId): boolean => done.has(screenId), [done]);

  const markDone = useCallback((screenId: ScreenTourId): void => {
    try {
      localStorage.setItem(SCREEN_TOUR_KEYS[screenId], '1');
    } catch {
      // The in-memory mirror below still dismisses it for this app session.
    }
    setDone((current) => {
      if (current.has(screenId)) return current;
      const next = new Set(current);
      next.add(screenId);
      return next;
    });
  }, []);

  const resetAll = useCallback((): void => {
    try {
      for (const key of Object.values(SCREEN_TOUR_KEYS)) {
        localStorage.removeItem(key);
      }
    } catch {
      // ignore
    }
    setDone(new Set());
  }, []);

  // “Replay tour” is dispatched from both Settings and the command palette.
  // Reset the same-window state mirror as well as the persisted flags.
  useEffect(() => {
    window.addEventListener(REPLAY_TOUR_EVENT, resetAll);
    return () => window.removeEventListener(REPLAY_TOUR_EVENT, resetAll);
  }, [resetAll]);

  // Keep a second app window in sync if it changes one of the completion keys.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key && Object.values(SCREEN_TOUR_KEYS).includes(event.key as typeof SCREEN_TOUR_KEYS[ScreenTourId])) {
        setDone(readDone());
      }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  return { isDone, markDone, resetAll };
}

// Made with Bob
