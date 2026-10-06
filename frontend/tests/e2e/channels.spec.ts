import { test, expect } from '@playwright/test';
import { authenticate } from './auth';

test('Russian templates, safe preview, history and channel form', async ({ page, baseURL }, info) => {
  await authenticate(page, baseURL!);
  const name = `Письмо ${Date.now()}`;
  await page.goto('/#templates');
  await page.getByRole('button', { name: 'Создать шаблон', exact: true }).click();
  await page.getByLabel('Название', { exact: true }).fill(name);
  await page.getByLabel('Текст уведомления', { exact: true }).fill('Получено: {{ event.body }}');
  await page.getByLabel('Оформление письма', { exact: true }).fill('<p>{{ event.body }}</p><script>window.top.location="https://example.org"</script><img src="https://example.org/tracker">');
  await page.getByText('Данные тестового события', { exact: true }).click();
  await page.getByLabel('Текст события', { exact: true }).fill('<b>Проверка</b>');
  const before = (await (await page.request.get('/api/v1/events')).json()).items.map((v: { id: string }) => v.id);
  await page.getByRole('button', { name: 'Предпросмотр', exact: true }).click();
  const preview = page.getByRole('region', { name: 'Предпросмотр', exact: true });
  await expect(preview).toContainText('Получено: <b>Проверка</b>');
  const frame = page.frameLocator('iframe');
  await expect(frame.locator('body')).toHaveText('<b>Проверка</b>');
  await expect(frame.locator('script, img')).toHaveCount(0);
  expect((await (await page.request.get('/api/v1/events')).json()).items.map((v: { id: string }) => v.id)).toEqual(before);
  await page.screenshot({ path: info.outputPath('templates-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 375, height: 812 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('templates-mobile.png'), fullPage: true });
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('Шаблон сохранён');
  await page.getByRole('button', { name: `Изменить «${name}»`, exact: true }).click();
  await page.getByLabel('Текст уведомления', { exact: true }).fill('Новая версия');
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('Шаблон сохранён');
  await page.getByRole('button', { name: `История шаблона «${name}»`, exact: true }).click();
  await expect(page.getByText('Версия 2 · Администратор', { exact: false })).toBeVisible();
  await page.goto('/#channels');
  await page.getByRole('button', { name: 'Создать канал', exact: true }).click();
  await expect(page.getByLabel('Тип канала', { exact: true }).locator('option[value="TELEGRAM"]')).toHaveCount(0);
  await page.getByLabel('Название', { exact: true }).fill('Проверка запрета адреса');
  await page.getByLabel('Почтовый сервер', { exact: true }).fill('127.0.0.1');
  await page.getByLabel('Отправитель', { exact: true }).fill('hub@example.org');
  await page.getByLabel('Получатели', { exact: true }).fill('ops@example.org');
  await page.getByLabel('Пароль почтового сервера', { exact: true }).fill('TEST_SECRET_SHOULD_NOT_LEAK');
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Подключение к этому адресу запрещено');
  await expect(page.locator('main')).not.toContainText(/TEST_SECRET_SHOULD_NOT_LEAK|OUTBOUND_BLOCKED|Traceback/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('channels-mobile.png'), fullPage: true });
});

for (const role of ['viewer', 'operator'] as const) test(`${role} cannot change channels or templates`, async ({ page, baseURL }) => {
  await authenticate(page, baseURL!, role);
  for (const path of ['channels', 'templates']) {
    await page.goto('/#' + path);
    await expect(page.getByRole('alert')).toContainText('Недостаточно прав');
    expect((await page.request.get('/api/v1/' + path)).status()).toBe(403);
  }
});
