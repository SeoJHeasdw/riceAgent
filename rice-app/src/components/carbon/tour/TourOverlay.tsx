import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
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
): BubblePos {
  const cx = rect.left + rect.width / 2;
  const cy = rect.top + rect.height / 2;

  const mk = (
    top: number,
    left: number,
    arrowSide: BubblePos['arrowSide'],
    arrowCenter: number,
  ): BubblePos => ({ top, left, arrowSide, arrowCenter });

  const right = (): BubblePos => {
    const rawTop = cy - 90;
    const top = clamp(rawTop, VP_PAD, vpH - 220);
    const left = rect.left + rect.width + GAP;
    return mk(top, left, 'left', clamp(cy - top, ARROW_W, 180 - ARROW_W));
  };
  const left = (): BubblePos => {
    const rawTop = cy - 90;
    const top = clamp(rawTop, VP_PAD, vpH - 220);
    const left = rect.left - BUBBLE_W - GAP;
    return mk(top, left, 'right', clamp(cy - top, ARROW_W, 180 - ARROW_W));
  };
  const top = (): BubblePos => {
    const rawLeft = cx - BUBBLE_W / 2;
    const l = clamp(rawLeft, VP_PAD, vpW - BUBBLE_W - VP_PAD);
    const t = rect.top - GAP - 200;
    return mk(t, l, 'bottom', clamp(cx - l, ARROW_W, BUBBLE_W - ARROW_W));
  };
  const bottom = (): BubblePos => {
    const rawLeft = cx - BUBBLE_W / 2;
    const l = clamp(rawLeft, VP_PAD, vpW - BUBBLE_W - VP_PAD);
    const t = rect.top + rect.height + GAP;
    return mk(t, l, 'top', clamp(cx - l, ARROW_W, BUBBLE_W - ARROW_W));
  };

  const fns = { right, left, top, bottom };
  const pos = fns[placement]();

  // 뷰포트 밖이면 반대방향 폴백
  if (placement === 'right' && pos.left + BUBBLE_W > vpW - VP_PAD) return left();
  if (placement === 'left' && pos.left < VP_PAD) return right();
  if (placement === 'top' && pos.top < VP_PAD) return bottom();
  if (placement === 'bottom' && pos.top + 220 > vpH - VP_PAD) return top();
  return pos;
}

