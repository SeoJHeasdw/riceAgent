import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '../../lib/api';
import { Button, Icon } from '../ui';

export function RuntimeConnectionNotice() {
  const { t } = useTranslation('common');
  const [retrying, setRetrying] = useState(false);
  const [failed, setFailed] = useState(false);

  const retry = async () => {
    setRetrying(true);
    setFailed(false);
    const connected = await api.retryConnection();
    setFailed(!connected);
    setRetrying(false);
  };

  return (
    <div role="status" className="flex flex-shrink-0 items-center gap-3 border-b border-border-subtle-01 bg-layer-01 px-4 py-3">
      <Icon name="warning" size={18} className="flex-shrink-0 text-support-warning" />
      <div className="min-w-0 flex-1">
        <p className="text-body-short-01 font-medium text-text-primary">{t('runtime.unavailable')}</p>
        <p className="mt-0.5 text-caption-01 text-text-helper">
          {failed ? t('runtime.retryFailed') : t('runtime.unavailableHint')}
        </p>
      </div>
      <Button kind="secondary" size="sm" disabled={retrying} onClick={() => void retry()}>
        {retrying ? t('runtime.reconnecting') : t('runtime.reconnect')}
      </Button>
    </div>
  );
}
