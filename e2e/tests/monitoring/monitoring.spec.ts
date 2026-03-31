import { test, expect } from '@playwright/test';
import { execSync } from 'child_process';
import { resolve } from 'path';

/**
 * Tests for the monitoring scripts themselves ("checks for the checks").
 * These verify that the monitoring infrastructure works correctly.
 *
 * Run with the monitoring config:
 *   LIVE_WP_HOME=http://localhost:8080 LIVE_OJS_URL=http://localhost:8081 \
 *     npx playwright test --config=playwright.monitor.config.ts monitoring.spec.ts
 *
 * Or run just the syntax checks (no server needed):
 *   npx playwright test --config=playwright.monitor.config.ts -g "syntax"
 */

const PROJECT_ROOT = resolve(__dirname, '..', '..', '..');

test.describe('Monitoring script validation', () => {
  test('monitor-safe.sh passes syntax check', () => {
    const result = execSync(`bash -n ${PROJECT_ROOT}/scripts/monitoring/monitor-safe.sh 2>&1`, {
      encoding: 'utf-8',
    });
    // bash -n returns empty string on success
    expect(result.trim()).toBe('');
  });

  test('monitor-deep.sh passes syntax check', () => {
    const result = execSync(`bash -n ${PROJECT_ROOT}/scripts/monitoring/monitor-deep.sh 2>&1`, {
      encoding: 'utf-8',
    });
    expect(result.trim()).toBe('');
  });

  test('setup-betterstack.sh passes syntax check', () => {
    const result = execSync(`bash -n ${PROJECT_ROOT}/scripts/monitoring/setup-betterstack.sh 2>&1`, {
      encoding: 'utf-8',
    });
    expect(result.trim()).toBe('');
  });

  test('setup-betterstack.sh --dry-run requires --host', () => {
    try {
      execSync(`${PROJECT_ROOT}/scripts/monitoring/setup-betterstack.sh --dry-run 2>&1`, {
        encoding: 'utf-8',
      });
      expect(true).toBe(false); // Should have thrown
    } catch (e: unknown) {
      const error = e as { stdout?: string; stderr?: string };
      const output = (error.stdout || '') + (error.stderr || '');
      expect(output).toContain('--host=');
    }
  });

  test('setup-betterstack.sh requires BETTERSTACK_API_TOKEN', () => {
    try {
      execSync(`${PROJECT_ROOT}/scripts/monitoring/setup-betterstack.sh --host=test --dry-run 2>&1`, {
        encoding: 'utf-8',
        env: { ...process.env, BETTERSTACK_API_TOKEN: '' },
      });
      expect(true).toBe(false);
    } catch (e: unknown) {
      const error = e as { stdout?: string; stderr?: string };
      const output = (error.stdout || '') + (error.stderr || '');
      expect(output).toContain('BETTERSTACK_API_TOKEN');
    }
  });

  test('monitor scripts are executable', () => {
    const scripts = ['monitoring/monitor-safe.sh', 'monitoring/monitor-deep.sh', 'monitoring/setup-betterstack.sh'];
    for (const script of scripts) {
      const path = `${PROJECT_ROOT}/scripts/${script}`;
      const result = execSync(`test -x ${path} && echo "executable" || echo "not executable"`, {
        encoding: 'utf-8',
      });
      expect(result.trim()).toBe('executable');
    }
  });
});
