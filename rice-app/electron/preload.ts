import { contextBridge, ipcRenderer } from 'electron';

// Expose protected methods that allow the renderer process to use
// the ipcRenderer without exposing the entire object
contextBridge.exposeInMainWorld('electronAPI', {
  // Runtime management
  runtime: {
    start: () => ipcRenderer.invoke('runtime:start'),
    stop: () => ipcRenderer.invoke('runtime:stop'),
    getStatus: () => ipcRenderer.invoke('runtime:status'),
    getPort: () => ipcRenderer.invoke('runtime:get-port'),
    getToken: () => ipcRenderer.invoke('runtime:get-token'),
    factoryReset: () => ipcRenderer.invoke('runtime:factory-reset'),
    /** main pushes this once the runtime is healthy (App connects on it) */
    onReady: (cb: (status: { running: boolean; port?: number; token?: string }) => void) => {
      const listener = (_e: unknown, status: { running: boolean; port?: number; token?: string }) => cb(status);
      ipcRenderer.on('runtime:ready', listener);
      return () => ipcRenderer.removeListener('runtime:ready', listener);
    },
  },

  // Window management
  window: {
    minimize: () => ipcRenderer.invoke('window:minimize'),
    maximize: () => ipcRenderer.invoke('window:maximize'),
    close: () => ipcRenderer.invoke('window:close'),
  },

  // App info
  app: {
    getVersion: () => ipcRenderer.invoke('app:get-version'),
    getPath: (name: string) => ipcRenderer.invoke('app:get-path', name),
    /** open an http(s) URL in the system browser (OAuth consent — CONTRACTS §4.2) */
    openExternal: (url: string) => ipcRenderer.invoke('app:open-external', url),
  },

  dialog: {
    selectDirectory: (defaultPath?: string) => ipcRenderer.invoke('dialog:select-directory', defaultPath),
    openDirectory: (dirPath: string) => ipcRenderer.invoke('dialog:open-directory', dirPath),
  },

  // Artifact 다운로드 (platform tar.gz → 임시 경로 + 체크섬). 마켓 설치 파이프라인용.
  artifact: {
    download: (url: string) => ipcRenderer.invoke('artifact:download', url),
  },
});

// Type definitions for the exposed API
export interface ElectronAPI {
  runtime: {
    start: () => Promise<{ running: boolean; port?: number; pid?: number; token?: string }>;
    stop: () => Promise<void>;
    getStatus: () => Promise<{
      running: boolean;
      port?: number;
      pid?: number;
      token?: string;
    }>;
    getPort: () => Promise<number | null>;
    getToken: () => Promise<string | null>;
    factoryReset: () => Promise<{ running: boolean; port?: number; pid?: number; token?: string }>;
    onReady: (cb: (status: { running: boolean; port?: number; token?: string }) => void) => () => void;
  };
  window: {
    minimize: () => Promise<void>;
    maximize: () => Promise<void>;
    close: () => Promise<void>;
  };
  app: {
    getVersion: () => Promise<string>;
    getPath: (name: string) => Promise<string>;
    openExternal: (url: string) => Promise<boolean>;
  };
  dialog: {
    selectDirectory: (defaultPath?: string) => Promise<string | null>;
    openDirectory: (dirPath: string) => Promise<boolean>;
  };
  artifact: {
    download: (url: string) => Promise<{ path: string; checksum: string }>;
  };
}

declare global {
  interface Window {
    electronAPI: ElectronAPI;
  }
}

// Made with Bob
