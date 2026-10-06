import { expect, test } from '@playwright/test';
import { authenticate } from './auth';

test('Russian routing builder, version history and read-only simulation', async ({ page, baseURL }, info) => {
  await authenticate(page, baseURL!);
  const suffix = Date.now().toString(); const name = `Обработка ${suffix}`;
  await page.goto('/#routing');
  await page.getByRole('button', { name: 'Создать правило', exact: true }).click();
  await page.getByLabel('Название', { exact: true }).fill(name);
  await page.getByLabel('Значение', { exact: true }).fill(`routing-${suffix}`);
  await page.getByLabel('Важность', { exact: true }).selectOption('CRITICAL');
  await page.getByRole('button', { name: 'Добавить действие', exact: true }).click();
  await page.getByLabel('Режим отправки', { exact: true }).selectOption('DELAYED');
  await page.getByLabel('Задержка, минут', { exact: true }).fill('15');
  await page.getByRole('button', { name: 'Добавить действие', exact: true }).click();
  await page.getByLabel('Действие', { exact: true }).nth(2).selectOption('SUPPRESS');
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('Настройки сохранены');
  await page.getByRole('button', { name: `Изменить «${name}»`, exact: true }).click();
  await page.getByLabel('Приоритет', { exact: true }).fill('5');
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('Настройки сохранены');
  await page.getByRole('button', { name: `История правила «${name}»`, exact: true }).click();
  await expect(page.getByText('Версия 2 · Администратор', { exact: false })).toBeVisible();
  await page.getByText('Версия 1 · Администратор', { exact: false }).click();
  await expect(page.locator('details[open]').getByLabel('Приоритет', { exact: true })).toHaveValue('100');
  await page.screenshot({ path: info.outputPath('routing-desktop.png'), fullPage: true });
  const before = (await (await page.request.get('/api/v1/events')).json()).items.map((v: { id: string }) => v.id);
  await page.goto('/#simulation');
  await page.getByLabel('Отправитель', { exact: true }).fill(`routing-${suffix}@example.org`);
  await page.getByLabel('Тема', { exact: true }).fill('Ошибка диска');
  await page.getByRole('button', { name: 'Проверить обработку', exact: true }).click();
  const result = page.locator('.simulation-result');
  await expect(result.getByRole('heading', { name: 'Результат проверки' })).toBeVisible();
  await expect(result.getByText('Подавлено', { exact: true })).toBeVisible();
  await expect(result.getByText('Критическая', { exact: true }).first()).toBeVisible();
  await expect(result.getByText('Отменено подавлением', { exact: true })).toBeVisible();
  expect((await (await page.request.get('/api/v1/events')).json()).items.map((v: { id: string }) => v.id)).toEqual(before);
  await expect(result).not.toContainText(/CRITICAL|SET_FIELDS|SUPPRESSED|PLANNED|rule_id|version_id/);
  await page.screenshot({ path: info.outputPath('simulation-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 375, height: 812 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('simulation-mobile.png'), fullPage: true });
  await page.route('**/api/v1/rules/simulate', (route) => route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ code: 'UNEXPECTED', detail: 'SQL SECRET TRACE' }) }));
  await page.getByRole('button', { name: 'Проверить обработку', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось выполнить действие');
  await expect(page.locator('main')).not.toContainText('SQL SECRET TRACE');
});

for (const role of ['viewer', 'operator'] as const) test(`${role} cannot access routing configuration or simulation`, async ({ page, baseURL }) => {
  await authenticate(page, baseURL!, role);
  for (const route of ['routing', 'simulation']) {
    await page.goto('/#' + route);
    await expect(page.getByRole('alert')).toContainText('Недостаточно прав');
  }
  expect((await page.request.get('/api/v1/routing-rules')).status()).toBe(403);
});
