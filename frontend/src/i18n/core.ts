import type { Catalog, LocaleDefinition, PluralKey, TranslationKey, TranslationParams } from './types.ts';

const placeholders = (value: string) => [...value.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(',');

export function validateCatalog(catalog: Catalog, reference: Catalog): void {
  for (const key of Object.keys(reference) as TranslationKey[]) {
    if (!Object.hasOwn(catalog, key) || !catalog[key]?.trim()
      || placeholders(catalog[key]) !== placeholders(reference[key])) {
      throw new Error(`INVALID_TRANSLATION:${key}`);
    }
  }
  if (Object.keys(catalog).length !== Object.keys(reference).length) {
    throw new Error('UNEXPECTED_TRANSLATION_KEY');
  }
}

export function createI18n(locale: LocaleDefinition, timeZone: string, strict = true) {
  const number = new Intl.NumberFormat(locale.intlLocale);
  const plural = new Intl.PluralRules(locale.intlLocale);
  const date = new Intl.DateTimeFormat(locale.intlLocale, {
    dateStyle: 'short', timeStyle: 'medium', timeZone,
  });
  function t(key: TranslationKey, params: TranslationParams = {}): string {
    const value = Object.hasOwn(locale.messages, key) ? locale.messages[key] : undefined;
    if (typeof value !== 'string') {
      if (strict) throw new Error('MISSING_TRANSLATION');
      return locale.messages['common.unavailableText'];
    }
    let invalid = false;
    const result = value.replace(/\{(\w+)\}/g, (_, name: string) => {
      if (!Object.hasOwn(params, name)) { invalid = true; return ''; }
      return String(params[name]);
    });
    if (invalid) {
      if (strict) throw new Error('MISSING_TRANSLATION_PARAMETER');
      return locale.messages['common.unavailableText'];
    }
    return result;
  }
  return {
    t,
    language: locale.language,
    timeZone,
    formatDate: (value: Date | string) => date.format(new Date(value)),
    formatNumber: (value: number) => number.format(value),
    formatTimeZone: (value: Date | string) => new Intl.DateTimeFormat(locale.intlLocale, {
      timeZone, timeZoneName: 'long',
    }).formatToParts(new Date(value)).find((part) => part.type === 'timeZoneName')?.value
      ?? t('common.unknownValue'),
    formatCount: (key: PluralKey, count: number) => {
      const candidate = `${key}.${plural.select(count)}`;
      const resolved = Object.hasOwn(locale.messages, candidate) ? candidate : `${key}.other`;
      return t(resolved as TranslationKey, { count: number.format(count) });
    },
  };
}

export type I18n = ReturnType<typeof createI18n>;
