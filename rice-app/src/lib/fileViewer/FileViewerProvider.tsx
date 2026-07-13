import React, { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '../api';
import { apiErrorMessage } from '../i18n/apiError';
import { useToast } from '../toast/ToastContext';
import { FileViewerContext } from './FileViewerContext';

/**
 * FileViewerProvider — openFile 구현을 제공한다. 워크스페이스 경로를 받아
 * 로컬 런타임에 OS 기본 앱으로 열도록 요청하고 결과를 토스트로 알린다.
 */
export const FileViewerProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const toast = useToast();
  const { t } = useTranslation('common');

  const openFile = useCallback(
    (path: string) => {
      const clean = path.replace(/^\.\//, '').trim();
      void api.openWorkspaceFile(clean).then((res) => {
        if (res.success) {
          toast.notify({ message: t('fileViewer.openedSuccess', { path: clean }), variant: 'success' });
        } else {
          toast.notify({ message: apiErrorMessage(res) || t('fileViewer.openFailed'), variant: 'error' });
        }
      });
    },
    [toast, t],
  );

  return <FileViewerContext.Provider value={{ openFile }}>{children}</FileViewerContext.Provider>;
};

// Made with Bob
