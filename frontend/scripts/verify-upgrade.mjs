import { chromium } from '@playwright/test';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
const base = process.argv[2], output = resolve(process.argv[3]);
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base)) throw new Error('LOCAL_TARGET_REQUIRED');
const account = JSON.parse(readFileSync(resolve('../.local/admin-credentials.json'), 'utf8'));
mkdirSync(output, { recursive: true });
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const outside = [], errors = [];
  page.on('pageerror', () => errors.push('pageerror'));
  await page.route('**/*', (route) => {
    if (new URL(route.request().url()).origin !== base) { outside.push('external'); return route.abort(); }
    return route.continue();
  });
  await page.goto(base);
  await page.getByLabel('Имя пользователя', { exact: true }).fill(account.username);
  await page.getByLabel('Пароль', { exact: true }).fill(account.password);
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await page.getByRole('link', { name: 'Подключение источников', exact: true }).click();
  await page.getByLabel('Адрес приёма событий', { exact: true }).waitFor();
  await page.screenshot({ path: resolve(output, 'connections-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 375, height: 1000 });
  if (!(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))) throw new Error('OVERFLOW');
  await page.screenshot({ path: resolve(output, 'connections-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(base + '/#channels');
  await page.getByRole('button', { name: 'Создать канал', exact: true }).click();
  await page.getByLabel('Пароль почтового сервера', { exact: true }).waitFor();
  await page.screenshot({ path: resolve(output, 'channel-editor.png'), fullPage: true });
  await page.getByRole('button', { name: 'Выйти', exact: true }).click();
  await page.getByRole('button', { name: 'Войти', exact: true }).waitFor();
  if (outside.length || errors.length) throw new Error('UI_CHECK_FAILED');
  writeFileSync(resolve(output, 'browser.json'), JSON.stringify({ admin_login: true, connections: true, channel_editor: true, logout: true, mobile_overflow: false, external_requests: 0, page_errors: 0 }, null, 2));
  console.log('Вход, новые разделы, узкий экран и отсутствие внешних запросов проверены.');
} finally { await browser.close(); }
