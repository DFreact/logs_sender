import { expect, it } from 'vitest';
import { parseCondition, parsePolicy } from '../../src/api/configuration';
import { conditionFields, operators, dedupFields } from '../../src/features/configuration/Editors';

it('configuration boundary rejects deep trees and invalid policy shapes', () => {
  let tree: unknown = { field: 'sender', operator: 'contains', value: 'x' };
  for (let i = 0; i < 5; i++) tree = { group: 'AND', children: [tree] };
  expect(() => parseCondition(tree)).toThrow();
  expect(() => parsePolicy({ enabled: 'true' })).toThrow();
});
it('condition field and operator labels are centralized', () => {
  expect(Object.keys(conditionFields)).toHaveLength(7);
  expect(Object.keys(operators)).toHaveLength(12);
  expect(Object.keys(dedupFields)).toHaveLength(7);
  for (const key of [...Object.values(conditionFields), ...Object.values(operators), ...Object.values(dedupFields)]) expect(key).toMatch(/^(config|events|sources)\./);
});
