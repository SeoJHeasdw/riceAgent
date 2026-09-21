import { useCallback, useEffect, useState } from 'react';
import { demoStretch } from '../../../../lib/demo';
import type { AwakeningBeat, CinematicPhase } from './types';

export const useCinematicSequence = (onFinished: () => void, reducedMotion = false) => {
  const [phase, setPhase] = useState<CinematicPhase>('awakening');
  const [awakeningBeat, setAwakeningBeat] = useState<AwakeningBeat>('void');
  const [capabilityIndex, setCapabilityIndex] = useState(0);

  useEffect(() => {
    if (phase !== 'awakening') return;
    // 데모 촬영에서는 3D·CSS와 같은 배율로 늘어난다. 그래야 단계와 그림이 어긋나지 않는다.
    const assemble = window.setTimeout(() => setAwakeningBeat('assembly'), demoStretch(reducedMotion ? 40 : 180));
    const awake = window.setTimeout(() => setAwakeningBeat('awake'), demoStretch(reducedMotion ? 120 : 2900));
    // Leave the promise on screen long enough to read after the core wakes.
    // The skip action remains available throughout for returning users.
    const revealLoop = window.setTimeout(() => setPhase('loop'), demoStretch(reducedMotion ? 1000 : 5200));
    return () => {
      window.clearTimeout(assemble);
      window.clearTimeout(awake);
      window.clearTimeout(revealLoop);
    };
  }, [phase, reducedMotion]);

  useEffect(() => {
    if (phase !== 'handoff') return;
    const finish = window.setTimeout(onFinished, demoStretch(reducedMotion ? 450 : 1900));
    return () => window.clearTimeout(finish);
  }, [onFinished, phase, reducedMotion]);

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
