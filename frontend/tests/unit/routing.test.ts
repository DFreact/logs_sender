import { expect, it } from 'vitest';
import { parseAction, parseRouting, parseSimulation } from '../../src/api/routing';
import { RuleActionValues, ActionOutcomeValues } from '../../src/api/generated';
import { actionKeys, outcomeKeys } from '../../src/features/configuration/RoutingFields';
import { createI18n } from '../../src/i18n/core';
import { defaultLocale } from '../../src/i18n/registry';

it('maps every routing action and outcome to centralized Russian text', () => {
  const { t } = createI18n(defaultLocale, 'Europe/Moscow');
  expect(Object.keys(actionKeys).sort()).toEqual([...RuleActionValues].sort());
  expect(Object.keys(outcomeKeys).sort()).toEqual([...ActionOutcomeValues].sort());
  for (const key of [...Object.values(actionKeys), ...Object.values(outcomeKeys)]) expect(t(key)).toMatch(/[А-Яа-яЁё]/);
});

it('rejects unknown actions, schedules, and malformed server results', () => {
  expect(() => parseAction({ kind: 'SQL_TRACE', scope: 'EVENT' })).toThrow();
  expect(() => parseAction({ kind: 'NOTIFY', scope: 'EVENT', mode: 'SCHEDULED', scheduled_at: 'not-a-date' })).toThrow();
  expect(() => parseSimulation({ detail: 'SECRET' })).toThrow();
  expect(() => parseRouting({ id: '../outside', actions: [{}] })).toThrow();
});

it('keeps unchanged fields distinct from explicitly cleared fields', () => {
  expect(parseAction({ kind: 'SET_FIELDS', scope: 'EVENT', fields: { severity: null, category: '', event_type: null, tags: ['проверка'] } }).fields).toEqual({ severity: null, category: '', event_type: null, tags: ['проверка'] });
});
