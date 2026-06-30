import React, { useId } from 'react';

/**
 * 온보딩 전용 프리미티브 — 셸/스텝이 공유하는 작은 시각 요소.
 * (브랜드 마크, 라인 아이콘, 피처 카드) 한 곳에 모아 재사용한다.
 */

/** RICE 방패 로고 마크 */
export const BrandMark: React.FC<{ size?: number; className?: string }> = ({ size = 28, className = '' }) => (
  <svg width={size} height={size} viewBox="0 0 32 32" fill="currentColor" className={className} aria-hidden>
    <path d="M16 2L4 8v9c0 7 5.2 11.4 12 13 6.8-1.6 12-6 12-13V8L16 2zm0 2.2l10 5v7.8c0 5.7-4.1 9.4-10 10.9C10.1 26.4 6 22.7 6 17V9.2l10-5z" />
    <path d="M14.2 19.6l-3.4-3.4 1.4-1.4 2 2 5-5 1.4 1.4z" />
  </svg>
);

/**
 * 손그림(색연필) 데코 — 핵심 단어 강조.
 * 거친 질감은 feTurbulence+변위 필터, "스윽 그어짐"은 stroke-dasharray 드로잉
 * (onbSketch 키프레임은 OnboardingFlow의 <style>에 있음). preserveAspectRatio="none"로
 * path를 글자 박스에 맞춰 늘려 글자 폭에 자동 정렬한다.
 *
 * 한 화면에 여러 개가 동시에 떠도 필터가 충돌하지 않도록 filter id는 useId로
 * 인스턴스마다 고유하게 만든다(중복 id → url(#) 첫 매치로 쏠리는 문제 방지).
 */

/** 단어를 손으로 동그라미 친 효과 */
export const PencilCircle: React.FC<{ children: React.ReactNode; color?: string; delay?: number }> = ({
  children,
  color = '#4589ff',
  delay = 320,
}) => {
  const fid = `pc-${useId().replace(/:/g, '')}`;
  return (
    <span className="relative inline-block">
      {children}
      <svg
        className="absolute pointer-events-none overflow-visible"
        style={{ left: '-11%', top: '-20%', width: '122%', height: '142%' }}
        viewBox="0 0 120 56"
        fill="none"
        preserveAspectRatio="none"
        aria-hidden
      >
        <defs>
          <filter id={fid} x="-20%" y="-20%" width="140%" height="140%">
            <feTurbulence type="fractalNoise" baseFrequency="0.018" numOctaves="2" seed="7" result="n" />
            <feDisplacementMap in="SourceGraphic" in2="n" scale="2.6" />
          </filter>
        </defs>
        <path
          d="M22 13 C 6 17 5 40 27 48 C 55 56 105 49 111 30 C 115 14 92 5 58 6 C 35 6 17 10 13 19"
          stroke={color}
          strokeWidth="2.4"
          strokeLinecap="round"
          filter={`url(#${fid})`}
          style={{
            strokeDasharray: 320,
            strokeDashoffset: 320,
            animation: `onbSketch 720ms cubic-bezier(0.65,0,0.35,1) ${delay}ms forwards`,
          }}
        />
      </svg>
    </span>
  );
};

/** 텍스트 아래 손으로 그은 물결 밑줄 효과 */
export const PencilUnderline: React.FC<{
  children: React.ReactNode;
  color?: string;
  delay?: number;
  /** false이면 애니메이션 없이 처음부터 물결이 그어진 상태로 표시 */
  animated?: boolean;
}> = ({
  children,
  color = '#8a3ffc',
  delay = 260,
  animated = true,
}) => {
  const fid = `pu-${useId().replace(/:/g, '')}`;
  return (
    <span className="relative inline-block">
      {children}
      <svg
        className="absolute left-0 pointer-events-none overflow-visible"
        style={{ bottom: '-0.34em', width: '100%', height: '0.5em' }}
        viewBox="0 0 200 16"
        fill="none"
        preserveAspectRatio="none"
        aria-hidden
      >
        <defs>
          <filter id={fid} x="-5%" y="-60%" width="110%" height="220%">
            <feTurbulence type="fractalNoise" baseFrequency="0.02" numOctaves="2" seed="3" result="n" />
            <feDisplacementMap in="SourceGraphic" in2="n" scale="1.8" />
          </filter>
        </defs>
        <path
          d="M3 9 Q 15 3 27 9 T 51 9 T 75 9 T 99 9 T 123 9 T 147 9 T 171 9 T 197 9"
          stroke={color}
          strokeWidth="2.6"
          strokeLinecap="round"
          filter={`url(#${fid})`}
          style={
            animated
              ? { strokeDasharray: 240, strokeDashoffset: 240, animation: `onbSketch 680ms ease ${delay}ms forwards` }
              : { strokeDasharray: 240, strokeDashoffset: 0 }
          }
        />
      </svg>
    </span>
  );
};

/** 텍스트 아래 살짝 대각선의 손그림 두 줄 밑줄 (강조) */
export const PencilDoubleUnderline: React.FC<{
  children: React.ReactNode;
  color?: string;
  delay?: number;
  /** false이면 애니메이션 없이 처음부터 두 줄이 그어진 상태로 표시 */
  animated?: boolean;
}> = ({
  children,
  color = '#4589ff',
  delay = 220,
  animated = true,
}) => {
  const fid = `pd-${useId().replace(/:/g, '')}`;
  return (
    <span className="relative inline-block">
      {children}
      <svg
        className="absolute left-0 pointer-events-none overflow-visible"
        style={{ bottom: '-0.46em', width: '100%', height: '0.62em' }}
        viewBox="0 0 200 24"
        fill="none"
        preserveAspectRatio="none"
        aria-hidden
      >
        <defs>
          <filter id={fid} x="-5%" y="-50%" width="110%" height="200%">
            <feTurbulence type="fractalNoise" baseFrequency="0.022" numOctaves="2" seed="5" result="n" />
            <feDisplacementMap in="SourceGraphic" in2="n" scale="1.6" />
          </filter>
        </defs>
        {/* 위 줄 — 왼쪽 낮고 오른쪽 살짝 올라가는 대각선 */}
        <path
          d="M3 12 C 60 9, 150 8, 197 5"
          stroke={color}
          strokeWidth="2.4"
          strokeLinecap="round"
          filter={`url(#${fid})`}
          style={
            animated
              ? { strokeDasharray: 230, strokeDashoffset: 230, animation: `onbSketch 560ms cubic-bezier(0.65,0,0.35,1) ${delay}ms forwards` }
              : { strokeDasharray: 230, strokeDashoffset: 0 }
          }
        />
        {/* 아래 줄 — 살짝 시차를 둬 두 번 그은 손맛 */}
        <path
          d="M5 20 C 60 18, 150 16, 195 12"
          stroke={color}
          strokeWidth="2.1"
          strokeLinecap="round"
          filter={`url(#${fid})`}
          style={
            animated
              ? { strokeDasharray: 230, strokeDashoffset: 230, animation: `onbSketch 560ms cubic-bezier(0.65,0,0.35,1) ${delay + 150}ms forwards` }
              : { strokeDasharray: 230, strokeDashoffset: 0 }
          }
        />
      </svg>
    </span>
  );
};

/** 24px 스트로크 라인 아이콘 래퍼 (앱 사이드레일과 동일한 스타일) */
export const LineIcon: React.FC<{ size?: number; className?: string; children: React.ReactNode }> = ({
  size = 22,
  className = '',
  children,
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.7"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden
  >
    {children}
  </svg>
);

// Made with Bob
