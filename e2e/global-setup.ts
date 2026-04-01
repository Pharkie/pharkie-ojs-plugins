import { createConnection } from 'net';
import { execSync, spawn } from 'child_process';
import { existsSync, writeFileSync, unlinkSync, readFileSync } from 'fs';
import { resolve } from 'path';
import { getDCCommand, needsSudo } from './helpers/docker';

const SUDO = needsSudo ? 'sudo ' : '';

const LOCKFILE = resolve(__dirname, '..', '.playwright-lock');

/**
 * Playwright global setup: ensure the devcontainer can reach the
 * Docker Compose services on localhost:8080 (WP) and localhost:8081 (OJS).
 *
 * The devcontainer is a sibling container — localhost doesn't route to
 * the compose services by default. We fix this by:
 * 1. Connecting to the compose Docker network (sea-net)
 * 2. Running socat to forward localhost ports to the service hostnames
 */
export default async function globalSetup() {
  // Prevent concurrent test runs — they corrupt shared Docker state.
  if (existsSync(LOCKFILE)) {
    const pid = readFileSync(LOCKFILE, 'utf-8').trim();
    // Check if the process is still alive
    try {
      process.kill(Number(pid), 0);
      throw new Error(
        `\n\nAnother Playwright run is active (PID ${pid}).\n` +
          'Concurrent runs corrupt shared Docker state.\n' +
          `If this is stale, remove ${LOCKFILE}\n`,
      );
    } catch (e: any) {
      if (e.code !== 'ESRCH') throw e;
      // Process is dead — stale lockfile, clean it up
    }
  }
  writeFileSync(LOCKFILE, String(process.pid));
  // Connect to compose network (idempotent).
  try {
    execSync(
      `${SUDO}docker network connect pharkie-ojs-plugins_sea-net $(hostname) 2>/dev/null || true`,
      { shell: '/bin/bash', timeout: 5000 },
    );
  } catch {}

  // Start socat forwards if the ports aren't already listening.
  await ensureForward(8080, 'wp', 80);
  await ensureForward(8081, 'ojs', 80);

  // Verify connectivity.
  const services = [
    { host: 'localhost', port: 8080, desc: 'WordPress (localhost:8080)' },
    { host: 'localhost', port: 8081, desc: 'OJS (localhost:8081)' },
  ];

  const down: string[] = [];
  for (const svc of services) {
    if (!(await tcpCheck(svc.host, svc.port))) down.push(svc.desc);
  }

  if (down.length > 0) {
    throw new Error(
      `\n\nServices not reachable: ${down.join(', ')}\n\n` +
        'Start them first:  docker compose up -d\n',
    );
  }

  // Ensure OJS URL is configured (may be blank if a previous error-recovery
  // test crashed before its afterAll could restore it).
  ensureOjsUrl();
}

async function ensureForward(
  localPort: number,
  remoteHost: string,
  remotePort: number,
): Promise<void> {
  // Already listening? Nothing to do.
  if (await tcpCheck('localhost', localPort, 500)) return;

  // Start socat in the background (detached, won't block tests).
  const child = spawn(
    'socat',
    [`TCP-LISTEN:${localPort},fork,reuseaddr`, `TCP:${remoteHost}:${remotePort}`],
    { detached: true, stdio: 'ignore' },
  );
  child.unref();

  // Wait briefly for it to start listening.
  for (let i = 0; i < 10; i++) {
    if (await tcpCheck('localhost', localPort, 300)) return;
  }
  throw new Error(`socat failed to start forwarding localhost:${localPort} → ${remoteHost}:${remotePort}`);
}

function ensureOjsUrl(): void {
  const dc = getDCCommand();
  const result = execSync(
    `${dc} exec -T wp wp option get wpojs_url --allow-root 2>/dev/null || true`,
    { encoding: 'utf-8', timeout: 10_000 },
  ).trim();

  if (!result || result === 'http://localhost:19999') {
    execSync(
      `${dc} exec -T wp wp option update wpojs_url 'http://ojs:80/index.php/ea' --allow-root 2>/dev/null`,
      { timeout: 10_000 },
    );
  }
}

function tcpCheck(host: string, port: number, timeoutMs = 3000): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = createConnection({ host, port });
    const timer = setTimeout(() => {
      socket.destroy();
      resolve(false);
    }, timeoutMs);
    socket.on('connect', () => {
      clearTimeout(timer);
      socket.destroy();
      resolve(true);
    });
    socket.on('error', () => {
      clearTimeout(timer);
      resolve(false);
    });
  });
}
