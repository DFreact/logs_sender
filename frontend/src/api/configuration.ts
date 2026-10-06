import { ApiError, requestJson } from './client';
import type { Page } from './identity';
export type Condition = { group: 'AND' | 'OR' | 'NOT'; children: Condition[] } | {
  field: string; operator: string; value?: string | number; selector?: string; case_sensitive?: boolean;
};
export interface Policy { enabled: boolean; inherit: boolean; window_seconds: number; fields: string[]; version: number }
export interface Source { id: string; name: string; description: string; enabled: boolean; version: number; dedup: Policy }
export interface Rule { id: string; name: string; source_id: string; priority: number; enabled: boolean; version: number;
  conditions: Condition; assignments: { severity: string | null; category: string; event_type: string; tags: string[] } }
export interface Version { version: number; author: string; created_at: string; snapshot: Omit<Rule, 'id'> }
export const defaultPolicy = (inherit = false): Policy => ({ enabled: false, inherit, window_seconds: 600, fields: ['sender', 'subject', 'body', 'event_type'], version: 1 });
export const newCondition = (): Condition => ({ field: 'sender', operator: 'contains', value: '', case_sensitive: false });
const bad = () => new ApiError({ key: 'errors.internalWithoutId' });
export function record(value: unknown): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) throw bad(); return value as Record<string, unknown>; }
export function string(value: unknown, max = 2000): string { if (typeof value !== 'string' || value.length > max) throw bad(); return value; }
export function integer(value: unknown): number { if (!Number.isSafeInteger(value) || Number(value) < 0) throw bad(); return Number(value); }
export function bool(value: unknown): boolean { if (typeof value !== 'boolean') throw bad(); return value; }
export function strings(value: unknown, max = 20): string[] { if (!Array.isArray(value) || value.length > max) throw bad(); return value.map((v) => string(v, 120)); }
export function parsePolicy(value: unknown): Policy { const v = record(value); return { enabled: bool(v.enabled), inherit: bool(v.inherit), window_seconds: integer(v.window_seconds), fields: strings(v.fields, 7), version: integer(v.version) }; }
export function parseSource(value: unknown): Source { const v = record(value); return { id: string(v.id, 36), name: string(v.name, 120), description: string(v.description), enabled: bool(v.enabled), version: integer(v.version), dedup: parsePolicy(v.dedup) }; }
export function parseCondition(value: unknown, depth = 0, budget = { count: 0 }): Condition {
  if (++budget.count > 100 || depth > 3) throw bad();
  const v = record(value);
  if ('group' in v) { if (!['AND', 'OR', 'NOT'].includes(String(v.group)) || !Array.isArray(v.children) || v.children.length < 1) throw bad(); return { group: v.group as 'AND' | 'OR' | 'NOT', children: v.children.map((c) => parseCondition(c, depth + 1, budget)) }; }
  return { field: string(v.field, 32), operator: string(v.operator, 32), value: typeof v.value === 'number' ? v.value : v.value === undefined ? undefined : string(v.value, 500), selector: v.selector === undefined ? undefined : string(v.selector, 120), case_sensitive: v.case_sensitive === undefined ? false : bool(v.case_sensitive) };
}
export function parseRule(value: unknown): Rule { const v = record(value), a = record(v.assignments); return { id: string(v.id ?? '', 36), name: string(v.name, 120), source_id: string(v.source_id, 36), priority: integer(v.priority), enabled: bool(v.enabled), version: integer(v.version), conditions: parseCondition(v.conditions), assignments: { severity: a.severity === null ? null : string(a.severity, 16), category: string(a.category, 120), event_type: string(a.event_type, 120), tags: strings(a.tags) } }; }
export function page<T>(value: unknown, parse: (v: unknown) => T): Page<T> { const v = record(value); if (!Array.isArray(v.items) || v.items.length > 100) throw bad(); return { items: v.items.map(parse), total: integer(v.total) }; }
export async function getSources(signal?: AbortSignal) { return page(await requestJson('/api/v1/sources', { signal }), parseSource); }
export async function saveSource(value: Source, csrf: string) { const { id, ...body } = value; return parseSource(await requestJson(`/api/v1/sources${id ? `/${id}` : ''}`, { method: id ? 'PATCH' : 'POST', csrf, body })); }
export async function getPolicy(signal?: AbortSignal) { return parsePolicy(await requestJson('/api/v1/dedup-policy', { signal })); }
export async function savePolicy(body: Policy, csrf: string) { return parsePolicy(await requestJson('/api/v1/dedup-policy', { method: 'PATCH', csrf, body })); }
export async function getRules(signal?: AbortSignal) { return page(await requestJson('/api/v1/identification-rules', { signal }), parseRule); }
export async function saveRule(value: Rule, csrf: string) { const { id, ...body } = value; return parseRule(await requestJson(`/api/v1/identification-rules${id ? `/${id}` : ''}`, { method: id ? 'PATCH' : 'POST', csrf, body })); }
export async function getVersions(id: string, offset: number, signal?: AbortSignal) { return page(await requestJson(`/api/v1/identification-rules/${id}/versions?offset=${offset}`, { signal }), (value): Version => { const v = record(value); return { version: integer(v.version), author: string(v.author, 120), created_at: string(v.created_at, 40), snapshot: parseRule(v.snapshot) }; }); }
