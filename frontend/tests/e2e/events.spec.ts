import { expect, test } from '@playwright/test';
import { authenticate } from './auth';

test('events filters, Russian detail, envelope and protected downloads', async ({ page, baseURL }, info) => {
  await authenticate(page, baseURL!, 'viewer');
  await page.goto('/#events');
  await expect(page.getByRole('heading', { name: 'События', exact: true })).toBeVisible();
  await page.getByLabel('Поиск', { exact: true }).fill('Проверка резервного');
  await page.getByRole('button', { name: 'Применить', exact: true }).click();
  await expect(page.getByRole('link', { name: 'Проверка резервного копирования', exact: true })).toBeVisible();
  await expect(page.locator('main')).not.toContainText(/ACKNOWLEDGED|NEW|RESOLVED|CRITICAL/);
  await page.getByRole('link', { name: 'Проверка резервного копирования', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'История поступлений' })).toBeVisible();
  await expect(page.getByText('Источник не определён', { exact: true })).toBeVisible();
  await expect(page.getByText('delivery@example.org', { exact: true })).toBeVisible();
  await expect(page.getByText('Резервная копия создана.', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('report.txt', { exact: true })).toBeVisible();
  const original = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Скачать исходное сообщение' }).click();
  expect((await original).suggestedFilename()).toBe('исходное-сообщение.eml');
  const attachment = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Скачать вложение' }).click();
  expect((await attachment).suggestedFilename()).toBe('вложение.bin');
  await page.screenshot({ path: info.outputPath('event-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 375, height: 812 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('event-mobile.png'), fullPage: true });
});

test('event empty filters, severity labels, reset and safe failure', async ({ page, baseURL }) => {
  await authenticate(page, baseURL!, 'operator');
  await page.goto('/#events');
  await page.getByLabel('Важность', { exact: true }).selectOption('WARNING');
  await page.getByRole('button', { name: 'Применить', exact: true }).click();
  await expect(page.getByRole('link', { name: 'Проверка диска', exact: true })).toBeVisible();
  await page.getByLabel('Поиск', { exact: true }).fill('Несуществующая тема');
  await page.getByRole('button', { name: 'Применить', exact: true }).click();
  await expect(page.getByText('События не найдены.', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Сбросить фильтры' }).click();
  await expect(page.getByRole('link', { name: 'Проверка диска', exact: true })).toBeVisible();
  await page.route('**/api/v1/events?*', (route) => route.fulfill({ status: 500, json: { code: 'INTERNAL_RAW_FAILURE', detail: 'SQL password secret' } }));
  await page.getByRole('button', { name: 'Обновить', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось выполнить действие');
  await expect(page.locator('main')).not.toContainText(/INTERNAL_RAW_FAILURE|SQL|password/);
});


test('large event feed keeps bounded pages and Russian period controls', async ({ page, baseURL }, info) => {
  await authenticate(page, baseURL!, 'viewer');
  await page.goto('/#events');
  await expect(page.getByRole('button', { name: 'Последние 24 часа', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Более ранние', exact: true })).toBeVisible();
  await expect(page.getByText('Показано на странице:', { exact: false })).toBeVisible();
  await page.getByLabel('Получено с', { exact: true }).fill('31.02.2026 12:00');
  await page.getByRole('button', { name: 'Применить', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Проверьте даты');
  await page.getByRole('button', { name: 'Последние 7 дней', exact: true }).click();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Проверка диска', exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath('event-feed-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 375, height: 812 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('event-feed-mobile.png'), fullPage: true });
});
