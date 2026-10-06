import { test, expect } from '@playwright/test';
import { authenticate } from './auth';

test('retention validates Russian fields and confirms enabling deletion', async ({ page, baseURL }, info) => {
  const auth = await authenticate(page, baseURL!);
  const path = '/api/v1/settings/retention';
  const initial = await (await page.request.get(path)).json();
  const headers = { Origin: new URL(baseURL!).origin, 'X-CSRF-Token': auth.csrf_token };
  try {
    await page.goto('/#settings');
    await expect(page.getByRole('heading', { name: 'Сроки хранения', exact: true })).toBeVisible();
    await page.getByLabel('Срок хранения исходных сообщений, дней', { exact: true }).fill('0');
    await page.getByRole('button', { name: 'Сохранить сроки хранения', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('Укажите целое число');
    await page.getByLabel('Срок хранения исходных сообщений, дней', { exact: true }).fill('30');
    for (const field of await page.locator('.retention-settings input[type=number]').all()) await field.fill('3650');
    await page.getByLabel('Автоматическая очистка', { exact: true }).check();
    await page.getByRole('button', { name: 'Сохранить сроки хранения', exact: true }).click();
    await expect(page.getByRole('alertdialog')).toContainText('удаление');
    expect((await (await page.request.get(path)).json()).enabled).toBe(false);
    await page.getByRole('button', { name: 'Применить и разрешить удаление', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: 'Сроки хранения сохранены' })).toBeVisible();
    await page.reload();
    await expect(page.getByLabel('Автоматическая очистка', { exact: true })).toBeChecked();
    await page.setViewportSize({ width: 375, height: 900 });
    await expect(page.locator('html')).toHaveAttribute('lang', 'ru');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath('retention-mobile.png'), fullPage: true });
  } finally {
    const current = await (await page.request.get(path)).json();
    const { enabled, inherit, days } = initial;
    await page.request.patch(path, { headers, data: { version: current.version, enabled, inherit, days } });
  }
});
