var __awaiter = (this && this.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
var __generator = (this && this.__generator) || function (thisArg, body) {
    var _ = { label: 0, sent: function() { if (t[0] & 1) throw t[1]; return t[1]; }, trys: [], ops: [] }, f, y, t, g = Object.create((typeof Iterator === "function" ? Iterator : Object).prototype);
    return g.next = verb(0), g["throw"] = verb(1), g["return"] = verb(2), typeof Symbol === "function" && (g[Symbol.iterator] = function() { return this; }), g;
    function verb(n) { return function (v) { return step([n, v]); }; }
    function step(op) {
        if (f) throw new TypeError("Generator is already executing.");
        while (g && (g = 0, op[0] && (_ = 0)), _) try {
            if (f = 1, y && (t = op[0] & 2 ? y["return"] : op[0] ? y["throw"] || ((t = y["return"]) && t.call(y), 0) : y.next) && !(t = t.call(y, op[1])).done) return t;
            if (y = 0, t) op = [op[0] & 2, t.value];
            switch (op[0]) {
                case 0: case 1: t = op; break;
                case 4: _.label++; return { value: op[1], done: false };
                case 5: _.label++; y = op[1]; op = [0]; continue;
                case 7: op = _.ops.pop(); _.trys.pop(); continue;
                default:
                    if (!(t = _.trys, t = t.length > 0 && t[t.length - 1]) && (op[0] === 6 || op[0] === 2)) { _ = 0; continue; }
                    if (op[0] === 3 && (!t || (op[1] > t[0] && op[1] < t[3]))) { _.label = op[1]; break; }
                    if (op[0] === 6 && _.label < t[1]) { _.label = t[1]; t = op; break; }
                    if (t && _.label < t[2]) { _.label = t[2]; _.ops.push(op); break; }
                    if (t[2]) _.ops.pop();
                    _.trys.pop(); continue;
            }
            op = body.call(thisArg, _);
        } catch (e) { op = [6, e]; y = 0; } finally { f = t = 0; }
        if (op[0] & 5) throw op[1]; return { value: op[0] ? op[1] : void 0, done: true };
    }
};
import { app, BrowserWindow, ipcMain } from 'electron';
import path from 'path';
import { fileURLToPath } from 'url';
import { RuntimeManager } from './runtime.js';
var __filename = fileURLToPath(import.meta.url);
var __dirname = path.dirname(__filename);
// Runtime manager instance
var runtimeManager = null;
// Main window instance
var mainWindow = null;
var isDev = process.env.NODE_ENV === 'development' || !app.isPackaged;
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
            preload: path.join(__dirname, 'preload.js'),
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
    }
    else {
        mainWindow.loadFile(path.join(__dirname, '../dist/index.html'));
    }
    mainWindow.on('closed', function () {
        mainWindow = null;
    });
}
// App lifecycle
app.whenReady().then(function () { return __awaiter(void 0, void 0, void 0, function () {
    return __generator(this, function (_a) {
        switch (_a.label) {
            case 0:
                // Initialize runtime manager
                runtimeManager = new RuntimeManager();
                return [4 /*yield*/, runtimeManager.initialize()];
            case 1:
                _a.sent();
                createWindow();
                app.on('activate', function () {
                    if (BrowserWindow.getAllWindows().length === 0) {
                        createWindow();
                    }
                });
                return [2 /*return*/];
        }
    });
}); });
app.on('window-all-closed', function () {
    if (process.platform !== 'darwin') {
        app.quit();
    }
});
app.on('before-quit', function () { return __awaiter(void 0, void 0, void 0, function () {
    return __generator(this, function (_a) {
        switch (_a.label) {
            case 0:
                if (!runtimeManager) return [3 /*break*/, 2];
                return [4 /*yield*/, runtimeManager.shutdown()];
            case 1:
                _a.sent();
                _a.label = 2;
            case 2: return [2 /*return*/];
        }
    });
}); });
// IPC Handlers
ipcMain.handle('runtime:start', function () { return __awaiter(void 0, void 0, void 0, function () {
    return __generator(this, function (_a) {
        switch (_a.label) {
            case 0:
                if (!runtimeManager) {
                    throw new Error('Runtime manager not initialized');
                }
                return [4 /*yield*/, runtimeManager.start()];
            case 1: return [2 /*return*/, _a.sent()];
        }
    });
}); });
ipcMain.handle('runtime:stop', function () { return __awaiter(void 0, void 0, void 0, function () {
    return __generator(this, function (_a) {
        switch (_a.label) {
            case 0:
                if (!runtimeManager) {
                    throw new Error('Runtime manager not initialized');
                }
                return [4 /*yield*/, runtimeManager.stop()];
            case 1: return [2 /*return*/, _a.sent()];
        }
    });
}); });
ipcMain.handle('runtime:status', function () { return __awaiter(void 0, void 0, void 0, function () {
    return __generator(this, function (_a) {
        if (!runtimeManager) {
            throw new Error('Runtime manager not initialized');
        }
        return [2 /*return*/, runtimeManager.getStatus()];
    });
}); });
ipcMain.handle('runtime:get-port', function () { return __awaiter(void 0, void 0, void 0, function () {
    return __generator(this, function (_a) {
        if (!runtimeManager) {
            throw new Error('Runtime manager not initialized');
        }
        return [2 /*return*/, runtimeManager.getPort()];
    });
}); });
// Window management
ipcMain.handle('window:minimize', function () {
    mainWindow === null || mainWindow === void 0 ? void 0 : mainWindow.minimize();
});
ipcMain.handle('window:maximize', function () {
    if (mainWindow === null || mainWindow === void 0 ? void 0 : mainWindow.isMaximized()) {
        mainWindow.unmaximize();
    }
    else {
        mainWindow === null || mainWindow === void 0 ? void 0 : mainWindow.maximize();
    }
});
ipcMain.handle('window:close', function () {
    mainWindow === null || mainWindow === void 0 ? void 0 : mainWindow.close();
});
// App info
ipcMain.handle('app:get-version', function () {
    return app.getVersion();
});
var ALLOWED_PATH_NAMES = new Set([
    'home', 'appData', 'userData', 'sessionData', 'temp',
    'desktop', 'documents', 'downloads', 'logs',
]);
ipcMain.handle('app:get-path', function (_event, name) {
    if (!ALLOWED_PATH_NAMES.has(name)) {
        throw new Error("Disallowed path name: ".concat(name));
    }
    return app.getPath(name);
});
// Made with Bob
