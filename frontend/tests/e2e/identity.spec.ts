import { randomBytes } from 'node:crypto';
import { expect, test } from '@playwright/test';
import { authenticate, credentials } from './auth';

test('Russian login validates fields, signs in and signs out', async ({ page }, testInfo) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page.getByText('Заполните это поле.', { exact: true })).toHaveCount(2);
  await page.getByLabel('Имя пользователя', { exact: true }).fill(credentials().username);
  await page.getByLabel('Пароль', { exact: true }).fill(randomBytes(24).toString('hex'));
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось войти. Проверьте имя пользователя и пароль.');
  await expect(page.getByLabel('Пароль', { exact: true })).toHaveValue('');
  await page.screenshot({ path: testInfo.outputPath('login.png'), fullPage: true });
  await page.getByLabel('Пароль', { exact: true }).fill(credentials().password);
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Обзор', exact: true })).toBeVisible();
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);
  expect(await page.evaluate(() => document.cookie)).not.toContain('eventhub');
  await page.getByRole('button', { name: 'Выйти', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Войти', exact: true })).toBeVisible();
  expect((await page.request.get('/api/v1/users')).status()).toBe(401);
});

test('administrator manages users and reads translated audit changes', async ({ page, baseURL }, testInfo) => {
  await authenticate(page, baseURL!);
  const username = `user-${randomBytes(5).toString('hex')}`;
  await page.goto('/#users');
  await page.getByRole('button', { name: 'Создать пользователя', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Имя пользователя', { exact: true }).fill(username);
  await dialog.getByLabel('Отображаемое имя', { exact: true }).fill('Проверка пользователя');
  await dialog.getByLabel('Пароль', { exact: true }).fill(randomBytes(24).toString('hex'));
  await dialog.getByRole('button', { name: 'Сохранить', exact: true }).click();
  const row = page.getByRole('row').filter({ hasText: username });
  await expect(row).toContainText('Наблюдатель');
  await row.getByRole('button').click();
  await dialog.getByLabel('Роль', { exact: true }).selectOption('OPERATOR');
  await dialog.getByRole('checkbox').uncheck();
  await dialog.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(row).toContainText('Оператор');
  await expect(row).toContainText('Отключена');
  await page.screenshot({ path: testInfo.outputPath('users.png'), fullPage: true });
  await page.getByRole('link', { name: 'Журнал аудита', exact: true }).click();
  await page.getByLabel('Действие', { exact: true }).selectOption('USER_UPDATED');
  await expect(page.getByRole('row').filter({ hasText: 'Проверка пользователя' })).toContainText('Пользователь изменён');
  await expect(page.locator('main')).not.toContainText(/USER_UPDATED|OPERATOR|VIEWER|password_hash/);
  await page.screenshot({ path: testInfo.outputPath('audit.png'), fullPage: true });
});

for (const role of ['operator', 'viewer']) {
  test(`${role} cannot access administration via navigation or direct requests`, async ({ page, baseURL }) => {
    const identity = await authenticate(page, baseURL!, role);
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Обзор', exact: true })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Пользователи', exact: true })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Журнал аудита', exact: true })).toHaveCount(0);
    await page.goto('/#users');
    await expect(page.getByRole('alert')).toContainText('Недостаточно прав');
    expect((await page.request.get('/api/v1/audit')).status()).toBe(403);
    expect((await page.request.post('/api/v1/users', {
      headers: { Origin: baseURL!, 'X-CSRF-Token': identity.csrf_token },
      data: { username: 'forbidden', display_name: 'Недоступно', password: randomBytes(24).toString('hex') },
    })).status()).toBe(403);
  });
}

test('revoked session returns to Russian login', async ({ page, baseURL }) => {
  const identity = await authenticate(page, baseURL!);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Обзор', exact: true })).toBeVisible();
  await page.request.post('/api/v1/auth/logout', { headers: { Origin: baseURL!, 'X-CSRF-Token': identity.csrf_token } });
  await page.getByRole('link', { name: 'Пользователи', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Войти', exact: true })).toBeVisible();
  await expect(page.getByRole('status')).toContainText('Сеанс завершён. Войдите снова.');
});

test('password form changes a password and ends the session', async ({ page, baseURL }) => {
  const identity = await authenticate(page, baseURL!);
  const username = `pass-${randomBytes(5).toString('hex')}`;
  const password = randomBytes(24).toString('hex');
  const created = await page.request.post('/api/v1/users', {
    headers: { Origin: baseURL!, 'X-CSRF-Token': identity.csrf_token },
    data: { username, display_name: 'Смена пароля', password },
  });
  expect(created.status()).toBe(201);
  const preauth = await page.request.get('/api/v1/auth/csrf');
  expect((await page.request.post('/api/v1/auth/login', {
    headers: { Origin: baseURL!, 'X-CSRF-Token': (await preauth.json()).csrf_token },
    data: { username, password },
  })).status()).toBe(200);
  await page.goto('/#password');
  await page.getByLabel('Текущий пароль', { exact: true }).fill(password);
  const next = randomBytes(24).toString('hex');
  await page.getByLabel('Новый пароль', { exact: true }).fill(next);
  await page.getByLabel('Повторите пароль', { exact: true }).fill(next);
  await page.getByRole('button', { name: 'Сменить пароль', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Войти', exact: true })).toBeVisible();
  await expect(page.getByRole('status')).toContainText('Пароль изменён');
  await page.getByLabel('Имя пользователя', { exact: true }).fill(username);
  await page.getByLabel('Пароль', { exact: true }).fill(next);
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Сменить пароль', exact: true })).toBeVisible();
});
