import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { TourStep } from './tourSteps';

/**
 * TourOverlay — 인앱 말풍선 투어 오버레이.
 *
 * data-tour 속성으로 DOM 요소를 찾아 getBoundingClientRect로 위치를 계산하고,
 * 배경 dimming + 타겟 하이라이트 링 + 말풍선을 렌더링한다.
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

const BUBBLE_W = 300;
const GAP = 16;        // 타겟 엣지 ↔ 말풍선 엣지 간격
const ARROW_H = 10;    // SVG 화살표 높이
const ARROW_W = 18;    // SVG 화살표 너비
const VP_PAD = 12;     // 뷰포트 최소 여백

function clamp(v: number, lo: number, hi: number) { return Math.min(Math.max(v, lo), hi); }

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

/* ── Dim + 하이라이트 마스크 ─────────────────────────────────────────────── */
const DimMask: React.FC<{ rect: Rect | null; animating: boolean }> = ({ rect, animating }) => {
  const vpW = window.innerWidth;
  const vpH = window.innerHeight;
  const PAD = 7;
  const R = 10;

  return (
    <svg
      className="fixed inset-0 pointer-events-none"
      width={vpW}
      height={vpH}
      style={{ zIndex: 9000 }}
      aria-hidden
    >
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
            />
          )}
        </mask>
        {rect && (
          <filter id="tour-glow">
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
        )}
      </defs>

      {/* 배경 dim */}
      <rect
        width={vpW} height={vpH}
        fill="rgba(2, 8, 23, 0.72)"
        mask="url(#tour-cutout)"
        style={{ transition: animating ? 'none' : 'fill 200ms' }}
      />

      {/* 타겟 하이라이트: 외곽 글로우 링 */}
      {rect && (
        <>
          {/* 글로우 레이어 */}
          <rect
            x={rect.left - PAD - 3}
            y={rect.top - PAD - 3}
            width={rect.width + (PAD + 3) * 2}
            height={rect.height + (PAD + 3) * 2}
            rx={R + 3} ry={R + 3}
            fill="none"
            stroke="rgba(99,179,237,0.25)"
            strokeWidth="6"
            style={{ transition: 'all 280ms cubic-bezier(0.34,1.56,0.64,1)' }}
          />
          {/* 메인 링 */}
          <rect
            x={rect.left - PAD}
            y={rect.top - PAD}
            width={rect.width + PAD * 2}
            height={rect.height + PAD * 2}
            rx={R} ry={R}
            fill="rgba(255,255,255,0.04)"
            stroke="rgba(99,179,237,0.85)"
            strokeWidth="1.5"
            strokeDasharray="0"
            style={{ transition: 'all 280ms cubic-bezier(0.34,1.56,0.64,1)' }}
          />
        </>
      )}
    </svg>
  );
};

/* ── SVG 화살표 (border 트릭 대신 path로 깔끔하게) ─────────────────────── */
const BubbleArrow: React.FC<{ side: BubblePos['arrowSide']; center: number }> = ({ side, center }) => {
  const fill = '#1a2236';
  const stroke = 'rgba(255,255,255,0.07)';

  // 각 방향의 tip/base 좌표
  if (side === 'left') {
    // 말풍선 왼쪽에 붙는 화살표 → 왼쪽으로 튀어나옴
    const tip = { x: -ARROW_H, y: center };
    const base1 = { x: 0, y: center - ARROW_W / 2 };
    const base2 = { x: 0, y: center + ARROW_W / 2 };
    return (
      <svg className="absolute pointer-events-none overflow-visible" style={{ top: 0, left: 0, width: 1, height: 1 }} aria-hidden>
        <path d={`M ${tip.x} ${tip.y} L ${base1.x} ${base1.y} L ${base2.x} ${base2.y} Z`} fill={fill} stroke={stroke} strokeWidth="1" strokeLinejoin="round" />
      </svg>
    );
  }
  if (side === 'right') {
    const bx = BUBBLE_W;
    const tip = { x: bx + ARROW_H, y: center };
    const base1 = { x: bx, y: center - ARROW_W / 2 };
    const base2 = { x: bx, y: center + ARROW_W / 2 };
    return (
      <svg className="absolute pointer-events-none overflow-visible" style={{ top: 0, left: 0, width: 1, height: 1 }} aria-hidden>
        <path d={`M ${tip.x} ${tip.y} L ${base1.x} ${base1.y} L ${base2.x} ${base2.y} Z`} fill={fill} stroke={stroke} strokeWidth="1" strokeLinejoin="round" />
      </svg>
    );
  }
  if (side === 'top') {
    const tip = { x: center, y: -ARROW_H };
    const base1 = { x: center - ARROW_W / 2, y: 0 };
    const base2 = { x: center + ARROW_W / 2, y: 0 };
    return (
      <svg className="absolute pointer-events-none overflow-visible" style={{ top: 0, left: 0, width: 1, height: 1 }} aria-hidden>
        <path d={`M ${tip.x} ${tip.y} L ${base1.x} ${base1.y} L ${base2.x} ${base2.y} Z`} fill={fill} stroke={stroke} strokeWidth="1" strokeLinejoin="round" />
      </svg>
    );
  }
  // bottom
  const tip = { x: center, y: ARROW_H };   // 실제 y는 bubble 하단 기준
  const base1 = { x: center - ARROW_W / 2, y: 0 };
  const base2 = { x: center + ARROW_W / 2, y: 0 };
  return (
    <svg className="absolute pointer-events-none overflow-visible" style={{ bottom: 0, left: 0, width: 1, height: 1, top: 'auto' }} aria-hidden>
      <path d={`M ${tip.x} ${tip.y} L ${base1.x} ${base1.y} L ${base2.x} ${base2.y} Z`} fill={fill} stroke={stroke} strokeWidth="1" strokeLinejoin="round" />
    </svg>
  );
};

