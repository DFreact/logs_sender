import { expect, test } from '@playwright/test';
import { authenticate } from './auth';

test('administrator configures sources, visual conditions, deduplication and version history', async ({ page, baseURL }, info) => {
  await authenticate(page, baseURL!);
  const suffix = Date.now().toString();
  await page.goto('/#sources');
  await page.getByRole('button', { name: 'Создать источник', exact: true }).click();
  await page.getByLabel('Название', { exact: true }).fill(`Источник ${suffix}`);
  await page.getByLabel('Описание', { exact: true }).fill('Проверка источника');
  await page.getByLabel('Использовать общие настройки', { exact: true }).uncheck();
  await page.getByLabel('Объединять повторные сообщения', { exact: true }).check();
  await page.getByLabel('Окно объединения повторов, минут', { exact: true }).fill('10');
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('Настройки сохранены');
  await page.goto('/#identification');
  await page.getByRole('button', { name: 'Создать правило', exact: true }).click();
  await page.getByLabel('Название', { exact: true }).fill(`Правило ${suffix}`);
  await page.getByLabel('Источник', { exact: true }).selectOption({ label: `Источник ${suffix}` });
  await page.getByLabel('Значение', { exact: true }).fill(`sender-${suffix}`);
  await page.getByLabel('Важность', { exact: true }).selectOption('CRITICAL');
  await page.getByLabel('Категория', { exact: true }).fill('Инфраструктура');
  await page.getByRole('button', { name: 'Добавить группу', exact: true }).click();
  await page.getByLabel('Когда', { exact: true }).nth(1).selectOption('OR');
  await page.getByLabel('Поле', { exact: true }).nth(1).selectOption('subject');
  await page.getByLabel('Значение', { exact: true }).nth(1).fill('Ошибка');
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('Настройки сохранены');
  await page.getByRole('button', { name: `Изменить «Правило ${suffix}»`, exact: true }).click();
  await page.getByLabel('Приоритет', { exact: true }).fill('5');
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('Настройки сохранены');
  await page.getByRole('button', { name: `История правила «Правило ${suffix}»`, exact: true }).click();
  await expect(page.getByText('Версия 2 · Администратор', { exact: false })).toBeVisible();
  await page.getByText('Версия 1 · Администратор', { exact: false }).click();
  await expect(page.locator('details[open]').getByLabel('Приоритет', { exact: true })).toHaveValue('100');
  await expect(page.locator('main')).not.toContainText(/VERSION_CONFLICT|CRITICAL|not_equals/);
  await page.screenshot({ path: info.outputPath('rules-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 375, height: 812 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('rules-mobile.png'), fullPage: true });
});

test('viewer cannot configure sources or rules by direct navigation', async ({ page, baseURL }) => {
  await authenticate(page, baseURL!, 'viewer');
  for (const route of ['sources', 'identification']) {
    await page.goto('/#' + route);
    await expect(page.getByRole('alert')).toContainText('Недостаточно прав');
  }
  expect((await page.request.get('/api/v1/identification-rules')).status()).toBe(403);
});
