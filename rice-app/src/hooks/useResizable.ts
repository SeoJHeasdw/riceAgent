import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * useResizable - drag-to-resize state for side panels
 * - Clamps width between min/max
 * - Persists width to localStorage (storageKey)
 * - invert=true for right-side panels (dragging left grows the panel)
 */

export interface UseResizableOptions {
  initial: number;
  min: number;
  max: number;
  storageKey?: string;
  /** Right-side panels grow when dragging left */
  invert?: boolean;
}

export interface UseResizableResult {
  width: number;
  isResizing: boolean;
  /** Attach to the handle's onPointerDown */
  startResize: (e: React.PointerEvent) => void;
  /** Reset to the initial width (e.g. on double-click) */
  resetWidth: () => void;
}

export function useResizable({
  initial,
  min,
  max,
  storageKey,
  invert = false,
}: UseResizableOptions): UseResizableResult {
  const clamp = useCallback(
    (value: number) => Math.min(max, Math.max(min, value)),
    [min, max]
  );

  const [width, setWidth] = useState<number>(() => {
    if (storageKey) {
      const stored = Number(localStorage.getItem(storageKey));
      if (stored > 0) return clamp(stored);
    }
    return initial;
  });
  const [isResizing, setIsResizing] = useState(false);
  const startRef = useRef({ x: 0, width: 0 });

  const startResize = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      startRef.current = { x: e.clientX, width };
      setIsResizing(true);
    },
    [width]
  );

  const resetWidth = useCallback(() => setWidth(clamp(initial)), [clamp, initial]);

  useEffect(() => {
    if (!isResizing) return;

    const handleMove = (e: PointerEvent) => {
      const delta = (e.clientX - startRef.current.x) * (invert ? -1 : 1);
      setWidth(clamp(startRef.current.width + delta));
    };
    const handleUp = () => setIsResizing(false);

    document.addEventListener('pointermove', handleMove);
    document.addEventListener('pointerup', handleUp);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    return () => {
      document.removeEventListener('pointermove', handleMove);
      document.removeEventListener('pointerup', handleUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [isResizing, invert, clamp]);

  useEffect(() => {
    if (storageKey && !isResizing) {
      localStorage.setItem(storageKey, String(width));
    }
  }, [width, isResizing, storageKey]);

  return { width, isResizing, startResize, resetWidth };
}

// Made with Bob
