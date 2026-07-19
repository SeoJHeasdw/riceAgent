import { spawn, spawnSync } from 'node:child_process';
import { createServer } from 'node:net';
import { existsSync } from 'node:fs';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const appDir = path.resolve(scriptDir, '..');
const runtimeDir = path.resolve(appDir, '..', 'rice-runtime');
const platform = process.platform;
const arch = process.arch;
const supportedPlatforms = new Set(['darwin', 'linux', 'win32']);
const supportedArchitectures = new Set(['x64', 'arm64']);

if (!supportedPlatforms.has(platform) || !supportedArchitectures.has(arch)) {
  throw new Error(`Unsupported runtime bundle target: ${platform}-${arch}`);
}

const targetKey = `${platform}-${arch}`;
const buildRoot = path.join(appDir, '.runtime-build', targetKey);
const venvDir = path.join(buildRoot, 'venv');
const distRoot = path.join(appDir, 'runtime-dist', targetKey);
const executableName = platform === 'win32' ? 'rice-runtime.exe' : 'rice-runtime';
const bundledExecutable = path.join(distRoot, 'rice-runtime', executableName);
const pythonCandidates = process.env.RICE_BUILD_PYTHON
  ? [process.env.RICE_BUILD_PYTHON]
  : platform === 'win32'
    ? ['py', 'python3.13', 'python']
    : ['python3.13', 'python3', 'python'];

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? appDir,
    env: options.env ?? process.env,
    encoding: 'utf8',
    stdio: options.capture ? 'pipe' : 'inherit',
    shell: false,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const detail = options.capture ? `\n${result.stdout ?? ''}${result.stderr ?? ''}` : '';
    throw new Error(`${command} exited with status ${result.status}${detail}`);
  }
  return options.capture ? String(result.stdout).trim() : '';
}

function commandWorks(command, args) {
  const result = spawnSync(command, args, { encoding: 'utf8', shell: false });
  return !result.error && result.status === 0;
}

function resolveBuildPython() {
  for (const candidate of pythonCandidates) {
    const prefix = candidate === 'py' ? ['-3.13'] : [];
    if (!commandWorks(candidate, [...prefix, '-c', 'import sys; raise SystemExit(sys.version_info < (3, 13))'])) continue;
    const info = run(
      candidate,
      [...prefix, '-c', 'import json, platform, sys; print(json.dumps({"version": list(sys.version_info[:3]), "machine": platform.machine(), "executable": sys.executable}))'],
      { capture: true },
    );
    return { command: candidate, prefix, info: JSON.parse(info) };
  }
  throw new Error('Python 3.13+ is required on the build machine (set RICE_BUILD_PYTHON to override).');
}

function normalizeMachine(machine) {
  const value = machine.toLowerCase();
  if (value === 'x86_64' || value === 'amd64') return 'x64';
  if (value === 'aarch64' || value === 'arm64') return 'arm64';
  return value;
}

function venvPython() {
  return platform === 'win32'
    ? path.join(venvDir, 'Scripts', 'python.exe')
    : path.join(venvDir, 'bin', 'python');
}

async function freePort() {
  return await new Promise((resolve, reject) => {
    const server = createServer();
    server.unref();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      const port = typeof address === 'object' && address ? address.port : 0;
      server.close(() => resolve(port));
    });
  });
}

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function waitForChildExit(child, timeoutMs, label) {
  if (child.exitCode !== null) return Promise.resolve(child.exitCode);
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`${label} timed out`));
    }, timeoutMs);
    child.once('exit', (code) => {
      clearTimeout(timer);
      resolve(code);
    });
    child.once('error', (error) => {
      clearTimeout(timer);
      reject(error);
    });
  });
}

