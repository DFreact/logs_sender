import { test, expect, type Page } from '@playwright/test';
import { authenticate } from './auth';
import { writeFileSync } from 'node:fs';

test('all editors have labelled fields, separated actions and responsive layouts', async ({ page, baseURL }, info) => {
  test.setTimeout(240000);
  page.setDefaultTimeout(12000);
  const identity = await authenticate(page, baseURL!);
  const headers = { Origin: new URL(baseURL!).origin, 'X-CSRF-Token': identity.csrf_token };
  const sources = await (await page.request.get('/api/v1/sources')).json();
  if (!sources.items.length) {
    const response = await page.request.post('/api/v1/sources', { headers, data: { name: 'Проверкадлинногоназванияисточника'.repeat(3), description: '', enabled: true, version: 1, dedup: { enabled: false, inherit: true, window_seconds: 600, fields: ['sender', 'subject', 'body'], version: 1 } } });
    expect(response.ok()).toBe(true);
  }
  // Make every adapter form available without allowing any outbound connections.
  await page.route('**/api/v1/output-adapters', async (route) => {
    const response = await route.fetch(); const body = await response.json();
    body.items = body.items.map((v: object) => ({ ...v, installed: true, enabled: true, visible: true }));
    await route.fulfill({ response, json: body });
  });
  const samples: object[] = [];
  async function review(name: string, screenshot = false) {
    for (const width of [1440, 768, 375, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      const result = await inspect(page);
      expect(result.problems, `${name} ${width}`).toEqual([]);
      samples.push({ name, width, ...result });
      if (screenshot && [1440, 375].includes(width)) await page.screenshot({ path: info.outputPath(`${name}-${width}.png`), fullPage: false });
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
  }
  async function open(path: string) {
    await page.goto('/#' + path);
    await expect(page.locator('main h1')).toBeVisible();
    await expect(page.getByText('Загрузка…', { exact: true })).toHaveCount(0);
  }
  for (const route of ['overview', 'events', 'notifications', 'dead-letters', 'connections', 'sources', 'identification', 'routing', 'channels', 'templates', 'escalations', 'digests', 'users', 'audit', 'health', 'password', 'settings']) {
    await open(route); await review(route, ['events', 'settings', 'health'].includes(route));
  }
  await open('sources'); await page.getByRole('button', { name: 'Создать источник', exact: true }).click();
  await page.getByLabel('Использовать общие настройки').uncheck(); await review('source-editor', true);
  await open('sources'); await page.getByRole('button', { name: 'Общие настройки повторов', exact: true }).click(); await review('global-dedup');
  await open('identification'); await page.getByRole('button', { name: 'Создать правило', exact: true }).click(); await review('identification-editor', true);
  for (const field of ['header', 'metadata', 'adapter', 'body']) {
    await page.getByLabel('Поле', { exact: true }).first().selectOption(field); await review('condition-' + field);
  }
  await page.getByLabel('Сравнение', { exact: true }).first().selectOption('regex'); await review('condition-regex');
  await open('routing'); await page.getByRole('button', { name: 'Создать правило', exact: true }).click();
  for (const kind of ['SET_FIELDS', 'SUPPRESS', 'NOTIFY', 'DIGEST', 'ESCALATE']) {
    await page.getByLabel('Действие', { exact: true }).selectOption(kind);
    if (kind === 'SET_FIELDS') { await page.getByLabel('Изменить категорию', { exact: true }).check(); await page.getByLabel('Изменить тип события', { exact: true }).check(); }
    await review('routing-' + kind, kind === 'NOTIFY');
    if (kind === 'NOTIFY') for (const mode of ['DELAYED', 'SCHEDULED']) { await page.getByLabel('Режим отправки', { exact: true }).selectOption(mode); await review('routing-' + mode); if (mode === 'SCHEDULED') { const date = page.getByLabel('Дата и время отправки', { exact: true }); await date.fill('31.02.2026 12:00'); await expect(date).toHaveAttribute('aria-invalid', 'true'); await expect(page.getByText('Укажите существующие дату и время в формате ДД.ММ.ГГГГ ЧЧ:ММ.', { exact: true })).toBeVisible(); await date.fill('31.12.2026 12:00'); await expect(date).not.toHaveAttribute('aria-invalid', 'true'); } }
  }
  await open('simulation'); await page.getByRole('button', { name: 'Добавить заголовок', exact: true }).click(); await review('simulation-SMTP'); await page.getByLabel('Входной адаптер', { exact: true }).selectOption('REST'); await page.getByRole('button', { name: 'Добавить поле', exact: true }).click();
  for (const type of ['TEXT', 'NUMBER', 'BOOLEAN', 'NULL']) { await page.getByLabel('Тип значения', { exact: true }).selectOption(type); await review('simulation-' + type, type === 'TEXT'); }
  await open('channels'); await page.getByRole('button', { name: 'Создать канал', exact: true }).click();
  for (const kind of ['SMTP', 'WEBHOOK', 'TELEGRAM', 'MAX']) { await page.getByLabel('Тип канала', { exact: true }).selectOption(kind); await review('channel-' + kind, true); }
  await open('templates'); await page.getByRole('button', { name: 'Создать шаблон', exact: true }).click(); await page.getByText('Данные тестового события', { exact: true }).click();
  for (const kind of ['SMTP', 'WEBHOOK', 'TELEGRAM', 'MAX']) { await page.getByLabel('Тип канала', { exact: true }).selectOption(kind); await review('template-' + kind, kind === 'SMTP'); }
  await open('escalations'); await page.getByRole('button', { name: 'Создать политику', exact: true }).click(); await page.getByRole('button', { name: 'Добавить шаг', exact: true }).click(); await review('escalation-editor', true);
  await open('digests'); await page.getByRole('button', { name: 'Создать сводку', exact: true }).click(); await review('digest-editor', true);
  await open('users'); await page.getByRole('button', { name: 'Создать пользователя', exact: true }).click(); await review('user-dialog', true); await page.getByRole('button', { name: 'Отменить', exact: true }).click();
  const events = await (await page.request.get('/api/v1/events')).json();
  const event = events.items.find((item: { status: string }) => ['NEW', 'ACKNOWLEDGED'].includes(item.status));
  if (event) { await open('events/' + event.id); await review('event-detail', true); await page.getByRole('button', { name: 'Подавить', exact: true }).click(); await review('event-confirmation'); }
  await open('settings'); await page.getByLabel('Фиолетовый', { exact: true }).check(); await page.getByLabel('Режим оформления', { exact: true }).selectOption('dark'); await review('settings-dark-preview', true);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.getByRole('button', { name: 'Открыть меню', exact: true }).click(); await expect(page.getByRole('link', { name: 'Настройки', exact: true })).toBeVisible(); await review('mobile-menu');
  writeFileSync(info.outputPath('field-review.json'), JSON.stringify(samples, null, 2));
  await info.attach('field-review', { path: info.outputPath('field-review.json'), contentType: 'application/json' });
});

async function inspect(page: Page) {
  return page.evaluate(() => {
    const problems: string[] = [], fields: string[] = [];
    const visible = (el: Element) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
    if (document.documentElement.scrollWidth > innerWidth + 1) problems.push('page-overflow');
    const ids = [...document.querySelectorAll('[id]')].map((el) => el.id);
    if (new Set(ids).size !== ids.length) problems.push('duplicate-id');
    for (const el of document.querySelectorAll<HTMLInputElement>('input, select, textarea')) {
      if (!visible(el)) continue;
      const label = el.labels?.[0]?.textContent?.trim() || el.getAttribute('aria-label') || '';
      fields.push(label);
      if (!label) problems.push('unlabelled:' + el.id);
      const r = el.getBoundingClientRect();
      if (r.width < (['checkbox', 'radio'].includes(el.type) ? 16 : 70) || r.height < 18) problems.push('small-field:' + el.id);
      const hint = document.getElementById(el.id + '-hint');
      if (hint && !(el.getAttribute('aria-describedby') || '').split(' ').includes(hint.id)) problems.push('unlinked-hint:' + el.id);
    }
    const buttons = [...document.querySelectorAll('button')].filter(visible);
    for (let i = 0; i < buttons.length; i++) {
      const a = buttons[i], ar = a.getBoundingClientRect();
      if (ar.height < 32) problems.push('small-button:' + a.textContent);
      for (const b of buttons.slice(i + 1)) {
        if (a.parentElement !== b.parentElement) continue;
        const br = b.getBoundingClientRect();
        const horizontalGap = Math.max(br.left - ar.right, ar.left - br.right);
        const verticalGap = Math.max(br.top - ar.bottom, ar.top - br.bottom);
        if (horizontalGap < 6 && verticalGap < 6) problems.push('touching-actions:' + a.textContent + '/' + b.textContent);
      }
    }
    return { problems, fields, buttons: buttons.length };
  });
}
