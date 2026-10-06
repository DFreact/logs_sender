import { expect, test } from '@playwright/test';
import { authenticate } from './auth';

test.beforeEach(async ({ page, baseURL }) => { await authenticate(page, baseURL!); });

test('Russian shell uses the real API and only local resources', async ({ page, baseURL }, testInfo) => {
  const external: string[] = [];
  page.on('request', (request) => {
    if (new URL(request.url()).origin !== new URL(baseURL!).origin) external.push(request.url());
  });
  await page.goto('/');
  await expect(page).toHaveTitle('Центр событий и уведомлений');
  await expect(page.locator('html')).toHaveAttribute('lang', 'ru');
  await expect(page.getByRole('heading', { name: 'Обзор', exact: true })).toBeVisible();
  await expect(page.getByText('Приложение отвечает', { exact: true })).toBeVisible();
  await expect(page.getByText('Приём и просмотр событий')).toBeVisible();
  await expect(page.getByRole('combobox')).toHaveCount(0);
  await page.getByRole('button', { name: 'Проверить связь' }).click();
  await expect(page.getByText('Приложение отвечает', { exact: true })).toBeVisible();
  expect(external).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('overview-desktop.png'), fullPage: true });
});

test('unknown API errors never expose technical details', async ({ page }) => {
  await page.route('**/health/live', (route) => route.fulfill({ status: 500, json: {
    code: 'SQL_ERROR', detail: 'Traceback password=TEST_SECRET',
    params: { message: 'SQL exception' }, request_id: 'a'.repeat(32),
  } }));
  await page.goto('/');
  await expect(page.getByRole('region', { name: 'Связь с приложением', exact: true }).getByRole('status')).toContainText('Не удалось выполнить действие.');
  await expect(page.getByRole('region', { name: 'Связь с приложением', exact: true }).getByRole('status')).toContainText('Номер обращения:');
  await expect(page.locator('body')).not.toContainText(/SQL_ERROR|Traceback|TEST_SECRET|SQL exception/);
});

test('network failure offers recovery', async ({ page }) => {
  await page.route('**/health/live', (route) => route.abort('failed'));
  await page.goto('/');
  await expect(page.getByRole('region', { name: 'Связь с приложением', exact: true }).getByRole('status')).toContainText('Проверьте подключение к сети');
  await page.unroute('**/health/live');
  await page.getByRole('button', { name: 'Проверить связь' }).click();
  await expect(page.getByText('Приложение отвечает', { exact: true })).toBeVisible();
});

test('HTML proxy failure remains a safe Russian message', async ({ page }) => {
  await page.route('**/health/live', (route) => route.fulfill({ status: 502,
    contentType: 'text/html', body: '<h1>Bad Gateway internal-service secret-token</h1>' }));
  await page.goto('/');
  await expect(page.getByRole('region', { name: 'Связь с приложением', exact: true }).getByRole('status')).toContainText('Не удалось выполнить действие.');
  await expect(page.locator('body')).not.toContainText(/Bad Gateway|internal-service|secret-token/);
});

test('mobile layout fits the screen', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Проверить связь' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.getByText('Приложение отвечает', { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('overview-mobile.png'), fullPage: true });
});

test('bootstrap failure has a translated static fallback', async ({ page }) => {
  await page.route(/\/(src\/main\.tsx|assets\/index-[^/]+\.js)(\?.*)?$/, (route) => route.abort());
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Не удалось открыть страницу' })).toBeVisible();
  await expect(page.getByText(/Не удалось загрузить приложение/)).toBeVisible();
});

test('render failure is caught without displaying exception text', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(Intl.DateTimeFormat.prototype, 'format', {
      configurable: true, get: () => () => { throw new Error('Python exception password=RENDER_SECRET'); },
    });
  });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Не удалось открыть страницу' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Обновить страницу' })).toBeVisible();
  await expect(page.locator('body')).not.toContainText(/Python exception|RENDER_SECRET/);
});
