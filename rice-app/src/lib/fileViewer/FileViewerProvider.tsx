import React, { useCallback } from 'react';
import { api } from '../api';
import { useToast } from '../toast/ToastContext';
import { FileViewerContext } from './FileViewerContext';

/**
 * FileViewerProvider — openFile 구현을 제공한다. 워크스페이스 경로를 받아
 * 로컬 런타임에 OS 기본 앱으로 열도록 요청하고 결과를 토스트로 알린다.
 */
export const FileViewerProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const toast = useToast();

  const openFile = useCallback(
    (path: string) => {
      const clean = path.replace(/^\.\//, '').trim();
      void api.openWorkspaceFile(clean).then((res) => {
        if (res.success) {
          toast.notify({ message: `시스템 기본 앱으로 열었어요 · ${clean}`, variant: 'success' });
        } else {
          toast.notify({ message: `파일을 열 수 없어요 · ${res.error ?? clean}`, variant: 'error' });
        }
      });
    },
    [toast],
  );

  return <FileViewerContext.Provider value={{ openFile }}>{children}</FileViewerContext.Provider>;
};

// Made with Bob
