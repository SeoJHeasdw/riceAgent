import { spawnSync } from 'node:child_process';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const appDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const platformFlags = { darwin: '--mac', win32: '--win', linux: '--linux' };
const requestedFlag = process.argv[2] ?? platformFlags[process.platform];
const requestedPlatform = Object.entries(platformFlags).find(([, flag]) => flag === requestedFlag)?.[0];

if (!requestedPlatform) {
  throw new Error(`Expected one of ${Object.values(platformFlags).join(', ')} (got ${requestedFlag})`);
}
if (requestedPlatform !== process.platform) {
  throw new Error(
    `rice-runtime must be frozen on the target OS. Run ${requestedFlag} packaging on ${requestedPlatform}, not ${process.platform}.`,
  );
}
if (process.arch !== 'x64' && process.arch !== 'arm64') {
  throw new Error(`Unsupported package architecture: ${process.arch}`);
}

function run(command, args) {
  const result = spawnSync(command, args, { cwd: appDir, stdio: 'inherit', shell: false });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} exited with status ${result.status}`);
}

const npmCommand = process.platform === 'win32' ? 'npm.cmd' : 'npm';
run(npmCommand, ['run', 'prepare:runtime']);
run(npmCommand, ['run', 'type-check']);
run(npmCommand, ['run', 'build:electron']);
const builderArgs = [
  path.join(appDir, 'node_modules', 'electron-builder', 'out', 'cli', 'cli.js'),
  requestedFlag,
  `--${process.arch}`,
  '--config', 'electron-builder.yml',
  '--publish', 'never',
];
if (
  process.platform === 'darwin'
  && process.env.APPLE_TEAM_ID
  && (
    (process.env.APPLE_ID && process.env.APPLE_APP_SPECIFIC_PASSWORD)
    || (process.env.APPLE_API_KEY && process.env.APPLE_API_KEY_ID && process.env.APPLE_API_ISSUER)
  )
) {
  builderArgs.push('--config.mac.notarize=true');
}
run(process.execPath, builderArgs);
