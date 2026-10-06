import { test, expect } from '@playwright/test';
import { authenticate } from './auth';

test('administrator creates, uses, hides and revokes an ingestion key', async ({ page, baseURL }, info) => {
  await authenticate(page, baseURL!);
  await page.goto('/#connections');
  await expect(page.getByLabel('Адрес приёма событий', { exact: true })).toHaveValue(`${new URL(baseURL!).origin}/api/v1/ingest`);
  const name = `Подключение ${Date.now()}`;
  await page.getByLabel('Название ключа', { exact: true }).fill(name);
  await page.getByRole('button', { name: 'Создать ключ', exact: true }).click();
  const field = page.getByLabel('Новый ключ приёма', { exact: true });
  await expect(field).toBeVisible();
  const token = await field.inputValue();
  expect(token).toMatch(/^[A-Za-z0-9_-]{43}$/);
  const headers = { Authorization: `Bearer ${token}` };
  expect((await page.request.post('/api/v1/ingest', { headers, data: { subject: 'Проверка подключения' } })).status()).toBe(202);
  await page.getByRole('button', { name: 'Ключ сохранён — скрыть', exact: true }).click();
  await expect(field).toHaveCount(0);
  await page.reload();
  await expect(field).toHaveCount(0);
  expect(await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage))).not.toContain(token);
  const row = page.getByRole('row').filter({ hasText: name });
  await row.getByRole('button', { name: `Отозвать ключ «${name}»`, exact: true }).click();
  await page.getByRole('button', { name: 'Отозвать ключ', exact: true }).click();
  await expect(row).toContainText('Отозван');
  expect((await page.request.post('/api/v1/ingest', { headers, data: {} })).status()).toBe(401);
  for (const width of [1440, 375, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath(`connections-${width}.png`), fullPage: true });
  }
});

for (const role of ['viewer', 'operator']) test(`${role} cannot manage connection keys`, async ({ page, baseURL }) => {
  await authenticate(page, baseURL!, role);
  await page.goto('/#connections');
  await expect(page.getByRole('alert')).toContainText('Недостаточно прав');
  expect((await page.request.get('/api/v1/connections/keys')).status()).toBe(403);
});

test('SMTP password can be replaced, preserved and removed from the channel form', async ({ page, baseURL }) => {
  await authenticate(page, baseURL!);
  await page.goto('/#channels');
  const name = `Смена пароля ${Date.now()}`;
  await page.getByRole('button', { name: 'Создать канал', exact: true }).click();
  await page.getByLabel('Название', { exact: true }).fill(name);
  await page.getByLabel('Почтовый сервер', { exact: true }).fill(process.env.E2E_DELIVERY_FIXTURE ? 'api.telegram.org' : '192.0.2.10');
  if (process.env.E2E_DELIVERY_FIXTURE) {
    await page.getByLabel('Порт', { exact: true }).fill('465');
    await page.getByLabel('Защита соединения', { exact: true }).selectOption('TLS');
  }
  await page.getByLabel('Отправитель', { exact: true }).fill('hub@example.org');
  await page.getByLabel('Получатели', { exact: true }).fill('ops@example.org');
  await page.getByLabel('Имя пользователя почтового сервера', { exact: true }).fill('smtp-user');
  await page.getByLabel('Пароль почтового сервера', { exact: true }).fill('test-only-first');
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('Настройки сохранены');
  await page.getByRole('button', { name: `Изменить «${name}»`, exact: true }).click();
  await expect(page.getByLabel('Пароль почтового сервера', { exact: true })).toHaveValue('');
  await expect(page.getByText('Версия сохранённого секрета: 1.', { exact: true })).toBeVisible();
  // Hold background list refreshes: the PATCH response must update the row immediately.
  await page.route('**/api/v1/channels', (route) => {
    if (route.request().method() !== 'GET') return route.continue();
  });
  await page.getByLabel('Пароль почтового сервера', { exact: true }).fill('test-only-second');
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('Настройки сохранены');
  await page.getByRole('button', { name: `Изменить «${name}»`, exact: true }).click();
  await expect(page.getByText('Версия сохранённого секрета: 2.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('Настройки сохранены');
  await page.getByRole('button', { name: `Изменить «${name}»`, exact: true }).click();
  await expect(page.getByText('Версия сохранённого секрета: 2.', { exact: true })).toBeVisible();
  await page.getByLabel('Удалить сохранённый секрет', { exact: true }).check();
  await expect(page.getByLabel('Пароль почтового сервера', { exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('Настройки сохранены');
  const result = await page.request.get('/api/v1/channels');
  const body = await result.json();
  const channel = body.items.find((v: { name: string }) => v.name === name);
  expect(channel.secret_configured).toBe(false);
  expect(channel.secret_version).toBe(3);
  expect(JSON.stringify(body)).not.toMatch(/test-only-first|test-only-second/);
});
