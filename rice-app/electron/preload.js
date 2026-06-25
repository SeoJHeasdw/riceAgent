import { contextBridge, ipcRenderer } from 'electron';
// Expose protected methods that allow the renderer process to use
// the ipcRenderer without exposing the entire object
contextBridge.exposeInMainWorld('electronAPI', {
    // Runtime management
    runtime: {
        start: function () { return ipcRenderer.invoke('runtime:start'); },
        stop: function () { return ipcRenderer.invoke('runtime:stop'); },
        getStatus: function () { return ipcRenderer.invoke('runtime:status'); },
        getPort: function () { return ipcRenderer.invoke('runtime:get-port'); },
    },
    // Window management
    window: {
        minimize: function () { return ipcRenderer.invoke('window:minimize'); },
        maximize: function () { return ipcRenderer.invoke('window:maximize'); },
        close: function () { return ipcRenderer.invoke('window:close'); },
    },
    // App info
    app: {
        getVersion: function () { return ipcRenderer.invoke('app:get-version'); },
        getPath: function (name) { return ipcRenderer.invoke('app:get-path', name); },
    },
});
// Made with Bob
