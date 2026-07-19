import { app, BrowserWindow, dialog, ipcMain, Menu, shell } from 'electron';
import path from 'path';
import { promises as fs } from 'fs';
import { fileURLToPath } from 'url';
import { RuntimeManager } from './runtime.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Runtime manager instance
let runtimeManager: RuntimeManager | null = null;

// Main window instance
let mainWindow: BrowserWindow | null = null;
let shutdownInProgress = false;

const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1200,
    minHeight: 700,
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 16, y: 16 },
    autoHideMenuBar: true, // Windows 상단 메뉴바(File/Edit/View...) 숨김
    backgroundColor: '#16181b', // RICE soft graphite — matches the first renderer paint
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

// Windows에서 상단 네이티브 메뉴바(File/Edit/View/Window/Help) 완전 제거
if (process.platform !== 'darwin') {
  Menu.setApplicationMenu(null);
}

// App lifecycle
app.whenReady().then(async () => {
  runtimeManager = new RuntimeManager();
  await runtimeManager.initialize();

  // Paint the window immediately, then start the runtime in the background
  // (CONTRACTS §1.1). When it's healthy, push `runtime:ready` so the renderer
  // connects its API/SSE clients (App bootstrap). Spawn failure is non-fatal:
  // the renderer just stays "offline" and uses scripted mock data.
  createWindow();

  void runtimeManager
    .start()
    .then((status) => {
      if (status.running && mainWindow) {
        const send = () => mainWindow?.webContents.send('runtime:ready', status);
        if (mainWindow.webContents.isLoading()) {
          mainWindow.webContents.once('did-finish-load', send);
        } else {
          send();
        }
      }
    })
    .catch((err) => console.error('[main] runtime start failed (app stays offline):', err));

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

app.on('before-quit', (event) => {
  // Electron does not await async event listeners. Prevent the first quit,
  // reap the Python child, then allow a second quit event through.
  if (shutdownInProgress) return;
  event.preventDefault();
  shutdownInProgress = true;
  void (runtimeManager?.shutdown() ?? Promise.resolve())
    .catch((err) => console.error('[main] runtime shutdown failed:', err))
    .finally(() => app.quit());
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

ipcMain.handle('runtime:get-token', async () => {
  if (!runtimeManager) {
    throw new Error('Runtime manager not initialized');
  }
  return runtimeManager.getToken();
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

// Native directory picker for the workspace-output setting. The renderer only
// receives a user-selected directory path; it never receives filesystem access.
ipcMain.handle('dialog:select-directory', async (_event, defaultPath?: string) => {
  const result = await dialog.showOpenDialog(mainWindow ?? undefined, {
    title: 'RICE 생성 파일 저장 폴더 선택',
    defaultPath: typeof defaultPath === 'string' && defaultPath ? defaultPath : undefined,
    properties: ['openDirectory', 'createDirectory'],
  });
  return result.canceled ? null : result.filePaths[0] ?? null;
});

// Open the workspace output folder in the OS file manager (Finder / Explorer).
// The path must be an absolute directory path, otherwise the call is rejected.
ipcMain.handle('dialog:open-directory', async (_event, dirPath: string) => {
  if (typeof dirPath !== 'string' || !path.isAbsolute(dirPath)) {
    throw new Error('invalid directory path');
  }
  await shell.openPath(dirPath);
  return true;
});

// Open an external URL in the user's default browser (OAuth consent — CONTRACTS
// §4.2 step 3). Restricted to http/https so the renderer can't launch arbitrary
// schemes/executables.
ipcMain.handle('app:open-external', async (_event, url: string) => {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    throw new Error('invalid url');
  }
  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') {
    throw new Error(`disallowed protocol: ${parsed.protocol}`);
  }
  await shell.openExternal(url);
  return true;
});

// Artifact 다운로드 (platform tar.gz → 임시 파일).
// 런타임 POST /artifacts/install 은 로컬 파일 *경로* + manifest(checksum)를 요구하므로
// (CONTRACTS §2.3), 파일을 쓸 수 없는 렌더러 대신 main이 받아 임시 경로에 저장하고
// 경로와 X-Checksum(sha256-<hex>)을 돌려준다. runtime이 이 체크섬으로 무결성을 검증한다.
ipcMain.handle('artifact:download', async (_event, url: string) => {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`artifact download failed: ${res.status} ${res.statusText}`);
  }
  const checksum = res.headers.get('x-checksum') ?? '';
  const buf = Buffer.from(await res.arrayBuffer());

  // URL ".../artifacts/{id}/download" 에서 안전한 파일명 추출 (영숫자·-_ 만 허용).
  const segs = new URL(url).pathname.split('/').filter(Boolean);
  const dlIdx = segs.lastIndexOf('download');
  const rawId = dlIdx > 0 ? segs[dlIdx - 1] : `artifact-${Date.now()}`;
  const safeId = rawId.replace(/[^a-zA-Z0-9._-]/g, '_');

  const dir = path.join(app.getPath('temp'), 'rice-artifacts');
  await fs.mkdir(dir, { recursive: true });
  const filePath = path.join(dir, `${safeId}.tar.gz`);
  await fs.writeFile(filePath, buf);
  return { path: filePath, checksum };
});

// Made with Bob
