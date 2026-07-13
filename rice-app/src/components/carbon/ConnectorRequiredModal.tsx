import { useTranslation } from 'react-i18next';
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
  const { t } = useTranslation('connectors');
  const names = connectors.length ? connectors : [t('required.fallbackConnectorName')];
  const connectorCount = names.length;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t('required.title')}
      size="sm"
      className="max-w-[520px]"
      footer={
        <>
          <Button kind="ghost" size="sm" onClick={onClose}>
            {t('required.later')}
          </Button>
          <Button kind="primary" size="sm" icon={<Icon name="arrow-up-right" size={14} />} onClick={onConnect}>
            {t('required.goToConnectors')}
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
                {t('required.tagWaiting')}
              </Tag>
            </div>
            <p className="mt-1.5 text-body-short-01 text-text-secondary leading-5">
              {t('required.descriptionLine1')}
              {' '}
              {t('required.descriptionLine2')}
            </p>
          </div>
        </div>

        <div className="rounded-xl border border-border-subtle-01 bg-background overflow-hidden">
          <div className="flex items-center justify-between px-3.5 py-2 border-b border-border-subtle-01">
            <span className="text-caption-01 font-semibold uppercase tracking-wider text-text-secondary">
              {t('required.sectionLabel')}
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
                  {t('required.tagNeedsConnection')}
                </Tag>
              </div>
            ))}
          </div>
        </div>

        <div className="flex items-start gap-2 rounded-lg border border-purple-50/20 bg-purple-60/[0.055] px-3 py-2.5">
          <Icon name="lock" size={15} className="mt-0.5 flex-shrink-0 text-purple-40" />
          <p className="text-caption-01 leading-5 text-text-secondary">
            {t('required.hint')}
          </p>
        </div>
      </div>
    </Modal>
  );
}

// Made with Bob
