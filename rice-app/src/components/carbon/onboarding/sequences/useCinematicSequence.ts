import { useCallback, useEffect, useState } from 'react';
import type { AwakeningBeat, CinematicPhase } from './types';

const HANDOFF_DURATION = 1900;

export const useCinematicSequence = (onFinished: () => void) => {
  const [phase, setPhase] = useState<CinematicPhase>('awakening');
  const [awakeningBeat, setAwakeningBeat] = useState<AwakeningBeat>('void');
  const [capabilityIndex, setCapabilityIndex] = useState(0);

  useEffect(() => {
    const assemble = window.setTimeout(() => setAwakeningBeat('assembly'), 180);
    const awake = window.setTimeout(() => setAwakeningBeat('awake'), 2900);
    // Leave the promise on screen long enough to read after the core wakes.
    // The skip action remains available throughout for returning users.
    const revealLoop = window.setTimeout(() => setPhase('loop'), 5200);
    return () => {
      window.clearTimeout(assemble);
      window.clearTimeout(awake);
      window.clearTimeout(revealLoop);
    };
  }, []);

  useEffect(() => {
    if (phase !== 'handoff') return;
    const finish = window.setTimeout(onFinished, HANDOFF_DURATION);
    return () => window.clearTimeout(finish);
  }, [onFinished, phase]);

  const enterEcosystem = useCallback(() => {
    setCapabilityIndex(0);
    setPhase('ecosystem');
  }, []);

  const advanceCapability = useCallback(() => {
    setCapabilityIndex((current) => Math.min(current + 1, 2));
  }, []);

  const selectCapability = useCallback((index: number) => {
    setCapabilityIndex(Math.max(0, Math.min(index, 2)));
  }, []);

  const enterModel = useCallback(() => setPhase('model'), []);
  const returnToEcosystem = useCallback(() => setPhase('ecosystem'), []);
  const startHandoff = useCallback(() => setPhase('handoff'), []);

  return {
    phase,
    awakeningBeat,
    capabilityIndex,
    enterEcosystem,
    advanceCapability,
    selectCapability,
    enterModel,
    returnToEcosystem,
    startHandoff,
  };
};
