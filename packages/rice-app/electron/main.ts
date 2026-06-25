import { app, BrowserWindow, ipcMain } from 'electron';
import path from 'path';
import { fileURLToPath } from 'url';
import { RuntimeManager } from './runtime.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Runtime manager instance
let runtimeManager: RuntimeManager | null = null;

// Main window instance
let mainWindow: BrowserWindow | null = null;

const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1200,
    minHeight: 700,
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 16, y: 16 },
    backgroundColor: '#161616', // IBM Carbon gray-100
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      // Preload only uses contextBridge/ipcRenderer, which are sandbox-safe
      sandbox: true,
    },
  });

  // Load the app
  if (isDev) {
    mainWindow.loadURL('http://localhost:5173');
    mainWindow.webContents.openDevTools();
  } else {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'));
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// App lifecycle
app.whenReady().then(async () => {
  // Initialize runtime manager
  runtimeManager = new RuntimeManager();
  await runtimeManager.initialize();

  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', async () => {
  // Cleanup runtime manager
  if (runtimeManager) {
    await runtimeManager.shutdown();
  }
});

// IPC Handlers
ipcMain.handle('runtime:start', async () => {
  if (!runtimeManager) {
    throw new Error('Runtime manager not initialized');
  }
  return await runtimeManager.start();
});

ipcMain.handle('runtime:stop', async () => {
  if (!runtimeManager) {
    throw new Error('Runtime manager not initialized');
  }
  return await runtimeManager.stop();
});

ipcMain.handle('runtime:status', async () => {
  if (!runtimeManager) {
    throw new Error('Runtime manager not initialized');
  }
  return runtimeManager.getStatus();
});

ipcMain.handle('runtime:get-port', async () => {
  if (!runtimeManager) {
    throw new Error('Runtime manager not initialized');
  }
  return runtimeManager.getPort();
});

// Window management
ipcMain.handle('window:minimize', () => {
  mainWindow?.minimize();
});

ipcMain.handle('window:maximize', () => {
  if (mainWindow?.isMaximized()) {
    mainWindow.unmaximize();
  } else {
    mainWindow?.maximize();
  }
});

ipcMain.handle('window:close', () => {
  mainWindow?.close();
});

// App info
ipcMain.handle('app:get-version', () => {
  return app.getVersion();
});

const ALLOWED_PATH_NAMES = new Set([
  'home', 'appData', 'userData', 'sessionData', 'temp',
  'desktop', 'documents', 'downloads', 'logs',
]);

ipcMain.handle('app:get-path', (_event, name: string) => {
  if (!ALLOWED_PATH_NAMES.has(name)) {
    throw new Error(`Disallowed path name: ${name}`);
  }
  return app.getPath(name as Parameters<typeof app.getPath>[0]);
});

// Made with Bob
