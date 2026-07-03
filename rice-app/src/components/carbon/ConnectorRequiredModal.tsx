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

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="커넥터 연결 필요"
      size="sm"
      footer={
        <>
          <Button kind="secondary" size="sm" onClick={onClose}>
            닫기
          </Button>
          <Button kind="primary" size="sm" icon={<Icon name="arrow-up-right" size={14} />} onClick={onConnect}>
            커넥터 연결
          </Button>
        </>
      }
    >
      <div className="flex items-start gap-3">
        <div className="w-10 h-10 flex-shrink-0 flex items-center justify-center rounded-lg bg-purple-60/12 border border-purple-60/25">
          <Icon name="connect" size={20} className="text-purple-40" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-body-short-02 text-text-primary leading-6">
            <span className="font-semibold">{pluginName}</span> 플러그인을 활성화하려면 아래 커넥터를 먼저 연결해야 해요.
          </p>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {names.map((name) => (
              <Tag key={name} type="purple" size="sm">
                {name}
              </Tag>
            ))}
          </div>
          <p className="mt-3 text-caption-01 text-text-helper leading-5">
            연결 후 다시 활성화하면 에이전트가 이 플러그인의 스킬과 도구를 사용할 수 있습니다.
          </p>
        </div>
      </div>
    </Modal>
  );
}

// Made with Bob
