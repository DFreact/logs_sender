import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, type Page } from '@playwright/test';

export function credentials(role = 'admin'): { username: string; password: string } {
  return JSON.parse(readFileSync(process.env.E2E_CREDENTIALS || resolve('../.local/e2e-credentials.json'), 'utf8'))[role];
}

export async function authenticate(page: Page, baseURL: string, role = 'admin') {
  const preauth = await page.request.get('/api/v1/auth/csrf');
  const response = await page.request.post('/api/v1/auth/login', {
    headers: { Origin: new URL(baseURL).origin, 'X-CSRF-Token': (await preauth.json()).csrf_token },
    data: credentials(role),
  });
  expect(response.status()).toBe(200);
  return response.json();
}
