import { spawnSync } from 'child_process';
import { resolve } from 'path';

const REPO_ROOT = resolve(__dirname, '..', '..');

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
  const args = ['compose', 'exec', '-T'];
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
