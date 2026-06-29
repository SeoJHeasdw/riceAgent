import React from 'react';

/**
 * 마켓 카탈로그 로드 중 표시하는 카드 스켈레톤. mock 시드를 제거했으므로 카탈로그가
 * 도착하기 전 짧은 순간을 빈 화면 대신 스켈레톤으로 채워 "로딩 중"임을 명확히 한다.
 */
export const CardSkeletonGrid: React.FC<{ count?: number; minWidth?: number }> = ({
  count = 6,
  minWidth = 330,
}) => (
  <div
    className="grid gap-4"
    style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${minWidth}px, 1fr))` }}
    aria-hidden="true"
  >
    {Array.from({ length: count }).map((_, i) => (
      <div
        key={i}
        className="min-h-[224px] rounded-2xl border border-border-subtle-01 bg-layer-01 p-4 animate-pulse"
      >
        <div className="flex items-center gap-3 mb-3.5">
          <div className="w-10 h-10 rounded-xl bg-layer-02" />
          <div className="flex-1 space-y-2">
            <div className="h-3 w-2/3 rounded bg-layer-02" />
            <div className="h-2.5 w-1/3 rounded bg-layer-02" />
          </div>
        </div>
        <div className="space-y-2 mb-4">
          <div className="h-2.5 w-full rounded bg-layer-02" />
          <div className="h-2.5 w-5/6 rounded bg-layer-02" />
        </div>
        <div className="flex gap-1.5">
          <div className="h-5 w-14 rounded-full bg-layer-02" />
          <div className="h-5 w-12 rounded-full bg-layer-02" />
        </div>
      </div>
    ))}
  </div>
);

// Made with Bob
