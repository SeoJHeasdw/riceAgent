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
}
declare global {
    interface Window {
        electronAPI: ElectronAPI;
    }
}
