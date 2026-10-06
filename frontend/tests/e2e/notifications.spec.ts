import { test, expect } from '@playwright/test';
import { authenticate } from './auth';

test.skip(!process.env.E2E_DELIVERY_FIXTURE, 'Requires the disposable local delivery destination');

test('viewer reads Russian delivery history and ambiguity on desktop and mobile', async ({ page, baseURL }, info) => {
  await authenticate(page, baseURL!, 'viewer');
  await page.goto('/#notifications');
  await expect(page.getByRole('heading', { name: 'Уведомления', exact: true })).toBeVisible();
  await page.getByLabel('Состояние', { exact: true }).selectOption('SENT');
  await page.getByRole('link', { name: 'delivery-check uncertain-once', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'История попыток отправки' })).toBeVisible();
  await expect(page.locator('main')).toContainText('Получатель мог принять сообщение');
  await expect(page.getByRole('cell', { name: 'Результат неизвестен', exact: true })).toBeVisible();
  await expect(page.locator('pre')).toContainText('Kaspersky — Критическая');
  await expect(page.getByRole('button', { name: 'Повторить отправку', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Отменить уведомление', exact: true })).toHaveCount(0);
  await expect(page.locator('main')).not.toContainText(/UNKNOWN_RESULT|PROCESSING|secret-details|Traceback/);
  await page.screenshot({ path: info.outputPath('notification-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 375, height: 812 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('notification-mobile.png'), fullPage: true });
});

test('operator retries and cancels a dead letter with history preserved', async ({ page, baseURL }) => {
  await authenticate(page, baseURL!, 'operator');
  await page.goto('/#dead-letters');
  await expect(page.getByRole('heading', { name: 'Неотправленные уведомления', exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'delivery-check permanent', exact: true }).click();
  await expect(page.locator('main')).toContainText('Сервер отклонил авторизацию');
  await page.getByRole('button', { name: 'Повторить отправку', exact: true }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Повторить отправку', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Повторная отправка назначена');
  await expect(page.getByRole('button', { name: 'Повторить отправку', exact: true })).toBeVisible({ timeout: 20000 });
  await expect(page.locator('tbody tr')).toHaveCount(2);
  await page.getByRole('button', { name: 'Отменить уведомление', exact: true }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Отменить уведомление', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('Уведомление отменено');
  await expect(page.locator('dd').filter({ hasText: /^Отменено$/ })).toBeVisible();
  await expect(page.locator('tbody tr')).toHaveCount(2);
  await expect(page.getByRole('button', { name: 'Повторить отправку', exact: true })).toHaveCount(0);
});

test('administrator explicitly sends a test notification to the local fixture', async ({ page, baseURL }) => {
  await authenticate(page, baseURL!);
  await page.goto('/#channels');
  const row = page.getByRole('row').filter({ hasText: 'Локальная проверка доставки' });
  await row.getByRole('button', { name: 'Отправить тестовое уведомление', exact: true }).click();
  const form = page.getByRole('form', { name: 'Тестовое уведомление', exact: true });
  await expect(form).toContainText('Сообщение будет отправлено получателю канала');
  await form.getByLabel('Тема', { exact: true }).fill('Проверка из интерфейса');
  await form.getByLabel('Текст события', { exact: true }).fill('Локальное тестовое уведомление');
  await form.getByRole('button', { name: 'Отправить тестовое уведомление', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Уведомление', exact: true })).toBeVisible();
  await expect(page.locator('dd').filter({ hasText: /^Отправлено$/ })).toBeVisible({ timeout: 20000 });
  await expect(page.locator('pre')).toContainText('Локальное тестовое уведомление');
});