/* ── Dim + 하이라이트 마스크 (구멍·링이 타겟 사이를 부드럽게 모핑) ─────────── */
const RING = '#4589ff'; // 온보딩 액센트 블루 — 차분한 하이라이트
const DimMask: React.FC<{ rect: Rect | null }> = ({ rect }) => {
  const vpW = window.innerWidth;
  const vpH = window.innerHeight;
  const PAD = 7;
  const R = 12;
  // SVG geometry(x/y/width/height/rx)는 Chromium에서 CSS transition으로 모핑된다.
  const morph = { transition: `x ${480}ms ${EASE_GLIDE}, y ${480}ms ${EASE_GLIDE}, width ${480}ms ${EASE_GLIDE}, height ${480}ms ${EASE_GLIDE}` };

  return (
    <svg className="fixed inset-0 pointer-events-none" width={vpW} height={vpH} style={{ zIndex: 9000 }} aria-hidden>
      <defs>
        <mask id="tour-cutout">
          <rect width={vpW} height={vpH} fill="white" />
          {rect && (
            <rect
              x={rect.left - PAD}
              y={rect.top - PAD}
              width={rect.width + PAD * 2}
              height={rect.height + PAD * 2}
              rx={R} ry={R}
              fill="black"
              style={morph}
            />
          )}
        </mask>
      </defs>

      {/* 배경 dim — 차분한 슬레이트, 살짝 옅게 */}
      <rect width={vpW} height={vpH} fill="rgba(15, 23, 42, 0.55)" mask="url(#tour-cutout)" />

      {/* 타겟 하이라이트 */}
      {rect && (
        <>
          <rect
            x={rect.left - PAD - 3}
            y={rect.top - PAD - 3}
            width={rect.width + (PAD + 3) * 2}
            height={rect.height + (PAD + 3) * 2}
            rx={R + 3} ry={R + 3}
            fill="none"
            stroke={RING}
            strokeOpacity={0.18}
            strokeWidth="4"
            style={morph}
          />
          <rect
            x={rect.left - PAD}
            y={rect.top - PAD}
            width={rect.width + PAD * 2}
            height={rect.height + PAD * 2}
            rx={R} ry={R}
            fill="rgba(255,255,255,0.03)"
            stroke={RING}
            strokeOpacity={0.8}
            strokeWidth="1.5"
            style={morph}
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
    fill: 'var(--cds-layer-01)',
    stroke: 'var(--cds-border-subtle-01)',
    strokeWidth: 1,
    strokeLinejoin: 'round',
  };
  const tri = (d: string, anchor: React.CSSProperties) => (
    <svg className="absolute pointer-events-none overflow-visible" style={{ top: 0, left: 0, width: 1, height: 1, ...anchor }} aria-hidden>
      <path d={d} style={pathStyle} />
    </svg>
  );

  if (side === 'left') {
    return tri(`M ${-ARROW_H} ${center} L 0 ${center - ARROW_W / 2} L 0 ${center + ARROW_W / 2} Z`, {});
  }
  if (side === 'right') {
    const bx = BUBBLE_W;
    return tri(`M ${bx + ARROW_H} ${center} L ${bx} ${center - ARROW_W / 2} L ${bx} ${center + ARROW_W / 2} Z`, {});
  }
  if (side === 'top') {
    return tri(`M ${center} ${-ARROW_H} L ${center - ARROW_W / 2} 0 L ${center + ARROW_W / 2} 0 Z`, {});
  }
  // bottom
  return tri(`M ${center} ${ARROW_H} L ${center - ARROW_W / 2} 0 L ${center + ARROW_W / 2} 0 Z`, { top: 'auto', bottom: 0 });
};

/* ── 메인 컴포넌트 ──────────────────────────────────────────────────────── */
export const TourOverlay: React.FC<TourOverlayProps> = ({ steps, currentPath, onNavigate, onDone }) => {
  const [index, setIndex] = useState(0);
  const [targetRect, setTargetRect] = useState<Rect | null>(null);
  const [bubblePos, setBubblePos] = useState<BubblePos | null>(null);
  const [shown, setShown] = useState(false);   // 첫 등장 페이드/스케일
  const [leaving, setLeaving] = useState(false); // 종료 페이드아웃
  const roRef = useRef<ResizeObserver | null>(null);
  const step = steps[index];
  const isLast = index === steps.length - 1;

  const measureTarget = useCallback(() => {
    if (!step) return;
    const el = document.querySelector(`[data-tour="${step.target}"]`);
    // 타겟을 못 찾으면(예: 접힌 패널) dim에 갇히지 않도록 중앙 폴백으로 띄운다.
    if (!el) { setTargetRect(null); setBubblePos(centeredBubblePos(window.innerWidth, window.innerHeight)); return; }
    const r = el.getBoundingClientRect();
    const rect: Rect = { top: r.top, left: r.left, width: r.width, height: r.height };
    setTargetRect(rect);
    setBubblePos(calcBubblePos(rect, step.placement, window.innerWidth, window.innerHeight));
  }, [step]);

  // 단계/경로 변화 시: 필요하면 먼저 네비게이트, 아니면 다음 프레임에 측정.
  // (숨기지 않는다 → 위치가 바뀌면 스포트라이트·말풍선이 그대로 미끄러진다)
  useLayoutEffect(() => {
    if (!step) return;
    if (step.navigateTo && currentPath !== step.navigateTo) {
      onNavigate(step.navigateTo);
      return;
    }
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

  useEffect(() => {
    window.addEventListener('resize', measureTarget);
    return () => window.removeEventListener('resize', measureTarget);
  }, [measureTarget]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') triggerDone(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!step) return null;

  const triggerDone = () => {
    setLeaving(true);
    setTimeout(onDone, 200);
  };

  const goNext = () => {
    if (isLast) { triggerDone(); return; }
    setIndex((i) => i + 1); // 숨기지 않음 → 다음 타겟으로 미끄러진다
  };

  const visible = shown && !leaving;

  return (
    <>
      {/* ── dim + 하이라이트 ── */}
      <DimMask rect={targetRect} />

      {/* ── 배경 클릭 차단 (말풍선 외 클릭 시 종료) ── */}
      <div className="fixed inset-0" style={{ zIndex: 9001 }} onClick={triggerDone} aria-hidden />

      {/* ── 말풍선 ── */}
      {bubblePos && (
        <div
          role="dialog"
          aria-modal="false"
          aria-label={`투어 ${index + 1}단계: ${step.title}`}
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
          <div className="relative rounded-2xl overflow-hidden bg-layer-01 border border-border-subtle-01 shadow-overlay">
            {/* 상단 액센트 (단색, 차분) */}
            <div className="h-[3px] w-full bg-interactive" />

            {/* 바디 */}
            <div className="relative px-5 pt-4 pb-4">
              {/* 닫기 버튼 (단계가 바뀌어도 유지) */}
              <button
                onClick={triggerDone}
                className="absolute top-3.5 right-3.5 flex items-center justify-center w-6 h-6 rounded-lg text-text-helper hover:text-text-secondary hover:bg-layer-hover-01 transition-colors"
                aria-label="투어 닫기"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden>
                  <path d="M18 6 6 18M6 6l12 12" />
                </svg>
              </button>

              {/* 단계별 텍스트만 크로스페이드 (key=index) */}
              <div key={index} style={{ animation: `tourBodyIn 300ms ${EASE_IN}` }}>
                <div className="flex items-center gap-2 mb-2.5 pr-7">
                  <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-interactive text-white text-[10px] font-bold tabular-nums flex-shrink-0">
                    {index + 1}
                  </span>
                  <span className="text-[13px] font-semibold leading-tight text-text-primary">
                    {step.title}
                  </span>
                </div>
                <p className="text-[13px] leading-[1.65] text-text-secondary">
                  {step.body}
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
                      background: 'var(--cds-interactive)',
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
                  className="text-[12px] font-medium px-2.5 py-1.5 rounded-lg text-text-helper hover:text-text-secondary hover:bg-layer-hover-01 transition-colors"
                >
                  건너뛰기
                </button>
                <button
                  onClick={goNext}
                  className="flex items-center gap-1.5 text-[12.5px] font-semibold px-4 py-1.5 rounded-lg bg-interactive text-white shadow-elevation-1 hover:bg-[#0353e9] active:scale-95 transition-all"
                >
                  {isLast ? '시작하기' : '다음'}
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
