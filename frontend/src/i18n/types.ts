import type ru from './locales/ru/messages.json';

export type TranslationKey = keyof typeof ru;
export type Catalog = { readonly [K in TranslationKey]: string };
export type TranslationParams = Readonly<Record<string, string | number>>;
export type PluralKey = {
  [K in TranslationKey]: K extends `${infer Base}.one` ? Base : never;
}[TranslationKey];

export interface LocaleDefinition {
  readonly language: string;
  readonly intlLocale: string;
  readonly messages: Catalog;
}
