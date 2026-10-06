import type { Plugin } from 'vite';
import { defaultLocale } from '../src/i18n/registry.ts';
import { createI18n, validateCatalog } from '../src/i18n/core.ts';
import type { TranslationKey } from '../src/i18n/types.ts';

const { t } = createI18n(defaultLocale, 'UTC');
const escape = (value: string) => value.replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[char]!);

export function fallbackContent(title: TranslationKey, body: TranslationKey) {
  return `<main class="fatal-page"><h1>${escape(t(title))}</h1><p>${escape(t(body))}</p><a href="/">${escape(t('common.home'))}</a></main>`;
}

export function staticPage(title: TranslationKey, body: TranslationKey): string {
  return `<!doctype html><html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>${escape(t(title))}</title></head><body>${fallbackContent(title, body)}</body></html>`;
}

export function localizedPages(): Plugin {
  return {
    name: 'localized-pages',
    buildStart() { validateCatalog(defaultLocale.messages, defaultLocale.messages); },
    transformIndexHtml(html) {
      return html.replace('__APP_TITLE__', escape(t('common.appName')))
        .replace('__STARTUP_FALLBACK__', fallbackContent('errors.pageTitle', 'errors.startupBody'))
        .replace('__JAVASCRIPT_REQUIRED__', escape(t('errors.javascriptRequired')));
    },
    generateBundle() {
      for (const [fileName, title, body] of [
        ['errors/unavailable.html', 'errors.proxyTitle', 'errors.serviceUnavailable'],
        ['errors/not-found.html', 'errors.pageNotFoundTitle', 'errors.pageNotFoundBody'],
        ['errors/request.html', 'errors.pageTitle', 'validation.invalid'],
        ['errors/too-large.html', 'errors.pageTitle', 'errors.tooLarge'],
      ] as const) {
        this.emitFile({ type: 'asset', fileName, source: staticPage(title, body) });
      }
    },
  };
}
