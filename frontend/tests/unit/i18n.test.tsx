import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { createI18n, validateCatalog } from '../../src/i18n/core';
import { I18nProvider, useI18n } from '../../src/i18n';
import { defaultLocale, locales } from '../../src/i18n/registry';
import { labelKey, eventStatuses, notificationStatuses, errorKeys, severities,
  deliveryModes, healthStatuses, attemptStatuses, userRoles, auditActions, workStates, componentCodes, healthReasons, deliveryReasons, escalationStates, escalationStepStates, digestStates, digestPeriods, digestSelections } from '../../src/i18n/mappings';
import { EventStatusValues, NotificationStatusValues, ErrorCodeValues, SeverityValues,
  DeliveryModeValues, HealthStatusValues, AttemptStatusValues, UserRoleValues, AuditActionValues, WorkStateValues, ComponentCodeValues, HealthReasonValues, DeliveryReasonValues, EscalationStateValues, EscalationStepStateValues, DigestStateValues, DigestPeriodValues, DigestSelectionValues } from '../../src/api/generated';
import type { Catalog, TranslationKey } from '../../src/i18n/types';

const i18n = createI18n(defaultLocale, 'Europe/Moscow');

describe('Russian locale contract', () => {
  it.each([[0, '0 событий'], [1, '1 событие'], [2, '2 события'], [5, '5 событий'],
    [11, '11 событий'], [21, '21 событие'], [22, '22 события'], [1.5, '1,5 события']])(
    'formats %s with a full phrase', (count, expected) => {
      expect(i18n.formatCount('events.count', Number(count))).toBe(expected);
    });

  it('uses explicit timezone and Russian number/date formatting', () => {
    expect(i18n.formatDate('2026-09-29T05:00:00Z')).toContain('08:00:00');
    expect(i18n.formatDate('2026-09-29T05:00:00Z')).toContain('29.09.2026');
    expect(i18n.formatNumber(1234.5)).toBe('1\u00a0234,5');
    expect(Object.keys(locales)).toEqual(['ru']);
  });

  it('maps every backend enum and error code to a Russian string', () => {
    for (const [values, map] of [
      [DigestStateValues, digestStates], [DigestPeriodValues, digestPeriods], [DigestSelectionValues, digestSelections],
      [EventStatusValues, eventStatuses], [NotificationStatusValues, notificationStatuses],
      [ErrorCodeValues, errorKeys], [SeverityValues, severities], [DeliveryModeValues, deliveryModes],
      [HealthStatusValues, healthStatuses], [AttemptStatusValues, attemptStatuses], [UserRoleValues, userRoles], [AuditActionValues, auditActions], [WorkStateValues, workStates],
      [EscalationStateValues, escalationStates], [EscalationStepStateValues, escalationStepStates], [DeliveryReasonValues, deliveryReasons], [ComponentCodeValues, componentCodes], [HealthReasonValues, healthReasons],
    ] as const) {
      expect(Object.keys(map).sort()).toEqual([...values].sort());
      for (const value of values) expect(i18n.t(labelKey(map, value))).toMatch(/[А-Яа-яЁё]/);
    }
    expect(i18n.t(eventStatuses.ACKNOWLEDGED)).toBe('Подтверждено');
    expect(i18n.t(notificationStatuses.PENDING)).toBe('Ожидает отправки');
  });

  it.each(['UNEXPECTED_STATUS', '__proto__', 'constructor', null, { value: 'NEW' }])(
    'does not expose unexpected API value %s', (value) => {
      expect(i18n.t(labelKey(eventStatuses, value))).toBe('Статус неизвестен');
    });

  it('fails validation for missing keys and mismatched interpolation', () => {
    expect(() => validateCatalog({} as Catalog, defaultLocale.messages)).toThrow();
    expect(() => validateCatalog({ ...defaultLocale.messages, 'common.requestId': 'Обращение' }, defaultLocale.messages)).toThrow();
    expect(() => i18n.t('missing' as TranslationKey)).toThrow('MISSING_TRANSLATION');
    expect(() => i18n.t('common.requestId')).toThrow('MISSING_TRANSLATION_PARAMETER');
    expect(createI18n(defaultLocale, 'UTC', false).t('missing' as TranslationKey)).toMatch(/Обновите/);
  });

  it('renders the same component with another locale without modifying it', () => {
    function Heading() { const { t } = useI18n(); return <h1>{t('overview.title')}</h1>; }
    const translated = { ...defaultLocale, language: 'fr', intlLocale: 'fr-FR',
      messages: { ...defaultLocale.messages, 'overview.title': 'Aperçu' } };
    expect(renderToStaticMarkup(<I18nProvider><Heading /></I18nProvider>)).toContain('Обзор');
    expect(renderToStaticMarkup(<I18nProvider locale={translated}><Heading /></I18nProvider>)).toContain('Aperçu');
  });
});