async function smokeTest(executable) {
  const stateDir = await mkdtemp(path.join(tmpdir(), 'rice-runtime-smoke-'));
  const port = await freePort();
  const token = `build-smoke-${process.pid}-${Date.now()}`;
  const child = spawn(executable, ['--port', String(port), '--token', token, '--log-level', 'warning'], {
    cwd: path.dirname(executable),
    env: { ...process.env, RICE_HOME: stateDir, PYTHONUNBUFFERED: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  const exited = waitForChildExit(child, 35_000, 'bundled runtime');
  let output = '';
  child.stdout?.on('data', (chunk) => { output += String(chunk); });
  child.stderr?.on('data', (chunk) => { output += String(chunk); });

  try {
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      if (child.exitCode !== null) throw new Error(`bundled runtime exited with ${child.exitCode}\n${output}`);
      try {
        const response = await fetch(`http://127.0.0.1:${port}/health`);
        if (response.ok) {
          const health = await response.json();
          if (health.status !== 'ok') throw new Error(`unexpected health response: ${JSON.stringify(health)}`);
          await fetch(`http://127.0.0.1:${port}/shutdown`, {
            method: 'POST',
            headers: { Authorization: `Bearer ${token}` },
          });
          return;
        }
      } catch {
        // Server is still starting.
      }
      await delay(250);
    }
    throw new Error(`bundled runtime did not become healthy\n${output}`);
  } finally {
    if (child.exitCode === null) child.kill();
    await Promise.race([exited.catch(() => undefined), delay(3_000)]);
    await rm(stateDir, { recursive: true, force: true });
  }
}

async function smokeTestFrozenWorker(executable) {
  const pluginDir = await mkdtemp(path.join(tmpdir(), 'rice-worker-smoke-'));
  await writeFile(
    path.join(pluginDir, 'rice_smoke_plugin.py'),
    'def increment(value):\n    return value + 1\n',
    'utf8',
  );
  try {
    const child = spawn(executable, ['--sandbox-worker', '--sandbox-path', pluginDir], {
      cwd: path.dirname(executable),
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    });
    // The first launch of a newly generated unsigned Mach-O can spend several
    // seconds in Gatekeeper before Python starts. Signed CI artifacts are
    // faster, but keep the build smoke test tolerant of local first-launch QA.
    const exited = waitForChildExit(child, 30_000, 'frozen sandbox worker');
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => { stdout += String(chunk); });
    child.stderr.on('data', (chunk) => { stderr += String(chunk); });
    child.stdin.end(`${JSON.stringify({ entrypoint: 'rice_smoke_plugin.increment', payload: { value: 41 } })}\n`);

    const exitCode = await exited;
    const lines = stdout.trim().split(/\r?\n/);
    const response = JSON.parse(lines.at(-1) ?? '{}');
    if (exitCode !== 0 || response.ok !== true || response.result !== 42) {
      throw new Error(`frozen sandbox worker failed (${exitCode}): ${stdout}\n${stderr}`);
    }
  } finally {
    await rm(pluginDir, { recursive: true, force: true });
  }
}

console.log(`[runtime-bundle] target ${targetKey}`);
const buildPython = resolveBuildPython();
const machine = normalizeMachine(buildPython.info.machine);
if (machine !== arch) {
  throw new Error(`Python architecture ${machine} does not match Electron target ${arch}: ${buildPython.info.executable}`);
}
console.log(`[runtime-bundle] Python ${buildPython.info.version.join('.')} (${buildPython.info.executable})`);

await mkdir(buildRoot, { recursive: true });
if (!existsSync(venvPython())) {
  run(buildPython.command, [...buildPython.prefix, '-m', 'venv', venvDir], { cwd: runtimeDir });
}

run(venvPython(), ['-m', 'pip', 'install', '--disable-pip-version-check', '-r', 'packaging/requirements-bundle.txt'], { cwd: runtimeDir });
run(
  venvPython(),
  ['-m', 'pip', 'install', '--disable-pip-version-check', '--no-build-isolation', '.[claude,connectors]'],
  { cwd: runtimeDir },
);
run(
  venvPython(),
  [
    '-m', 'PyInstaller',
    '--noconfirm',
    '--clean',
    '--distpath', distRoot,
    '--workpath', path.join(buildRoot, 'pyinstaller'),
    'packaging/rice-runtime.spec',
  ],
  {
    cwd: runtimeDir,
    // Keep PyInstaller's signed-binary cache inside the build tree. This makes
    // sandboxed/local builds reproducible and avoids mutating ~/Library or
    // platform-equivalent user cache directories.
    env: { ...process.env, PYINSTALLER_CONFIG_DIR: path.join(buildRoot, 'pyinstaller-cache') },
  },
);

if (!existsSync(bundledExecutable)) {
  throw new Error(`PyInstaller did not create ${bundledExecutable}`);
}
await smokeTestFrozenWorker(bundledExecutable);
await smokeTest(bundledExecutable);
console.log(`[runtime-bundle] ready and healthy: ${bundledExecutable}`);
