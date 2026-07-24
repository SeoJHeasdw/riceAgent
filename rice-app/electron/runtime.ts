import { spawn, type ChildProcess } from 'child_process';
import net from 'net';
import crypto from 'crypto';
import path from 'path';
import os from 'os';
import { existsSync, promises as fs } from 'fs';
import { app } from 'electron';

export interface RuntimeStatus {
  running: boolean;
  port?: number;
  pid?: number;
  /** one-shot localhost bearer token (CONTRACTS §1.1) — renderer sends it as Bearer */
  token?: string;
}

interface RuntimeCommand {
  cmd: string;
  args: string[];
  sourceTree: boolean;
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Ask the OS for a free TCP port (bind :0, read it back, release). */
function findFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const addr = srv.address();
      const port = typeof addr === 'object' && addr ? addr.port : 0;
      srv.close(() => resolve(port));
    });
  });
}

/**
 * RuntimeManager — owns the rice-runtime child process lifecycle (CONTRACTS §1.1).
 *
 * On start it: picks a free port, mints a one-shot bearer token, spawns the
 * Python runtime (`rice-runtime --port <p> --token <t>`, localhost-only), and
 * waits for `/health` before reporting running. The renderer reads port+token via
 * IPC and points the API/SSE clients at the live runtime.
 *
 * Command resolution (first match wins):
 *   1. env `RICE_RUNTIME_BIN`            — explicit executable path
 *   2. packaged `<resources>/rice-runtime/rice-runtime[.exe]` (PyInstaller)
 *   3. dev `<runtimeDir>/.venv/bin/rice-runtime` (POSIX) / `Scripts\rice-runtime.exe` (Win)
 *   4. dev `python3 -m rice_runtime` with cwd=<runtimeDir> (PYTHONPATH=src)
 * where <runtimeDir> = env `RICE_RUNTIME_DIR` → (dev) `<repo>/rice-runtime`
 *   → (packaged) `<resources>/rice-runtime`.
 *
 * Spawn failures (no Python, etc.) are non-fatal: the app stays usable and the
 * renderer falls back to scripted mock data (App bootstrap stays "offline").
 */
export class RuntimeManager {
  private process: ChildProcess | null = null;
  private port: number | null = null;
  private token: string | null = null;
  private isRunning = false;
  private startPromise: Promise<RuntimeStatus> | null = null;
  private resetInProgress = false;

  async initialize(): Promise<void> {
    console.log('[RuntimeManager] Initializing...');
  }

  /** Resolve the rice-runtime source/install dir. */
  private runtimeDir(): string {
    if (process.env.RICE_RUNTIME_DIR) return process.env.RICE_RUNTIME_DIR;
    const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged;
    if (isDev) {
      // app.getAppPath() → rice-app in dev; the runtime is its sibling.
      return path.resolve(app.getAppPath(), '..', 'rice-runtime');
    }
    return path.join(process.resourcesPath, 'rice-runtime');
  }

  /**
   * The runtime owns this directory in its entirety. Keeping resolution here
   * and passing the same value to every child makes factory reset independent
   * of dev/packaged Python defaults.
   */
  private dataRoot(): string {
    const configured = process.env.RICE_HOME?.trim();
    const root = configured
      ? path.resolve(configured)
      : app.isPackaged
        ? path.join(app.getPath('userData'), 'runtime')
        : path.join(os.homedir(), '.rice');
    return this.assertSafeDataRoot(root);
  }

  /**
   * RICE_HOME is an ownership declaration, but reject roots broad enough to
   * erase the account, app installation, or current source tree if it was
   * accidentally misconfigured.
   */
  private assertSafeDataRoot(candidate: string): string {
    const resolved = path.resolve(candidate);
    const normalized = process.platform === 'win32' ? resolved.toLowerCase() : resolved;
    const protectedPaths = [
      path.parse(resolved).root,
      os.homedir(),
      os.tmpdir(),
      app.getPath('appData'),
      app.getPath('userData'),
      app.getPath('temp'),
      app.getAppPath(),
      this.runtimeDir(),
      process.cwd(),
    ].map((item) => {
      const value = path.resolve(item);
      return process.platform === 'win32' ? value.toLowerCase() : value;
    });

    const containsProtectedPath = protectedPaths.some(
      (item) => normalized === item || item.startsWith(`${normalized}${path.sep}`),
    );
    if (containsProtectedPath) {
      throw new Error(`Refusing unsafe RICE data root: ${resolved}`);
    }
    return resolved;
  }

  /** (command, args) to launch, given the chosen port + token. */
  private resolveCommand(dir: string, port: number, token: string): RuntimeCommand {
    const common = ['--port', String(port), '--token', token];

    const explicit = process.env.RICE_RUNTIME_BIN;
    if (explicit && existsSync(explicit)) return { cmd: explicit, args: common, sourceTree: false };

    if (app.isPackaged) {
      const bundled = path.join(dir, process.platform === 'win32' ? 'rice-runtime.exe' : 'rice-runtime');
      if (!existsSync(bundled)) {
        throw new Error(`bundled rice-runtime executable is missing: ${bundled}`);
      }
      return { cmd: bundled, args: common, sourceTree: false };
    }

    const venvBin = process.platform === 'win32'
      ? path.join(dir, '.venv', 'Scripts', 'rice-runtime.exe')
      : path.join(dir, '.venv', 'bin', 'rice-runtime');
    if (existsSync(venvBin)) return { cmd: venvBin, args: common, sourceTree: false };

    // Fallback: module run via python (PYTHONPATH=src set in spawn env).
    const py = process.platform === 'win32' ? 'python' : 'python3';
    return { cmd: py, args: ['-m', 'rice_runtime', ...common], sourceTree: true };
  }

