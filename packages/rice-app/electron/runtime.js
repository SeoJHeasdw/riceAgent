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
/**
 * RuntimeManager handles the lifecycle of the RICE runtime process.
 * This is a stub implementation that will be expanded to manage the actual runtime.
 */
var RuntimeManager = /** @class */ (function () {
    function RuntimeManager() {
        this.process = null;
        this.port = null;
        this.isRunning = false;
        // Default runtime port
        this.port = 8080;
    }
    /**
     * Initialize the runtime manager
     */
    RuntimeManager.prototype.initialize = function () {
        return __awaiter(this, void 0, void 0, function () {
            return __generator(this, function (_a) {
                console.log('[RuntimeManager] Initializing...');
                return [2 /*return*/];
            });
        });
    };
    /**
     * Start the runtime process
     */
    RuntimeManager.prototype.start = function () {
        return __awaiter(this, void 0, void 0, function () {
            return __generator(this, function (_a) {
                if (this.isRunning) {
                    console.log('[RuntimeManager] Runtime already running');
                    return [2 /*return*/];
                }
                console.log('[RuntimeManager] Starting runtime...');
                try {
                    // TODO: Implement actual runtime process spawning
                    // For now, this is a stub that simulates starting the runtime
                    // Example of how to spawn the runtime process:
                    // const runtimePath = path.join(app.getAppPath(), 'runtime', 'rice-runtime');
                    // this.process = spawn(runtimePath, ['--port', this.port.toString()], {
                    //   stdio: 'pipe',
                    //   env: {
                    //     ...process.env,
                    //     RICE_PORT: this.port.toString(),
                    //   },
                    // });
                    //
                    // this.process.on('error', (error) => {
                    //   console.error('[RuntimeManager] Runtime process error:', error);
                    //   this.isRunning = false;
                    // });
                    //
                    // this.process.on('exit', (code) => {
                    //   console.log('[RuntimeManager] Runtime process exited with code:', code);
                    //   this.isRunning = false;
                    // });
                    this.isRunning = true;
                    console.log('[RuntimeManager] Runtime started successfully');
                }
                catch (error) {
                    console.error('[RuntimeManager] Failed to start runtime:', error);
                    throw error;
                }
                return [2 /*return*/];
            });
        });
    };
    /**
     * Stop the runtime process
     */
    RuntimeManager.prototype.stop = function () {
        return __awaiter(this, void 0, void 0, function () {
            var error_1;
            var _this = this;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0:
                        if (!this.isRunning) {
                            console.log('[RuntimeManager] Runtime not running');
                            return [2 /*return*/];
                        }
                        console.log('[RuntimeManager] Stopping runtime...');
                        _a.label = 1;
                    case 1:
                        _a.trys.push([1, 4, , 5]);
                        if (!this.process) return [3 /*break*/, 3];
                        this.process.kill('SIGTERM');
                        // Wait for graceful shutdown
                        return [4 /*yield*/, new Promise(function (resolve) {
                                var _a;
                                var timeout = setTimeout(function () {
                                    if (_this.process) {
                                        _this.process.kill('SIGKILL');
                                    }
                                    resolve();
                                }, 5000);
                                (_a = _this.process) === null || _a === void 0 ? void 0 : _a.once('exit', function () {
                                    clearTimeout(timeout);
                                    resolve();
                                });
                            })];
                    case 2:
                        // Wait for graceful shutdown
                        _a.sent();
                        _a.label = 3;
                    case 3:
                        this.isRunning = false;
                        this.process = null;
                        console.log('[RuntimeManager] Runtime stopped successfully');
                        return [3 /*break*/, 5];
                    case 4:
                        error_1 = _a.sent();
                        console.error('[RuntimeManager] Failed to stop runtime:', error_1);
                        throw error_1;
                    case 5: return [2 /*return*/];
                }
            });
        });
    };
    /**
     * Get the current runtime status
     */
    RuntimeManager.prototype.getStatus = function () {
        var _a, _b;
        return {
            running: this.isRunning,
            port: this.isRunning ? (_a = this.port) !== null && _a !== void 0 ? _a : undefined : undefined,
            pid: (_b = this.process) === null || _b === void 0 ? void 0 : _b.pid,
        };
    };
    /**
     * Get the runtime port
     */
    RuntimeManager.prototype.getPort = function () {
        return this.isRunning ? this.port : null;
    };
    /**
     * Shutdown the runtime manager
     */
    RuntimeManager.prototype.shutdown = function () {
        return __awaiter(this, void 0, void 0, function () {
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0:
                        console.log('[RuntimeManager] Shutting down...');
                        return [4 /*yield*/, this.stop()];
                    case 1:
                        _a.sent();
                        return [2 /*return*/];
                }
            });
        });
    };
    return RuntimeManager;
}());
export { RuntimeManager };
// Made with Bob
