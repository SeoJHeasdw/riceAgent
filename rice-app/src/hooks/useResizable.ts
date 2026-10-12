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
  /**
   * Preview a drag width without forcing the owning React tree to re-render.
   * Pair with deferStateDuringDrag for heavy screens and update a CSS variable
   * or element style from this callback.
   */
  onResizePreview?: (width: number) => void;
  /** Commit React state only once on pointer-up; live width comes from onResizePreview. */
  deferStateDuringDrag?: boolean;
}

export interface UseResizableResult {
  width: number;
  min: number;
  max: number;
  isResizing: boolean;
  /** Attach to the handle's onPointerDown */
  startResize: (e: React.PointerEvent) => void;
  /** Reset to the initial width (e.g. on double-click) */
  resetWidth: () => void;
  /** Keyboard-accessible width adjustment. */
  resizeBy: (delta: number) => void;
}

export function useResizable({
  initial,
  min,
  max,
  storageKey,
  invert = false,
  onResizePreview,
  deferStateDuringDrag = false,
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
  const widthRef = useRef(width);
  const pendingWidthRef = useRef(width);
  const previewFrameRef = useRef<number | null>(null);

  widthRef.current = width;

  const previewWidth = useCallback(
    (next: number) => {
      if (!onResizePreview) return;
      if (previewFrameRef.current != null) cancelAnimationFrame(previewFrameRef.current);
      previewFrameRef.current = requestAnimationFrame(() => {
        previewFrameRef.current = null;
        onResizePreview(next);
      });
    },
    [onResizePreview],
  );

  const commitWidth = useCallback(
    (next: number) => {
      const clamped = clamp(next);
      widthRef.current = clamped;
      pendingWidthRef.current = clamped;
      onResizePreview?.(clamped);
      setWidth(clamped);
    },
    [clamp, onResizePreview],
  );

  const startResize = useCallback(
    (e: React.PointerEvent) => {
      // Context-menu/right-click drags are not resize gestures.
      if (e.button !== 0 || !e.isPrimary) return;
      e.preventDefault();
      startRef.current = { x: e.clientX, width: widthRef.current };
      pendingWidthRef.current = widthRef.current;
      setIsResizing(true);
    },
    [],
  );

  const resetWidth = useCallback(() => commitWidth(initial), [commitWidth, initial]);
  const resizeBy = useCallback(
    (delta: number) => commitWidth(widthRef.current + delta),
    [commitWidth],
  );

  useEffect(() => {
    if (!isResizing) return;

    const handleMove = (e: PointerEvent) => {
      const delta = (e.clientX - startRef.current.x) * (invert ? -1 : 1);
      const next = clamp(startRef.current.width + delta);
      pendingWidthRef.current = next;
      if (deferStateDuringDrag) previewWidth(next);
      else {
        widthRef.current = next;
        setWidth(next);
      }
    };
    const handleUp = () => {
      if (previewFrameRef.current != null) {
        cancelAnimationFrame(previewFrameRef.current);
        previewFrameRef.current = null;
      }
      if (deferStateDuringDrag) commitWidth(pendingWidthRef.current);
      setIsResizing(false);
    };

    document.addEventListener('pointermove', handleMove);
    document.addEventListener('pointerup', handleUp);
    document.addEventListener('pointercancel', handleUp);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    return () => {
      document.removeEventListener('pointermove', handleMove);
      document.removeEventListener('pointerup', handleUp);
      document.removeEventListener('pointercancel', handleUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [isResizing, invert, clamp, commitWidth, deferStateDuringDrag, previewWidth]);

  useEffect(
    () => () => {
      if (previewFrameRef.current != null) cancelAnimationFrame(previewFrameRef.current);
    },
    [],
  );

  useEffect(() => {
    if (storageKey && !isResizing) {
      localStorage.setItem(storageKey, String(width));
    }
  }, [width, isResizing, storageKey]);

  return { width, min, max, isResizing, startResize, resetWidth, resizeBy };
}
