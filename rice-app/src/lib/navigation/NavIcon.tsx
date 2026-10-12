import React from 'react';

/**
 * NavIcon — 네비게이션 destination 아이콘의 공용 래퍼.
 * 24 viewBox / stroke 스타일(좌측 IconRail과 동일)을 크기만 바꿔 재사용한다.
 * 매니페스트(navDestinations)는 path만 들고, 레일·홈 허브가 이 래퍼로 그린다.
 */
export const NavIcon: React.FC<{ size?: number; className?: string; children: React.ReactNode }> = ({
  size = 20,
  className = '',
  children,
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden
  >
    {children}
  </svg>
);
