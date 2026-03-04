import { test, expect } from '@playwright/test';
import { dockerExec } from '../helpers/docker';
import { ojsApiCall } from '../helpers/ojs';
import { wpEval } from '../helpers/wp';

/**
 * OJS API authentication: verify that the OJS plugin enforces auth correctly.
 *
 * Uses raw curl via dockerExec to bypass the valid-key helper (ojsApiCall),
 * allowing us to test with invalid/missing credentials.
 */
test.describe('OJS API authentication', () => {
  // Read OJS base URL once for raw curl tests.
  let ojsBaseUrl: string;

  test.beforeAll(() => {
    ojsBaseUrl = wpEval(`echo get_option('wpojs_url', '');`);
  });

  test('invalid API key → 401', () => {
    const apiUrl = `${ojsBaseUrl}/api/v1/wpojs/preflight`;
    const out = dockerExec(
      'wp',
      `curl -s -X GET -H 'Authorization: Bearer totally-bogus-key' -w '\\n%{http_code}' '${apiUrl}'`,
      { timeout: 15_000 },
    );

    const lines = out.trimEnd().split('\n');
    const statusCode = parseInt(lines.pop()!, 10);
    expect(statusCode).toBe(401);
  });

  test('missing Authorization header → 401', () => {
    const apiUrl = `${ojsBaseUrl}/api/v1/wpojs/preflight`;
    const out = dockerExec(
      'wp',
      `curl -s -X GET -w '\\n%{http_code}' '${apiUrl}'`,
      { timeout: 15_000 },
    );

    const lines = out.trimEnd().split('\n');
    const statusCode = parseInt(lines.pop()!, 10);
    expect(statusCode).toBe(401);
  });

  test('valid key → 200 on preflight', () => {
    const result = ojsApiCall('GET', '/wpojs/preflight');
    expect(result.status).toBe(200);
    expect(result.body).toHaveProperty('compatible');
  });

  test('ping works without auth', () => {
    const apiUrl = `${ojsBaseUrl}/api/v1/wpojs/ping`;
    const out = dockerExec(
      'wp',
      `curl -s -X GET -w '\\n%{http_code}' '${apiUrl}'`,
      { timeout: 15_000 },
    );

    const lines = out.trimEnd().split('\n');
    const statusCode = parseInt(lines.pop()!, 10);
    const body = lines.join('\n');

    expect(statusCode).toBe(200);
    expect(body).toContain('ok');
  });
});
