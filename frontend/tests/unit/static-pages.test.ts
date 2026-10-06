import { expect, it } from 'vitest';
import { staticPage } from '../../build/localized-pages';
import { defaultLocale } from '../../src/i18n/registry';

it('generates proxy pages from the catalog without remote dependencies', () => {
  const page = staticPage('errors.proxyTitle', 'errors.serviceUnavailable');
  expect(page).toContain('lang="ru"');
  expect(page).toContain(defaultLocale.messages['errors.serviceUnavailable']);
  expect(page).not.toMatch(/https?:\/\/|<script/i);
});
