import React, { createContext, useCallback, useContext } from 'react';
import { api } from '../api';
import { useToast } from '../toast/ToastContext';

/**
 * FileViewerContext — "open a workspace file" capability shared across the chat.
 *
 * A workspace path (tool card, filename chip in an answer) calls `openFile(path)`,
 * which asks the local runtime to open the file in the **OS default app** (Excel →
 * office suite, .txt → the user's editor, …). The runtime runs on the user's
 * machine, so this works the same in browser-dev and the packaged desktop app.
 */
interface FileViewerValue {
  openFile: (path: string) => void;
}

const FileViewerContext = createContext<FileViewerValue>({ openFile: () => {} });

export const useFileViewer = () => useContext(FileViewerContext);

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
