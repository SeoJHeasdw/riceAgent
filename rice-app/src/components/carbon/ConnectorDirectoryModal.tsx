import React, { useMemo, useState } from 'react';
import { Icon, Modal, Tag } from '../ui';
import { CONNECTOR_CATALOG, type CatalogConnector } from '../../lib/learningData';

/**
 * ConnectorDirectoryModal — "커넥터 디렉터리".
 *
 * 원격 커넥터 탭의 "추가"에서 열리는 카탈로그. Claude의 디렉터리처럼 검색 + 2열
 * 카드 그리드로 추가 가능한 커넥터를 보여주고, 각 카드의 + 로 목록에 추가한다.
 * 다중 추가를 위해 추가 후에도 모달은 열린 채로 두고, 이미 추가된 항목은
 * existingIds로 판별해 "추가됨" 상태로 비활성화한다.
 */

export interface ConnectorDirectoryModalProps {
  open: boolean;
  onClose: () => void;
  /** 이미 커넥터 목록에 존재하는 id 집합 (추가됨 표시용) */
  existingIds: string[];
  /** 카드의 + 클릭 시 호출 */
  onAdd: (entry: CatalogConnector) => void;
}

const badgeType = (b: string): React.ComponentProps<typeof Tag>['type'] =>
  b === '인기' ? 'blue' : b === '추천' ? 'purple' : 'teal';

export const ConnectorDirectoryModal: React.FC<ConnectorDirectoryModalProps> = ({
  open,
  onClose,
  existingIds,
  onAdd,
}) => {
  const [query, setQuery] = useState('');

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return CONNECTOR_CATALOG;
    return CONNECTOR_CATALOG.filter(
      (c) =>
        c.name.toLowerCase().includes(q) ||
        c.category.toLowerCase().includes(q) ||
        c.summary.toLowerCase().includes(q),
    );
  }, [query]);

  return (
    <Modal open={open} onClose={onClose} title="커넥터 디렉터리" size="lg">
      <div className="space-y-4">
        {/* 검색 */}
        <div className="relative">
          <Icon name="search" size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-icon-secondary" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="커넥터 검색…"
            autoFocus
            className="w-full h-10 pl-9 pr-3.5 bg-field-01 text-text-primary text-body-short-01 border border-border-strong-01 rounded-lg outline-none transition-colors focus:border-focus focus:ring-1 focus:ring-focus placeholder:text-text-placeholder"
          />
        </div>

        {/* 출처 칩 */}
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-layer-selected-01 border border-border-subtle-01 text-caption-01 font-semibold text-text-primary">
            <Icon name="bookmark" size={12} /> IBM 및 파트너
          </span>
          <span className="text-caption-01 text-text-helper">함께 사용 가능한 · {results.length}</span>
        </div>

        {/* 카드 그리드 */}
        {results.length === 0 ? (
          <div className="py-16 text-center text-body-short-01 text-text-helper">
            "{query}"에 해당하는 커넥터가 없어요.
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
            {results.map((c) => {
              const added = existingIds.includes(c.id);
              return (
                <div
                  key={c.id}
                  className="group relative flex items-start gap-3 rounded-xl border border-border-subtle-01 bg-background p-3.5 transition-colors hover:border-border-strong-01 hover:bg-layer-hover-01"
                >
                  <div className="w-9 h-9 flex-shrink-0 flex items-center justify-center rounded-lg bg-layer-03">
                    <Icon name={c.icon} size={18} className="text-icon-primary" />
                  </div>
                  <div className="flex-1 min-w-0 pr-7">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-body-short-01 font-semibold text-text-primary truncate">{c.name}</span>
                      {c.badges?.map((b) => (
                        <Tag key={b} type={badgeType(b)} size="sm">
                          {b}
                        </Tag>
                      ))}
                    </div>
                    <p className="mt-1 text-caption-01 text-text-helper leading-4 line-clamp-2">{c.summary}</p>
                  </div>

                  {/* 추가 버튼 (우상단) */}
                  <button
                    onClick={() => !added && onAdd(c)}
                    disabled={added}
                    title={added ? '이미 추가됨' : `${c.name} 추가`}
                    aria-label={added ? '이미 추가됨' : `${c.name} 추가`}
                    className={`absolute top-2.5 right-2.5 w-7 h-7 flex items-center justify-center rounded-lg transition-colors ${
                      added
                        ? 'text-support-success cursor-default'
                        : 'text-icon-secondary hover:text-text-primary hover:bg-layer-selected-01'
                    }`}
                  >
                    <Icon name={added ? 'check' : 'add'} size={16} />
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </Modal>
  );
};

// Made with Bob
