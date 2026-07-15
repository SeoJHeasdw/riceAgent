import React, { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { TourStep } from './tourSteps';

/**
 * TourOverlay — 인앱 코치마크 투어.
 *
 * data-tour 속성으로 타겟 DOM을 찾아 위치를 계산하고, 배경 dim + 하이라이트 링 +
 * 말풍선을 렌더한다. 단계 전환 시 스포트라이트(구멍·링)와 말풍선이 다음 타겟으로
 * **부드럽게 미끄러지고**, 본문만 살짝 크로스페이드된다 (숨김→순간이동 없음).
 * 팔레트·이징은 온보딩과 동일한 Carbon 토큰을 따른다.
 */

export interface TourOverlayProps {
  steps: TourStep[];
  currentPath: string;
  onNavigate: (path: string) => void;
  onDone: () => void;
  /** Missing global-nav targets use a centered explanation. Data-backed screen
   * tours wait invisibly until their stable target has mounted. */
  missingTargetBehavior?: 'center' | 'wait';
}

interface Rect { top: number; left: number; width: number; height: number; }

interface BubblePos {
  top: number;
  left: number;
  arrowSide: 'left' | 'right' | 'top' | 'bottom';
  /** 화살표 중심의 bubble-내 오프셋 (px) */
  arrowCenter: number;
  /** 타겟을 못 찾아 화면 중앙에 띄운 폴백(화살표 숨김) */
  centered?: boolean;
}

const BUBBLE_W = 300;
const GAP = 16;        // 타겟 엣지 ↔ 말풍선 엣지 간격
const ARROW_H = 10;    // SVG 화살표 높이
const ARROW_W = 18;    // SVG 화살표 너비
const VP_PAD = 12;     // 뷰포트 최소 여백
const BUBBLE_ESTIMATED_H = 220;
const EXIT_MS = 180;

// 부드러운 감속(오버슈트 없음) — 미끄러짐·모핑용
const EASE_GLIDE = 'cubic-bezier(0.22, 1, 0.36, 1)';
// 등장(페이드/스케일)용
const EASE_IN = 'cubic-bezier(0.16, 1, 0.3, 1)';

function clamp(v: number, lo: number, hi: number) { return Math.min(Math.max(v, lo), hi); }

/** 타겟 요소를 못 찾았을 때 화면 중앙에 띄우는 폴백 위치 */
function centeredBubblePos(vpW: number, vpH: number): BubblePos {
  return {
    top: Math.max(VP_PAD, vpH / 2 - 110),
    left: vpW / 2 - BUBBLE_W / 2,
    arrowSide: 'top',
    arrowCenter: 0,
    centered: true,
  };
}

function calcBubblePos(
  rect: Rect,
  placement: TourStep['placement'],
  vpW: number,
  vpH: number,
  bubbleHeight: number,
): BubblePos {
  const cx = rect.left + rect.width / 2;
  const cy = rect.top + rect.height / 2;

  type Candidate = Omit<BubblePos, 'arrowCenter'>;
  const effectiveHeight = Math.min(Math.max(bubbleHeight, 1), Math.max(1, vpH - VP_PAD * 2));
  const candidates: Record<TourStep['placement'], Candidate> = {
    right: { top: cy - effectiveHeight / 2, left: rect.left + rect.width + GAP, arrowSide: 'left' },
    left: { top: cy - effectiveHeight / 2, left: rect.left - BUBBLE_W - GAP, arrowSide: 'right' },
    top: { top: rect.top - GAP - effectiveHeight, left: cx - BUBBLE_W / 2, arrowSide: 'bottom' },
    bottom: { top: rect.top + rect.height + GAP, left: cx - BUBBLE_W / 2, arrowSide: 'top' },
  };
  const opposite: Record<TourStep['placement'], TourStep['placement']> = {
    right: 'left', left: 'right', top: 'bottom', bottom: 'top',
  };
  const fits = (candidate: Candidate) =>
    candidate.left >= VP_PAD
    && candidate.left + BUBBLE_W <= vpW - VP_PAD
    && candidate.top >= VP_PAD
    && candidate.top + effectiveHeight <= vpH - VP_PAD;

  const preferred = candidates[placement];
  const alternate = candidates[opposite[placement]];
  const raw = fits(preferred) ? preferred : fits(alternate) ? alternate : preferred;
  const left = clamp(raw.left, VP_PAD, Math.max(VP_PAD, vpW - BUBBLE_W - VP_PAD));
  const top = clamp(raw.top, VP_PAD, Math.max(VP_PAD, vpH - effectiveHeight - VP_PAD));
  const arrowCenter = raw.arrowSide === 'left' || raw.arrowSide === 'right'
    ? clamp(cy - top, ARROW_W, Math.max(ARROW_W, effectiveHeight - ARROW_W))
    : clamp(cx - left, ARROW_W, BUBBLE_W - ARROW_W);
  return { top, left, arrowSide: raw.arrowSide, arrowCenter };
}

/** Intersect an element with the viewport and any clipping/scroll ancestors.
 * getBoundingClientRect alone includes the invisible part of an element inside
 * overflow containers, which made edge targets produce clipped highlight rings. */
function visibleTargetRect(el: Element): Rect | null {
  const source = el.getBoundingClientRect();
  let left = Math.max(0, source.left);
  let top = Math.max(0, source.top);
  let right = Math.min(window.innerWidth, source.right);
  let bottom = Math.min(window.innerHeight, source.bottom);

  let parent = el.parentElement;
  while (parent && parent !== document.body) {
    const style = window.getComputedStyle(parent);
    const clipsX = /(auto|scroll|hidden|clip)/.test(style.overflowX);
    const clipsY = /(auto|scroll|hidden|clip)/.test(style.overflowY);
    if (clipsX || clipsY) {
      const bounds = parent.getBoundingClientRect();
      if (clipsX) {
        left = Math.max(left, bounds.left);
        right = Math.min(right, bounds.right);
      }
      if (clipsY) {
        top = Math.max(top, bounds.top);
        bottom = Math.min(bottom, bounds.bottom);
      }
    }
    parent = parent.parentElement;
  }

  if (right <= left || bottom <= top) return null;
  return { top, left, width: right - left, height: bottom - top };
}

/* ── Dim + 하이라이트 마스크 (구멍·링이 타겟 사이를 부드럽게 모핑) ─────────── */
const DimMask: React.FC<{ rect: Rect | null; maskId: string; visible: boolean }> = ({ rect, maskId, visible }) => {
  const vpW = window.innerWidth;
  const vpH = window.innerHeight;
  const PAD = 7;
  const R = 12;
  // SVG geometry(x/y/width/height/rx)는 Chromium에서 CSS transition으로 모핑된다.
  const morph = { transition: `x ${480}ms ${EASE_GLIDE}, y ${480}ms ${EASE_GLIDE}, width ${480}ms ${EASE_GLIDE}, height ${480}ms ${EASE_GLIDE}` };

  const geometry = (extra: number, edge: number) => {
    if (!rect) return null;
    const left = clamp(rect.left - PAD - extra, edge, Math.max(edge, vpW - edge));
    const top = clamp(rect.top - PAD - extra, edge, Math.max(edge, vpH - edge));
    const right = clamp(rect.left + rect.width + PAD + extra, edge, Math.max(edge, vpW - edge));
    const bottom = clamp(rect.top + rect.height + PAD + extra, edge, Math.max(edge, vpH - edge));
    return { x: left, y: top, width: Math.max(0, right - left), height: Math.max(0, bottom - top) };
  };
  const hole = geometry(0, 0);
  const innerRing = geometry(0, 1);
  // The glow has a 4px centered stroke, so keep its geometry 3px inside the
  // viewport. This preserves the complete line at the screen's outer edges.
  const outerRing = geometry(3, 3);

  return (
    <svg
      className="fixed inset-0 pointer-events-none"
      width={vpW}
      height={vpH}
      style={{ zIndex: 9000, opacity: visible ? 1 : 0, transition: `opacity ${EXIT_MS}ms ${EASE_IN}` }}
      aria-hidden
    >
      <defs>
        <mask id={maskId}>
          <rect width={vpW} height={vpH} fill="white" />
          {hole && (
            <rect
              {...hole}
              rx={R} ry={R}
              fill="black"
              style={morph}
            />
          )}
        </mask>
      </defs>

      {/* 배경 dim — 차분한 슬레이트, 살짝 옅게 */}
      <rect
        width={vpW}
        height={vpH}
        mask={`url(#${maskId})`}
        style={{ fill: 'var(--rice-tour-dim)' }}
      />

      {/* 타겟 하이라이트 */}
      {innerRing && outerRing && (
        <>
          <rect
            {...outerRing}
            rx={R + 3} ry={R + 3}
            fill="none"
            strokeOpacity={0.18}
            strokeWidth="4"
            style={{ ...morph, stroke: 'var(--rice-tour-ring)' }}
          />
          <rect
            {...innerRing}
            rx={R} ry={R}
            strokeOpacity={0.8}
            strokeWidth="1.5"
            style={{
              ...morph,
              fill: 'var(--rice-tour-highlight)',
              stroke: 'var(--rice-tour-ring)',
            }}
          />
        </>
      )}
    </svg>
  );
};

/* ── SVG 화살표 (카드 색과 동일, 테마 토큰) ─────────────────────────────── */
const BubbleArrow: React.FC<{ side: BubblePos['arrowSide']; center: number }> = ({ side, center }) => {
  // NOTE: var()는 SVG presentation attribute(fill="...")에서는 무효 → 검게 폴백된다.
  // 반드시 CSS(style)로 줘야 토큰이 해석되어 카드(layer-01)와 같은 색이 된다.
  const pathStyle: React.CSSProperties = {
    fill: 'var(--rice-tour-surface)',
    stroke: 'var(--rice-tour-arrow-border)',
    strokeWidth: 1,
    strokeLinejoin: 'round',
  };

  // 핵심: 1×1 svg + overflow로 박스 밖에 그리면(예전 방식) 삼각형이 잘려 안 보였다.
  // → svg를 삼각형 크기에 딱 맞추고 양수 좌표로 그린다(overflow 의존 X).
  // base(밑변)는 OVERLAP만큼 카드 안쪽에 묻어 이음새(테두리 겹선)를 숨긴다.
  const OVERLAP = 1;
  let box: React.CSSProperties;
  let w: number, h: number, d: string;

  if (side === 'left') {
    w = ARROW_H + OVERLAP; h = ARROW_W;
    box = { left: -ARROW_H, top: center - ARROW_W / 2 };
    d = `M 0 ${ARROW_W / 2} L ${w} 0 L ${w} ${ARROW_W} Z`;
  } else if (side === 'right') {
    w = ARROW_H + OVERLAP; h = ARROW_W;
    box = { left: BUBBLE_W - OVERLAP, top: center - ARROW_W / 2 };
    d = `M ${w} ${ARROW_W / 2} L 0 0 L 0 ${ARROW_W} Z`;
  } else if (side === 'top') {
    w = ARROW_W; h = ARROW_H + OVERLAP;
    box = { left: center - ARROW_W / 2, top: -ARROW_H };
    d = `M ${ARROW_W / 2} 0 L 0 ${h} L ${ARROW_W} ${h} Z`;
  } else { // bottom
    w = ARROW_W; h = ARROW_H + OVERLAP;
    box = { left: center - ARROW_W / 2, top: `calc(100% - ${OVERLAP}px)` };
    d = `M ${ARROW_W / 2} ${h} L 0 0 L ${ARROW_W} 0 Z`;
  }

  return (
    <svg
      className="absolute pointer-events-none overflow-visible"
      width={w}
      height={h}
      style={box}
      aria-hidden
    >
      <path d={d} style={pathStyle} />
    </svg>
  );
};

/* ── 메인 컴포넌트 ──────────────────────────────────────────────────────── */
export const TourOverlay: React.FC<TourOverlayProps> = ({
  steps,
  currentPath,
  onNavigate,
  onDone,
  missingTargetBehavior = 'center',
}) => {
  const { t } = useTranslation('tour');
  // 스텝 텍스트는 id 기반 동적 키(steps.<id>.title|body)라 넓힌 t 로 조회한다.
  const tt = t as unknown as (key: string, options?: Record<string, unknown>) => string;
  const [index, setIndex] = useState(0);
  const [targetRect, setTargetRect] = useState<Rect | null>(null);
  const [bubblePos, setBubblePos] = useState<BubblePos | null>(null);
  const [shown, setShown] = useState(false);   // 첫 등장 페이드/스케일
  const [leaving, setLeaving] = useState(false); // 종료 페이드아웃
  const [bubbleHeight, setBubbleHeight] = useState(BUBBLE_ESTIMATED_H);
  const roRef = useRef<ResizeObserver | null>(null);
  const bubbleRef = useRef<HTMLDivElement | null>(null);
  const doneRef = useRef(false);
  const maskId = `tour-cutout-${useId().replace(/:/g, '')}`;
  const step = steps[index];
  const isLast = index === steps.length - 1;

  const measureTarget = useCallback(() => {
    if (!step) return;
    const el = document.querySelector(`[data-tour="${step.target}"]`);
    // 데이터가 아직 로드되지 않은 화면별 투어는 아무 레이어도 띄우지 않고
    // 기다린다. 전역 네비게이션 투어는 기존 중앙 설명 폴백을 유지한다.
    if (!el) {
      setTargetRect(null);
      setBubblePos(missingTargetBehavior === 'center'
        ? centeredBubblePos(window.innerWidth, window.innerHeight)
        : null);
      return;
    }
    const rect = visibleTargetRect(el);
    if (!rect) {
      setTargetRect(null);
      setBubblePos(null);
      return;
    }
    setTargetRect(rect);
    setBubblePos(calcBubblePos(rect, step.placement, window.innerWidth, window.innerHeight, bubbleHeight));
  }, [bubbleHeight, missingTargetBehavior, step]);

  // 단계/경로 변화 시: 필요하면 먼저 네비게이트, 아니면 다음 프레임에 측정.
  // (숨기지 않는다 → 위치가 바뀌면 스포트라이트·말풍선이 그대로 미끄러진다)
  useLayoutEffect(() => {
    if (!step) return;
    if (step.navigateTo && currentPath !== step.navigateTo) {
      onNavigate(step.navigateTo);
      return;
    }
    document.querySelector(`[data-tour="${step.target}"]`)?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    const raf = requestAnimationFrame(measureTarget);
    const t = setTimeout(measureTarget, 160); // 라우트 페인트 후 늦은 레이아웃 보정
    return () => { cancelAnimationFrame(raf); clearTimeout(t); };
  }, [index, step, currentPath, onNavigate, measureTarget]);

  // 위치가 잡히면 한 번 등장시킨다 (이후 단계 전환에서는 계속 보인 채 미끄러짐)
  useEffect(() => {
    if (bubblePos && !shown && !leaving) {
      const r = requestAnimationFrame(() => setShown(true));
      return () => cancelAnimationFrame(r);
    }
  }, [bubblePos, shown, leaving]);

  // 타겟 크기/위치 변화 추적
  useEffect(() => {
    roRef.current?.disconnect();
    const ro = new ResizeObserver(measureTarget);
    ro.observe(document.documentElement);
    roRef.current = ro;
    return () => roRef.current?.disconnect();
  }, [measureTarget]);

  // Fixed/full-height app shells often do not resize documentElement when
  // async data mounts. Observe DOM changes so a waiting screen tour appears as
  // soon as its target exists.
  useEffect(() => {
    if (missingTargetBehavior !== 'wait') return;
    const observer = new MutationObserver(measureTarget);
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [measureTarget, missingTargetBehavior]);

  useLayoutEffect(() => {
    const bubble = bubbleRef.current;
    if (!bubble) return;
    const updateHeight = () => setBubbleHeight(bubble.getBoundingClientRect().height || BUBBLE_ESTIMATED_H);
    updateHeight();
    const observer = new ResizeObserver(updateHeight);
    observer.observe(bubble);
    return () => observer.disconnect();
  }, [bubblePos, index]);

  useEffect(() => {
    window.addEventListener('resize', measureTarget);
    return () => window.removeEventListener('resize', measureTarget);
  }, [measureTarget]);

  const triggerDone = useCallback(() => {
    if (doneRef.current) return;
    doneRef.current = true;
    setLeaving(true);
    // Do not cancel this tiny timer on unmount: once the blocking layer has
    // released pointer events, a fast route change must still persist the tour
    // that the user just dismissed.
    window.setTimeout(onDone, EXIT_MS);
  }, [onDone]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') triggerDone(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [triggerDone]);

  if (!step || (missingTargetBehavior === 'wait' && !bubblePos)) return null;

  const stepTitle = tt(`steps.${step.id}.title`);
  const stepBody = tt(`steps.${step.id}.body`);

  const goNext = () => {
    if (isLast) { triggerDone(); return; }
    setIndex((i) => i + 1); // 숨기지 않음 → 다음 타겟으로 미끄러진다
  };

  const visible = shown && !leaving;

  return (
    <>
      {/* ── dim + 하이라이트 ── */}
      <DimMask rect={targetRect} maskId={maskId} visible={!leaving} />

      {/* ── 배경 클릭 차단 (말풍선 외 클릭 시 종료) ── */}
      <div
        className="fixed inset-0"
        style={{ zIndex: 9001, pointerEvents: leaving ? 'none' : 'auto' }}
        onClick={triggerDone}
        aria-hidden
      />

      {/* ── 말풍선 ── */}
      {bubblePos && (
        <div
          ref={bubbleRef}
          role="dialog"
          aria-modal="false"
          aria-label={t('ui.stepAria', { step: index + 1, title: stepTitle })}
          className="fixed top-0 left-0"
          style={{
            zIndex: 9002,
            width: BUBBLE_W,
            pointerEvents: 'auto',
            opacity: visible ? 1 : 0,
            transform: `translate3d(${bubblePos.left}px, ${bubblePos.top}px, 0) scale(${visible ? 1 : 0.98})`,
            transition: `opacity 220ms ${EASE_IN}, transform 480ms ${EASE_GLIDE}`,
            willChange: 'transform, opacity',
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {/* 화살표 (중앙 폴백일 땐 숨김) */}
          {!bubblePos.centered && <BubbleArrow side={bubblePos.arrowSide} center={bubblePos.arrowCenter} />}

          {/* 카드 본체 — 온보딩과 동일한 Carbon 토큰 */}
          <div className="rice-tour-card relative overflow-hidden rounded-xl">
            {/* RICE brand blue → purple */}
            <div className="rice-tour-accent h-[3px] w-full" />

            {/* 바디 */}
            <div className="relative px-5 pt-4 pb-4">
              {/* 닫기 버튼 (단계가 바뀌어도 유지) */}
              <button
                onClick={triggerDone}
                className="absolute top-3.5 right-3.5 flex items-center justify-center w-6 h-6 rounded-lg text-text-helper hover:text-text-secondary hover:bg-layer-hover-01 transition-colors"
                aria-label={t('ui.close')}
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden>
                  <path d="M18 6 6 18M6 6l12 12" />
                </svg>
              </button>

              {/* 단계별 텍스트만 크로스페이드 (key=index) */}
              <div key={index} style={{ animation: `tourBodyIn 300ms ${EASE_IN}` }}>
                <div className="flex items-center gap-2 mb-2.5 pr-7">
                  <span className="rice-tour-step inline-flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full text-label-micro font-bold tabular-nums text-white">
                    {index + 1}
                  </span>
                  <span className="text-body-short-01 font-semibold leading-tight text-text-primary">
                    {stepTitle}
                  </span>
                </div>
                <p className="whitespace-pre-line text-body-short-01 leading-[1.65] text-text-secondary">
                  {stepBody}
                </p>
              </div>
            </div>

            {/* 푸터 (유지 — 진행 도트가 매끄럽게 이동) */}
            <div className="flex items-center justify-between px-5 py-3 border-t border-border-subtle-01">
              {/* 진행 도트 */}
              <div className="flex items-center gap-[5px]">
                {steps.map((_, i) => (
                  <span
                    key={i}
                    className="block rounded-full"
                    style={{
                      width: i === index ? 18 : 5,
                      height: 5,
                      background: 'var(--rice-tour-accent)',
                      opacity: i === index ? 1 : i < index ? 0.45 : 0.18,
                      transition: `width 320ms ${EASE_GLIDE}, opacity 220ms ease`,
                    }}
                  />
                ))}
              </div>

              {/* 버튼 그룹 */}
              <div className="flex items-center gap-1.5">
                <button
                  onClick={triggerDone}
                  className="text-caption-01 font-medium px-2.5 py-1.5 rounded-lg text-text-helper hover:text-text-secondary hover:bg-layer-hover-01 transition-colors"
                >
                  {t('ui.skip')}
                </button>
                <button
                  onClick={goNext}
                  className="rice-tour-primary flex items-center gap-1.5 rounded-lg px-4 py-1.5 text-caption-01 font-semibold text-white transition-[filter,box-shadow]"
                >
                  {isLast ? t('ui.start') : t('ui.next')}
                  {!isLast && (
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                      <path d="M5 12h14M13 6l6 6-6 6" />
                    </svg>
                  )}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      <style>{`
        @keyframes tourBodyIn {
          from { opacity: 0; transform: translateY(4px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </>
  );
};

// Made with Bob
