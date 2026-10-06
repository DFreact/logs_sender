import ru from './locales/ru/messages.json' with { type: 'json' };
import type { LocaleDefinition } from './types.ts';

export const locales = {
  ru: { language: 'ru', intlLocale: 'ru-RU', messages: ru },
} satisfies Record<string, LocaleDefinition>;

export const defaultLocale = locales.ru;
