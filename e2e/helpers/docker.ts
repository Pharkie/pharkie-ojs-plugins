import { spawnSync } from 'child_process';
import { resolve } from 'path';

const REPO_ROOT = resolve(__dirname, '..', '..');

/**
 * Build the docker compose command args, matching scripts/lib/dc.sh logic.
 * In DinD (devcontainer), volume mounts resolve against the HOST filesystem,
 * so --project-directory must be the host path (HOST_PROJECT_DIR env var).
 */
function getDCArgs(): string[] {
  const hostDir = process.env.HOST_PROJECT_DIR;
  if (hostDir) {
    // DinD: --project-directory is HOST path, -f and --env-file are container paths
    return [
      'compose',
      '--project-directory', hostDir,
      '-f', `${REPO_ROOT}/docker-compose.yml`,
      '--env-file', `${REPO_ROOT}/.env`,
    ];
  }
  // Regular Docker (native Linux, VPS)
  return ['compose', '-f', `${REPO_ROOT}/docker-compose.yml`, '--env-file', `${REPO_ROOT}/.env`];
}

/** Get a full "docker compose ..." command string for use with execSync/shell. */
export function getDCCommand(): string {
  return `docker ${getDCArgs().join(' ')}`;
}

export interface DockerExecOptions {
  /** Working directory inside the container */
  workdir?: string;
  /** Timeout in milliseconds (default: 60_000) */
  timeout?: number;
  /** Suppress errors and return empty string on failure */
  ignoreError?: boolean;
  /** Data to write to the container process's stdin */
  stdin?: string;
}

/**
 * Run a command inside a Docker Compose service container.
 */
export function dockerExec(
  service: string,
  command: string,
  opts: DockerExecOptions = {},
): string {
  const { workdir, timeout = 60_000, ignoreError = false, stdin } = opts;
  const args = [...getDCArgs(), 'exec', '-T'];
  if (workdir) {
    args.push('-w', workdir);
  }
  args.push(service, 'bash', '-c', command);

  try {
    const result = spawnSync('docker', args, {
      cwd: REPO_ROOT,
      timeout,
      encoding: 'utf-8',
      stdio: ['pipe', 'pipe', 'pipe'],
      input: stdin,
    });

    if (result.error) throw result.error;
    if (result.status !== 0) {
      throw new Error(result.stderr || result.stdout || 'Command failed');
    }

    return result.stdout.trim();
  } catch (err) {
    if (ignoreError) return '';
    throw err;
  }
}
