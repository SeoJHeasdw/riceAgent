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
  },

  // Artifact 다운로드 (platform tar.gz → 임시 경로 + 체크섬). 마켓 설치 파이프라인용.
  artifact: {
    download: (url: string) => ipcRenderer.invoke('artifact:download', url),
  },
});

// Type definitions for the exposed API
export interface ElectronAPI {
  runtime: {
    start: () => Promise<void>;
    stop: () => Promise<void>;
    getStatus: () => Promise<{
      running: boolean;
      port?: number;
      pid?: number;
    }>;
    getPort: () => Promise<number | null>;
  };
  window: {
    minimize: () => Promise<void>;
    maximize: () => Promise<void>;
    close: () => Promise<void>;
  };
  app: {
    getVersion: () => Promise<string>;
    getPath: (name: string) => Promise<string>;
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
