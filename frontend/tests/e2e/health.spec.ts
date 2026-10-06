import { expect, test } from '@playwright/test';
import { authenticate } from './auth';

test('Russian system health shows real component checks and survives a failed refresh', async ({ page, baseURL }, testInfo) => {
  await authenticate(page, baseURL!);
  await page.goto('/#health');
  await expect(page.getByRole('heading', { name: 'Состояние системы', exact: true })).toBeVisible();
  for (const name of ['База данных', 'Очередь задач', 'Хранилище файлов', 'Диспетчер задач', 'Планировщик', 'Обработчик задач', 'Приём почты', 'Обработка входящих сообщений']) {
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
  }
  await expect(page.getByText('Проверено:', { exact: false })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Фоновые задачи' })).toBeVisible();
  await expect(page.locator('main')).not.toContainText(/HEALTHY|DEGRADED|UNAVAILABLE|WORKER|Redis|lease_token|PostgreSQL/);
  await page.screenshot({ path: testInfo.outputPath('health-desktop.png'), fullPage: true });
  await page.route('**/api/v1/health', (route) => route.fulfill({ status: 503,
    json: { code: 'INTERNAL_SERVICE_FAILURE', detail: 'redis://secret-token/internal', request_id: 'a'.repeat(32) } }));
  await page.getByRole('button', { name: 'Обновить', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Показан результат последней успешной проверки' })).toBeVisible();
  await expect(page.locator('main')).not.toContainText(/secret-token|INTERNAL_SERVICE_FAILURE/);
  await page.unroute('**/api/v1/health');
  await page.getByRole('button', { name: 'Обновить', exact: true }).click();
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('health page fits mobile width', async ({ page, baseURL }, testInfo) => {
  await authenticate(page, baseURL!);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/#health');
  await expect(page.getByRole('heading', { name: 'Фоновые задачи' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('health-mobile.png'), fullPage: true });
});

test('viewer cannot open system health by direct URL or API', async ({ page, baseURL }) => {
  await authenticate(page, baseURL!, 'viewer');
  await page.goto('/#health');
  await expect(page.getByRole('alert')).toContainText('Недостаточно прав');
  await expect(page.getByRole('link', { name: 'Состояние системы', exact: true })).toHaveCount(0);
  expect((await page.request.get('/api/v1/health')).status()).toBe(403);
});

test('unknown measurements stay unknown and fit a narrow screen', async ({ page, baseURL }) => {
  await authenticate(page, baseURL!);
  const data = await (await page.request.get('/api/v1/health')).json();
  data.metrics = { fresh: false, sampled_at: null, rates: { accepted_total: null, normalized_total: null, sent_total: null }, disk: null, memory: null, cpu_percent: null, disk_remaining_seconds: null };
  data.queue.SUCCEEDED = 1_234_567;
  await page.route('**/api/v1/health', (route) => route.fulfill({ json: data }));
  await page.setViewportSize({ width: 320, height: 900 });
  await page.goto('/#health');
  await expect(page.getByText('Недостаточно данных', { exact: true }).first()).toBeVisible();
  await expect(page.locator('.metrics-panel')).not.toContainText('0 в секунду');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