  /** Poll /health until the runtime answers (or timeout). */
  private async waitForHealth(port: number, timeoutMs = 30000): Promise<boolean> {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (!this.process || this.process.exitCode !== null) return false; // child died
      try {
        const res = await fetch(`http://127.0.0.1:${port}/health`);
        if (res.ok) return true;
      } catch {
        /* not up yet */
      }
      await sleep(300);
    }
    return false;
  }

  async start(): Promise<RuntimeStatus> {
    if (this.isRunning) {
      console.log('[RuntimeManager] Runtime already running');
      return this.getStatus();
    }
    if (this.startPromise) return this.startPromise;

    const pending = this.startOnce();
    this.startPromise = pending;
    try {
      return await pending;
    } finally {
      if (this.startPromise === pending) this.startPromise = null;
    }
  }

  private async startOnce(): Promise<RuntimeStatus> {
    const dir = this.runtimeDir();
    const port = await findFreePort();
    const token = crypto.randomBytes(24).toString('hex');
    const { cmd, args, sourceTree } = this.resolveCommand(dir, port, token);

    // Never print the one-shot bearer token into application logs.
    console.log(`[RuntimeManager] Starting: ${cmd} --port ${port} --token <redacted> (cwd=${dir})`);
    const childEnv: NodeJS.ProcessEnv = {
      ...process.env,
      PYTHONUNBUFFERED: '1',
      RICE_HOME: this.dataRoot(),
    };
    if (sourceTree) childEnv.PYTHONPATH = path.join(dir, 'src');
    this.process = spawn(cmd, args, {
      cwd: dir,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: childEnv,
      windowsHide: true,
    });

    this.process.stdout?.on('data', (d) => console.log(`[runtime] ${String(d).trimEnd()}`));
    this.process.stderr?.on('data', (d) => console.warn(`[runtime] ${String(d).trimEnd()}`));
    this.process.on('error', (err) => {
      console.error('[RuntimeManager] spawn error:', err);
      this.isRunning = false;
    });
    this.process.on('exit', (code) => {
      console.log('[RuntimeManager] runtime exited:', code);
      this.isRunning = false;
      this.process = null;
    });

    const healthy = await this.waitForHealth(port);
    if (!healthy) {
      console.error('[RuntimeManager] runtime did not become healthy — staying offline');
      await this.stop();
      return { running: false };
    }

    this.port = port;
    this.token = token;
    this.isRunning = true;
    console.log(`[RuntimeManager] Runtime healthy on :${port}`);
    return this.getStatus();
  }

  async stop(): Promise<void> {
    const proc = this.process;
    if (!proc) {
      this.isRunning = false;
      return;
    }
    console.log('[RuntimeManager] Stopping runtime...');
    try {
      // Ask the runtime to tear down gracefully first (best-effort), then signal.
      proc.kill('SIGTERM');
      await new Promise<void>((resolve) => {
        const timeout = setTimeout(() => {
          try { proc.kill('SIGKILL'); } catch { /* already gone */ }
          resolve();
        }, 5000);
        proc.once('exit', () => { clearTimeout(timeout); resolve(); });
      });
    } finally {
      this.isRunning = false;
      this.process = null;
      this.port = null;
      this.token = null;
    }
  }

  getStatus(): RuntimeStatus {
    return {
      running: this.isRunning,
      port: this.isRunning ? this.port ?? undefined : undefined,
      pid: this.process?.pid,
      token: this.isRunning ? this.token ?? undefined : undefined,
    };
  }

  getPort(): number | null {
    return this.isRunning ? this.port : null;
  }

  getToken(): string | null {
    return this.isRunning ? this.token : null;
  }

  /** Ask the live runtime to remove OS-keychain data that is not under RICE_HOME. */
  private async prepareFactoryReset(): Promise<void> {
    if (!this.isRunning) {
      const started = await this.start();
      if (!started.running) {
        throw new Error('Runtime is unavailable for factory-reset preparation');
      }
    }
    if (!this.port || !this.token) {
      throw new Error('Runtime credentials are unavailable for factory reset');
    }
    const response = await fetch(`http://127.0.0.1:${this.port}/factory-reset/prepare`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${this.token}` },
    });
    const body = await response.json() as { success?: boolean; error?: string };
    if (!response.ok || !body.success) {
      throw new Error(body.error || `Factory-reset preparation failed (${response.status})`);
    }
  }

  /**
   * True factory reset: release the live SQLite/WAL handles, remove the entire
   * RICE-owned state tree, then boot against a newly-created empty root.
   * Immutable builtins are code assets and are discovered again on that boot.
   */
  async factoryReset(): Promise<RuntimeStatus> {
    if (this.resetInProgress) {
      throw new Error('Runtime factory reset is already in progress');
    }
    this.resetInProgress = true;
    const root = this.dataRoot();
    console.log(`[RuntimeManager] Factory reset: ${root}`);
    try {
      await this.prepareFactoryReset();
      await this.stop();
      await fs.rm(root, { recursive: true, force: true });
      await fs.mkdir(root, { recursive: true });
      const status = await this.start();
      if (!status.running) {
        throw new Error('Runtime did not restart after factory reset');
      }
      return status;
    } finally {
      this.resetInProgress = false;
    }
  }

  async shutdown(): Promise<void> {
    console.log('[RuntimeManager] Shutting down...');
    await this.stop();
  }
}

// Made with Bob
