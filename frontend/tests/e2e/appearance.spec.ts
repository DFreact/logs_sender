import { test, expect } from '@playwright/test';
import { authenticate } from './auth';

test('appearance is shared, preview is isolated, conflicts and permissions are safe', async ({ page, baseURL, browser }, info) => {
  const auth = await authenticate(page, baseURL!);
  const initial = await (await page.request.get('/api/v1/settings/appearance')).json();
  const headers = { Origin: new URL(baseURL!).origin, 'X-CSRF-Token': auth.csrf_token };
  const other = await browser.newContext({ baseURL }); const guest = await other.newPage();
  try {
    await page.goto('/#settings');
    await expect(page.getByRole('heading', { name: 'Оформление', exact: true })).toBeVisible();
    const palette = initial.palette === 'teal' ? 'violet' : 'teal';
    await page.getByLabel(palette === 'teal' ? 'Бирюзовый' : 'Фиолетовый', { exact: true }).check();
    await page.getByLabel('Режим оформления', { exact: true }).selectOption('dark');
    await expect(page.locator('html')).toHaveAttribute('data-palette', initial.palette);
    await expect(page.locator('.appearance-preview')).toHaveAttribute('data-palette', palette);
    await page.getByRole('button', { name: 'Сохранить оформление', exact: true }).click();
    await expect(page.locator('html')).toHaveAttribute('data-mode', 'dark');
    await expect(page.locator('html')).toHaveAttribute('data-palette', palette);
    await page.screenshot({ path: info.outputPath('settings-saved-dark.png'), fullPage: true });
    await guest.goto('/');
    await expect(guest.getByRole('button', { name: 'Войти', exact: true })).toBeVisible();
    await expect(guest.locator('html')).toHaveAttribute('data-palette', palette);
    await expect(guest.locator('html')).toHaveAttribute('data-mode', 'dark');
    // An administrator in another session saves a newer revision.
    const current = await (await page.request.get('/api/v1/settings/appearance')).json();
    expect((await page.request.patch('/api/v1/settings/appearance', { headers, data: { ...current, mode: 'light' } })).ok()).toBe(true);
    await page.getByLabel('Бордовый', { exact: true }).check();
    await page.getByRole('button', { name: 'Сохранить оформление', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('Данные уже изменены другим пользователем');
    await page.getByRole('button', { name: 'Обновить', exact: true }).click();
    await expect(page.getByLabel('Режим оформления', { exact: true })).toHaveValue('light');
    await authenticate(guest, baseURL!, 'viewer'); await guest.goto('/#settings'); await guest.reload();
    await expect(guest.getByRole('alert')).toContainText('Недостаточно прав');
    await expect(guest.getByRole('link', { name: 'Настройки', exact: true })).toHaveCount(0);
  } finally {
    const current = await (await page.request.get('/api/v1/settings/appearance')).json();
    await page.request.patch('/api/v1/settings/appearance', { headers, data: { ...initial, version: current.version } });
    await other.close();
  }
});

test('all twelve palettes keep text and focus contrast and keyboard navigation', async ({ page, baseURL }) => {
  await authenticate(page, baseURL!); await page.goto('/#settings');
  await expect(page.getByRole('heading', { name: 'Оформление', exact: true })).toBeVisible();
  for (const name of ['Синий', 'Бирюзовый', 'Зелёный', 'Фиолетовый', 'Бордовый', 'Графитовый']) {
    await page.getByLabel(name, { exact: true }).check();
    for (const mode of ['light', 'dark']) {
      await page.getByLabel('Режим оформления', { exact: true }).selectOption(mode);
      await page.locator('#appearance-example').focus();
      const ratios = await page.locator('.appearance-preview').evaluate((preview) => {
        const rgb = (v: string) => (v.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
        const luminance = (v: string) => rgb(v).map((n) => { const c = n / 255; return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4; }).reduce((s, v, i) => s + v * [.2126, .7152, .0722][i], 0);
        const ratio = (a: string, b: string) => { const x = luminance(a), y = luminance(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); };
        const results = [...preview.querySelectorAll('h3, .field-hint, input, .button-sample, .badge, .notice')].map((el) => {
          const css = getComputedStyle(el); let parent: Element | null = el; let background = css.backgroundColor;
          while (background === 'rgba(0, 0, 0, 0)' && parent?.parentElement) { parent = parent.parentElement; background = getComputedStyle(parent).backgroundColor; }
          return { element: el.tagName + '.' + el.className, contrast: ratio(css.color, background), minimum: 4.5 };
        });
        const input = preview.querySelector('input')!; const style = getComputedStyle(input);
        results.push({ element: 'focus', contrast: ratio(style.outlineColor, getComputedStyle(preview).backgroundColor), minimum: 3 });
        return results;
      });
      for (const result of ratios) expect(result.contrast, `${name} ${mode}: ${result.element}`).toBeGreaterThanOrEqual(result.minimum);
    }
  }
  await page.setViewportSize({ width: 375, height: 812 });
  const menu = page.getByRole('button', { name: 'Открыть меню', exact: true });
  await menu.focus(); await page.keyboard.press('Enter');
  await expect(page.getByRole('link', { name: 'События', exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'События', exact: true }).click();
  await expect(page.locator('#content')).toBeFocused();
  await expect(page.getByRole('button', { name: 'Открыть меню', exact: true })).toBeVisible();
});
