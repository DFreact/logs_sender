import { ApiError, requestJson } from './client';
import { record, string, integer, bool, strings, page, parseCondition, type Condition } from './configuration';
import { RuleActionValues, ActionOutcomeValues, type RuleAction, type ActionOutcome } from './generated';
export interface Fields { severity: string | null; category: string | null; event_type: string | null; tags: string[] }
export interface Action { kind: RuleAction; scope: 'EVENT' | 'OCCURRENCE'; fields?: Fields; mode?: 'IMMEDIATE' | 'DELAYED' | 'SCHEDULED'; delay_seconds?: number | null; scheduled_at?: string | null; target_id?: string | null }
export interface RoutingRule { id: string; name: string; description: string; priority: number; enabled: boolean; version: number; conditions: Condition; actions: Action[] }
export interface RoutingVersion { version: number; author: string; created_at: string; snapshot: RoutingRule }
export interface Decision { rule_id: string; rule_name: string; version: number; action_index: number; action: Action; outcome: ActionOutcome; applied: Partial<Fields>; overridden: string[] }
export interface Simulation { checked_at: string; manual_source: boolean; source_name: string | null; identification: { name: string; version: number } | null; deduplication: { status: string; event_id: string | null; window_seconds: number | null }; retention: { status: string; event_days: number | null; raw_days: number | null }; result: Fields; status: string; decisions: Decision[] }
export interface SimulationInput { adapter: 'SMTP' | 'REST'; source_id: string | null; sender: string; envelope_sender: string; subject: string; body: string; recipients: string[]; severity: string | null; headers: { name: string; value: string }[]; metadata: { name: string; value: string | number | boolean | null }[] }
const bad = () => new ApiError({ key: 'errors.internalWithoutId' });
export const emptyFields = (): Fields => ({ severity: null, category: null, event_type: null, tags: [] });
export function newAction(kind: RuleAction): Action { return { kind, scope: 'EVENT', ...(kind === 'SET_FIELDS' ? { fields: emptyFields() } : kind === 'NOTIFY' ? { mode: 'IMMEDIATE' as const, target_id: null } : {}) }; }
function date(value: unknown) { const s = string(value, 40); if (!Number.isFinite(Date.parse(s))) throw bad(); return s; }
function identifier(value: unknown) { const s = string(value, 36); if (!/^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/.test(s)) throw bad(); return s; }
function fields(value: unknown): Fields { const v = record(value); return { severity: v.severity == null ? null : string(v.severity, 16), category: v.category == null ? null : string(v.category, 120), event_type: v.event_type == null ? null : string(v.event_type, 120), tags: strings(v.tags ?? [], 10000) }; }
export function parseAction(value: unknown): Action {
  const v = record(value); if (!RuleActionValues.includes(v.kind as RuleAction) || !['EVENT', 'OCCURRENCE'].includes(String(v.scope))) throw bad();
  const result: Action = { kind: v.kind as RuleAction, scope: v.scope as Action['scope'] };
  if (['NOTIFY', 'DIGEST', 'ESCALATE'].includes(result.kind)) result.target_id = v.target_id == null ? null : identifier(v.target_id);
  if (result.kind === 'SET_FIELDS') result.fields = fields(v.fields);
  if (result.kind === 'NOTIFY') { if (!['IMMEDIATE', 'DELAYED', 'SCHEDULED'].includes(String(v.mode))) throw bad(); result.mode = v.mode as Action['mode']; result.delay_seconds = v.delay_seconds == null ? null : integer(v.delay_seconds); result.scheduled_at = v.scheduled_at == null ? null : date(v.scheduled_at); }
  return result;
}
export function parseRouting(value: unknown): RoutingRule { const v = record(value); if (!Array.isArray(v.actions) || !v.actions.length || v.actions.length > 10) throw bad(); return { id: v.id == null ? '' : identifier(v.id), name: string(v.name, 120), description: string(v.description), priority: integer(v.priority), enabled: bool(v.enabled), version: integer(v.version), conditions: parseCondition(v.conditions), actions: v.actions.map(parseAction) }; }
export function parseDecision(value: unknown): Decision {
  const v = record(value); if (!ActionOutcomeValues.includes(v.outcome as ActionOutcome)) throw bad(); const applied = record(v.applied);
  const normalized = fields(applied), partial: Partial<Fields> = {};
  for (const field of ['severity', 'category', 'event_type', 'tags'] as const) if (field in applied) Object.assign(partial, { [field]: normalized[field] });
  return { rule_id: identifier(v.rule_id), rule_name: string(v.rule_name, 120), version: integer(v.version), action_index: integer(v.action_index), action: parseAction(v.action), outcome: v.outcome as ActionOutcome, applied: partial, overridden: strings(v.overridden, 4) };
}
export function parseSimulation(value: unknown): Simulation { const v = record(value), d = record(v.deduplication); if (!Array.isArray(v.decisions) || v.decisions.length > 1000) throw bad(); const i = v.identification == null ? null : record(v.identification); return { checked_at: date(v.checked_at), manual_source: bool(v.manual_source), source_name: v.source_name == null ? null : string(v.source_name, 120), identification: i ? { name: string(i.name, 120), version: integer(i.version) } : null, deduplication: { status: string(d.status, 32), event_id: d.event_id == null ? null : identifier(d.event_id), window_seconds: d.window_seconds == null ? null : integer(d.window_seconds) }, retention: { status: string(record(v.retention).status, 32), event_days: record(v.retention).event_days == null ? null : integer(record(v.retention).event_days), raw_days: record(v.retention).raw_days == null ? null : integer(record(v.retention).raw_days) }, result: fields(v.result), status: string(v.status, 32), decisions: v.decisions.map(parseDecision) }; }
export async function getRouting(signal?: AbortSignal) { return page(await requestJson('/api/v1/routing-rules', { signal }), parseRouting); }
export async function saveRouting(value: RoutingRule, csrf: string) { const { id, ...body } = value; return parseRouting(await requestJson(`/api/v1/routing-rules${id ? `/${id}` : ''}`, { method: id ? 'PATCH' : 'POST', csrf, body })); }
export async function getRoutingVersions(id: string, offset: number, signal?: AbortSignal) { return page(await requestJson(`/api/v1/routing-rules/${id}/versions?offset=${offset}`, { signal }), (value): RoutingVersion => { const v = record(value); return { version: integer(v.version), author: string(v.author, 120), created_at: date(v.created_at), snapshot: parseRouting(v.snapshot) }; }); }
export async function simulate(body: SimulationInput, csrf: string, signal?: AbortSignal) { return parseSimulation(await requestJson('/api/v1/rules/simulate', { method: 'POST', csrf, body, signal })); }
export async function getProcessing(id: string, offset: number, signal?: AbortSignal) { return page(await requestJson(`/api/v1/events/${encodeURIComponent(id)}/processing?offset=${offset}`, { signal }), (v) => { const row = record(v); return { id: identifier(row.id), created_at: date(row.created_at), decision: parseDecision(row.decision) }; }); }
