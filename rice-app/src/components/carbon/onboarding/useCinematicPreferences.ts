import { useEffect, useState } from 'react';

interface ConnectionHint {
  saveData?: boolean;
  addEventListener?: (type: 'change', listener: () => void) => void;
  removeEventListener?: (type: 'change', listener: () => void) => void;
}

interface NavigatorWithPerformanceHints extends Navigator {
  deviceMemory?: number;
  connection?: ConnectionHint;
}

const getReducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const getLowPowerMode = () => {
  if (typeof navigator === 'undefined') return false;
  const hints = navigator as NavigatorWithPerformanceHints;
  const logicalCores = hints.hardwareConcurrency || 8;
  const memory = hints.deviceMemory ?? 8;
  return logicalCores <= 4 || memory <= 4 || hints.connection?.saveData === true;
};

/** Keeps CSS motion preferences and WebGL rendering quality in the same policy. */
export const useCinematicPreferences = () => {
  const [reducedMotion, setReducedMotion] = useState(getReducedMotion);
  const [lowPower, setLowPower] = useState(getLowPowerMode);

  useEffect(() => {
    const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    const hints = navigator as NavigatorWithPerformanceHints;
    const updateMotion = () => setReducedMotion(motionQuery.matches);
    const updatePower = () => setLowPower(getLowPowerMode());

    motionQuery.addEventListener('change', updateMotion);
    hints.connection?.addEventListener?.('change', updatePower);
    return () => {
      motionQuery.removeEventListener('change', updateMotion);
      hints.connection?.removeEventListener?.('change', updatePower);
    };
  }, []);

  return { reducedMotion, lowPower: lowPower || reducedMotion };
};
