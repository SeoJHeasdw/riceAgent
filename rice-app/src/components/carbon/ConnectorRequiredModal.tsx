import { Button, Icon, Modal, Tag } from '../ui';

interface ConnectorRequiredModalProps {
  open: boolean;
  pluginName: string;
  connectors: string[];
  onClose: () => void;
  onConnect: () => void;
}

export function ConnectorRequiredModal({
  open,
  pluginName,
  connectors,
  onClose,
  onConnect,
}: ConnectorRequiredModalProps) {
  const names = connectors.length ? connectors : ['필요한 커넥터'];
  const connectorCount = names.length;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="활성화 전 연결 필요"
      size="sm"
      className="max-w-[520px]"
      footer={
        <>
          <Button kind="ghost" size="sm" onClick={onClose}>
            나중에
          </Button>
          <Button kind="primary" size="sm" icon={<Icon name="arrow-up-right" size={14} />} onClick={onConnect}>
            커넥터 화면으로
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="flex items-start gap-3.5">
          <div className="w-11 h-11 flex-shrink-0 flex items-center justify-center rounded-lg bg-purple-60/12 border border-purple-50/25 text-purple-40">
            <Icon name="connect" size={21} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <p className="text-heading-02 font-semibold text-text-primary leading-6">
                {pluginName}
              </p>
              <Tag type="purple" size="sm">
                연결 대기
              </Tag>
            </div>
            <p className="mt-1.5 text-body-short-01 text-text-secondary leading-5">
              플러그인을 켜기 전에 접근할 커넥터를 먼저 승인해야 합니다.
              연결 후 다시 활성화하면 이 플러그인의 스킬과 도구가 에이전트에 붙습니다.
            </p>
          </div>
        </div>

        <div className="rounded-xl border border-border-subtle-01 bg-background overflow-hidden">
          <div className="flex items-center justify-between px-3.5 py-2 border-b border-border-subtle-01">
            <span className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">
              필수 커넥터
            </span>
            <span className="text-caption-01 font-mono text-text-helper">{connectorCount}</span>
          </div>
          <div className="flex flex-col gap-px bg-border-subtle-01">
            {names.map((name) => (
              <div key={name} className="flex items-center gap-2.5 bg-background px-3.5 py-2.5">
                <div className="w-7 h-7 flex-shrink-0 rounded-lg border border-teal-40/25 bg-teal-60/10 text-teal-30 flex items-center justify-center">
                  <Icon name="connect" size={15} />
                </div>
                <span className="min-w-0 flex-1 truncate text-body-short-01 font-semibold text-text-primary">
                  {name}
                </span>
                <Tag type="yellow" size="sm">
                  연결 필요
                </Tag>
              </div>
            ))}
          </div>
        </div>

        <div className="flex items-start gap-2 rounded-lg border border-purple-50/20 bg-purple-60/[0.055] px-3 py-2.5">
          <Icon name="lock" size={15} className="mt-0.5 flex-shrink-0 text-purple-40" />
          <p className="text-caption-01 leading-5 text-text-secondary">
            연결은 커넥터 화면에서 권한 범위를 확인한 뒤 진행됩니다. 지금 닫아도 플러그인은 설치 상태로 남아요.
          </p>
        </div>
      </div>
    </Modal>
  );
}

// Made with Bob