/* ── 메인 컴포넌트 ──────────────────────────────────────────────────────── */
export const TourOverlay: React.FC<TourOverlayProps> = ({ steps, currentPath, onNavigate, onDone }) => {
  const [index, setIndex] = useState(0);
  const [targetRect, setTargetRect] = useState<Rect | null>(null);
  const [bubblePos, setBubblePos] = useState<BubblePos | null>(null);
  // 'hidden' → 'entering' → 'visible' → 'leaving'
  const [phase, setPhase] = useState<'hidden' | 'entering' | 'visible' | 'leaving'>('hidden');
  const roRef = useRef<ResizeObserver | null>(null);
  const step = steps[index];
  const isLast = index === steps.length - 1;

  const measureTarget = useCallback(() => {
    if (!step) return;
    const el = document.querySelector(`[data-tour="${step.target}"]`);
    // 타겟을 못 찾으면(예: 접힌 패널) dim만 깔린 채 갇히지 않도록
    // 말풍선을 화면 중앙에 띄워 사용자가 계속/건너뛰기 할 수 있게 한다.
    if (!el) { setTargetRect(null); setBubblePos(centeredBubblePos(window.innerWidth, window.innerHeight)); return; }
    const r = el.getBoundingClientRect();
    const rect: Rect = { top: r.top, left: r.left, width: r.width, height: r.height };
    setTargetRect(rect);
    setBubblePos(calcBubblePos(rect, step.placement, window.innerWidth, window.innerHeight));
  }, [step]);

  useLayoutEffect(() => {
    if (!step) return;
    if (step.navigateTo && currentPath !== step.navigateTo) {
      onNavigate(step.navigateTo);
      return;
    }
    setPhase('hidden');
    const tid = setTimeout(() => {
      measureTarget();
      setPhase('entering');
      requestAnimationFrame(() => requestAnimationFrame(() => setPhase('visible')));
    }, 100);
    return () => clearTimeout(tid);
  }, [index, step, currentPath, onNavigate, measureTarget]);

  useEffect(() => {
    if (!step) return;
    roRef.current?.disconnect();
    const el = document.querySelector(`[data-tour="${step.target}"]`);
    if (el) {
      const ro = new ResizeObserver(measureTarget);
      ro.observe(document.documentElement);
      roRef.current = ro;
    }
    return () => roRef.current?.disconnect();
  }, [step, measureTarget]);

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
    setPhase('leaving');
    setTimeout(onDone, 200);
  };

  const goNext = () => {
    setPhase('leaving');
    setTimeout(() => {
      if (isLast) { onDone(); return; }
      setIndex((i) => i + 1);
    }, 160);
  };

  const isVisible = phase === 'visible';
  const isEntering = phase === 'entering';

  return (
    <>
      {/* ── dim + 하이라이트 ── */}
      <DimMask rect={targetRect} animating={phase === 'hidden' || phase === 'entering'} />

      {/* ── 배경 클릭 차단 (말풍선 외) ── */}
      <div
        className="fixed inset-0"
        style={{ zIndex: 9001 }}
        onClick={triggerDone}
        aria-hidden
      />

      {/* ── 말풍선 ── */}
      {bubblePos && (
        <div
          role="dialog"
          aria-modal="false"
          aria-label={`투어 ${index + 1}단계: ${step.title}`}
          className="fixed"
          style={{
            zIndex: 9002,
            top: bubblePos.top,
            left: bubblePos.left,
            width: BUBBLE_W,
            pointerEvents: 'auto',
            opacity: isVisible ? 1 : 0,
            transform: isVisible
              ? 'translateY(0) scale(1)'
              : isEntering
              ? 'translateY(6px) scale(0.96)'
              : 'translateY(-4px) scale(0.97)',
            transition: isEntering || isVisible
              ? 'opacity 220ms cubic-bezier(0.16,1,0.3,1), transform 280ms cubic-bezier(0.34,1.56,0.64,1)'
              : 'opacity 160ms ease, transform 160ms ease',
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {/* ── 화살표 (중앙 폴백일 땐 가리킬 타겟이 없어 숨김) ── */}
          {!bubblePos.centered && <BubbleArrow side={bubblePos.arrowSide} center={bubblePos.arrowCenter} />}

          {/* ── 카드 본체 ── */}
          <div
            className="rounded-2xl overflow-hidden"
            style={{
              background: '#1a2236',
              boxShadow: '0 4px 6px -1px rgba(0,0,0,0.3), 0 20px 48px -8px rgba(0,0,0,0.55), 0 0 0 1px rgba(255,255,255,0.07)',
            }}
          >
            {/* ── 상단 액센트 바 ── */}
            <div
              className="h-[3px] w-full"
              style={{
                background: 'linear-gradient(90deg, #3b82f6 0%, #8b5cf6 60%, #06b6d4 100%)',
              }}
            />

            {/* ── 바디 ── */}
            <div className="px-5 pt-4 pb-4">
              {/* 헤더 행 */}
              <div className="flex items-start justify-between gap-3 mb-3">
                <div className="flex items-center gap-2">
                  {/* 스텝 뱃지 */}
                  <span
                    className="inline-flex items-center justify-center rounded-full text-[10px] font-bold tabular-nums flex-shrink-0"
                    style={{
                      width: 20, height: 20,
                      background: 'linear-gradient(135deg,#3b82f6,#8b5cf6)',
                      color: '#fff',
                      letterSpacing: '-0.02em',
                    }}
                  >
                    {index + 1}
                  </span>
                  <span
                    className="text-[13px] font-bold leading-tight"
                    style={{ color: '#f1f5f9' }}
                  >
                    {step.title}
                  </span>
                </div>

                {/* 닫기 버튼 */}
                <button
                  onClick={triggerDone}
                  className="flex-shrink-0 flex items-center justify-center w-6 h-6 rounded-lg transition-all"
                  style={{ color: '#64748b' }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'rgba(255,255,255,0.08)'; (e.currentTarget as HTMLButtonElement).style.color = '#94a3b8'; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; (e.currentTarget as HTMLButtonElement).style.color = '#64748b'; }}
                  aria-label="투어 닫기"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden>
                    <path d="M18 6 6 18M6 6l12 12" />
                  </svg>
                </button>
              </div>

              {/* 설명 텍스트 */}
              <p
                className="text-[13px] leading-[1.65]"
                style={{ color: '#94a3b8' }}
              >
                {step.body}
              </p>
            </div>

            {/* ── 푸터 ── */}
            <div
              className="flex items-center justify-between px-5 py-3"
              style={{ borderTop: '1px solid rgba(255,255,255,0.07)' }}
            >
              {/* 진행 도트 */}
              <div className="flex items-center gap-[5px]">
                {steps.map((_, i) => (
                  <span
                    key={i}
                    className="rounded-full"
                    style={{
                      display: 'block',
                      width: i === index ? 18 : 5,
                      height: 5,
                      transition: 'width 250ms cubic-bezier(0.34,1.56,0.64,1), background 200ms',
                      background: i === index
                        ? 'linear-gradient(90deg,#3b82f6,#8b5cf6)'
                        : i < index
                        ? 'rgba(99,179,237,0.4)'
                        : 'rgba(255,255,255,0.12)',
                    }}
                  />
                ))}
              </div>

              {/* 버튼 그룹 */}
              <div className="flex items-center gap-2">
                <button
                  onClick={triggerDone}
                  className="text-[12px] font-medium px-2.5 py-1.5 rounded-lg transition-all"
                  style={{ color: '#64748b', background: 'transparent' }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'rgba(255,255,255,0.07)'; (e.currentTarget as HTMLButtonElement).style.color = '#94a3b8'; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; (e.currentTarget as HTMLButtonElement).style.color = '#64748b'; }}
                >
                  건너뛰기
                </button>

                <button
                  onClick={goNext}
                  className="flex items-center gap-1.5 text-[12.5px] font-semibold px-4 py-1.5 rounded-lg transition-all active:scale-95"
                  style={{
                    background: 'linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%)',
                    color: '#fff',
                    boxShadow: '0 1px 3px rgba(59,130,246,0.4), 0 4px 14px rgba(139,92,246,0.35)',
                  }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.filter = 'brightness(1.1)'; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.filter = 'brightness(1)'; }}
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
    </>
  );
};

// Made with Bob
