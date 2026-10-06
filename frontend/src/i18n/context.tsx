import { createContext, useContext, useEffect, useMemo, type ReactNode } from 'react';
import { createI18n, validateCatalog } from './core';
import { defaultLocale } from './registry';
import type { LocaleDefinition } from './types';

const defaultTimeZone = import.meta.env.VITE_TIME_ZONE || 'Europe/Moscow';
const I18nContext = createContext(createI18n(defaultLocale, defaultTimeZone, import.meta.env.DEV));

export function I18nProvider({ children, locale = defaultLocale, timeZone = defaultTimeZone }: {
  children: ReactNode;
  locale?: LocaleDefinition;
  timeZone?: string;
}) {
  const value = useMemo(() => {
    validateCatalog(locale.messages, defaultLocale.messages);
    return createI18n(locale, timeZone, import.meta.env.DEV);
  }, [locale, timeZone]);
  useEffect(() => {
    document.documentElement.lang = value.language;
    document.title = value.t('common.appName');
  }, [value]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() { return useContext(I18nContext); }
