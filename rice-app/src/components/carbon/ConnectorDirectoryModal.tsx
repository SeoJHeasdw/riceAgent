import React, { useMemo, useState } from 'react';
import { Icon, Modal, Tag } from '../ui';
import { CONNECTOR_CATALOG, type CatalogConnector } from '../../lib/learningData';

/**
 * ConnectorDirectoryModal — "커넥터 둘러보기".
 *
 * 원격 커넥터 탭의 "추가"에서 열리는 카탈로그. Claude의 둘러보기처럼 검색 + 2열
 * 카드 그리드로 추가 가능한 커넥터를 보여주고, 각 카드의 + 로 목록에 추가한다.
 * 추가하면 곧바로 그 커넥터로 이동해 연결을 이어가도록 모달이 닫힌다(onAdd 콜백이
 * 닫기까지 책임진다). 이미 추가된 항목은 existingIds로 판별해 "추가됨" 상태로 비활성화한다.
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
    <Modal open={open} onClose={onClose} title="커넥터 둘러보기" size="lg">
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
          <span className="text-caption-01 text-text-helper">카탈로그 · {results.length}</span>
        </div>

        {/* 준비 중 안내 — "준비 중" 항목은 미리보기용이라 바로 연결은 안 되고, 커스텀 추가로 안내한다. */}
        {results.some((c) => c.comingSoon) && (
          <p className="text-caption-01 text-text-helper leading-relaxed -mt-1.5">
            "준비 중" 항목은 아직 바로 연결할 수 없어요. MCP 서버 URL을 알고 있다면{' '}
            <span className="text-text-secondary font-medium">"커스텀 추가"</span>로 바로 연결해보세요.
          </p>
        )}

        {/* 카드 그리드 */}
        {results.length === 0 ? (
          <div className="py-16 text-center text-body-short-01 text-text-helper">
            "{query}"에 해당하는 커넥터가 없어요.
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
            {results.map((c) => {
              const added = existingIds.includes(c.id);
              const comingSoon = !!c.comingSoon;
              const disabled = added || comingSoon;
              const title = added
                ? '이미 추가됨'
                : comingSoon
                ? 'MVP에서는 아직 바로 연결할 수 없어요 — MCP 서버 URL이 있다면 "커스텀 추가"를 이용하세요'
                : `${c.name} 추가`;
              return (
                <div
                  key={c.id}
                  className={`group relative flex items-start gap-3 rounded-xl border border-border-subtle-01 bg-background p-3.5 transition-colors ${
                    comingSoon ? '' : 'hover:border-border-strong-01 hover:bg-layer-hover-01'
                  }`}
                >
                  <div
                    className={`w-9 h-9 flex-shrink-0 flex items-center justify-center rounded-lg bg-layer-03 ${
                      comingSoon ? 'opacity-50' : ''
                    }`}
                  >
                    <Icon name={c.icon} size={18} className="text-icon-primary" />
                  </div>
                  <div className={`flex-1 min-w-0 pr-7 ${comingSoon ? 'opacity-50' : ''}`}>
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-body-short-01 font-semibold text-text-primary truncate">{c.name}</span>
                      {comingSoon ? (
                        <Tag type="gray" size="sm">
                          준비 중
                        </Tag>
                      ) : (
                        c.badges?.map((b) => (
                          <Tag key={b} type={badgeType(b)} size="sm">
                            {b}
                          </Tag>
                        ))
                      )}
                    </div>
                    <p className="mt-1 text-caption-01 text-text-helper leading-4 line-clamp-2">{c.summary}</p>
                  </div>

                  {/* 추가 버튼 (우상단) */}
                  <button
                    onClick={() => !disabled && onAdd(c)}
                    disabled={disabled}
                    title={title}
                    aria-label={title}
                    className={`absolute top-2.5 right-2.5 w-7 h-7 flex items-center justify-center rounded-lg transition-colors ${
                      added
                        ? 'text-support-success cursor-default'
                        : comingSoon
                        ? 'text-icon-secondary opacity-40 cursor-not-allowed'
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
