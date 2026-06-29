import React from 'react';

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

/** 아이콘 + 제목 + 설명의 피처 카드 (개념 소개 스텝에서 사용) */
export const FeatureCard: React.FC<{
  icon: React.ReactNode;
  title: string;
  desc: string;
  accent?: 'blue' | 'teal' | 'purple';
}> = ({ icon, title, desc, accent = 'blue' }) => {
  const accentMap = {
    blue: 'text-blue-50 bg-blue-60/12',
    teal: 'text-teal-60 bg-teal-60/12',
    purple: 'text-purple-60 bg-purple-60/12',
  } as const;
  return (
    <div className="flex items-start gap-3.5 p-4 rounded-xl border border-border-subtle-01 bg-layer-02 transition-colors hover:border-border-strong-01">
      <span className={`flex items-center justify-center w-10 h-10 rounded-lg flex-shrink-0 ${accentMap[accent]}`}>
        {icon}
      </span>
      <div className="min-w-0">
        <div className="text-body-short-01 font-semibold text-text-primary">{title}</div>
        <div className="text-caption-01 text-text-secondary mt-1 leading-relaxed">{desc}</div>
      </div>
    </div>
  );
};

// Made with Bob
