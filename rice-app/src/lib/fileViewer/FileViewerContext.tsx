import { createContext, useContext } from 'react';

/**
 * FileViewerContext — "open a workspace file" capability shared across the chat.
 *
 * A workspace path (tool card, filename chip in an answer) calls `openFile(path)`,
 * which asks the local runtime to open the file in the **OS default app** (Excel →
 * office suite, .txt → the user's editor, …). The runtime runs on the user's
 * machine, so this works the same in browser-dev and the packaged desktop app.
 *
 * Provider 컴포넌트는 ./FileViewerProvider 로 분리되어 있다(fast-refresh 규칙).
 */
export interface FileViewerValue {
  openFile: (path: string) => void;
}

export const FileViewerContext = createContext<FileViewerValue>({ openFile: () => {} });

export const useFileViewer = () => useContext(FileViewerContext);

// Made with Bob
