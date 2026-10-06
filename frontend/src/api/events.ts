import { ApiError, requestJson } from './client';
export interface EventPage { items: EventItem[]; next_cursor: string | null; as_of: string }

export interface EventItem {
  id: string; subject: string; sender: string; status: string; severity: string | null;
  category: string; event_type: string; tags: string[];
  source_name: string | null; first_seen_at: string; last_seen_at: string;
  input_adapter: string; received_at: string; occurrence_count: number;
}
export interface Occurrence {
  id: string; raw_message_id: string; raw_available: boolean; received_at: string; limited: boolean;
  rule_name: string | null; rule_version: number | null; snapshot: { subject?: string; body?: string };
  envelope_sender: string; recipients: string[];
  attachments: { id: string; filename: string; size: number }[];
}
export interface EventDetail extends EventItem { body: string; occurrences: Occurrence[] }
const invalid = () => new ApiError({ key: 'errors.internalWithoutId' });
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid();
  return value as Record<string, unknown>;
}
function text(value: unknown, max = 65536): string {
  if (typeof value !== 'string' || value.length > max) throw invalid();
  return value;
}
function id(value: unknown) {
  const result = text(value, 36);
  if (!/^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/.test(result)) throw invalid();
  return result;
}
function date(value: unknown) {
  const result = text(value, 40);
  if (!Number.isFinite(Date.parse(result))) throw invalid();
  return result;
}
function number(value: unknown) {
  if (!Number.isSafeInteger(value) || Number(value) < 0) throw invalid();
  return Number(value);
}
export function parseEvent(value: unknown): EventItem {
  const row = record(value);
  if (row.tags != null && (!Array.isArray(row.tags) || row.tags.length > 10000 || !row.tags.every((v) => typeof v === 'string'))) throw invalid();
  return { category: text(row.category ?? '', 120), event_type: text(row.event_type ?? '', 120), tags: (row.tags ?? []) as string[], id: id(row.id), subject: text(row.subject, 500), sender: text(row.sender, 320),
    source_name: row.source_name == null ? null : text(row.source_name, 120), first_seen_at: date(row.first_seen_at ?? row.received_at), last_seen_at: date(row.last_seen_at ?? row.received_at),
    status: text(row.status, 32), severity: row.severity === null ? null : text(row.severity, 32),
    input_adapter: text(row.input_adapter, 32), received_at: date(row.received_at), occurrence_count: number(row.occurrence_count) };
}
export async function getEvents(query: URLSearchParams, signal?: AbortSignal): Promise<EventPage> {
  const page = record(await requestJson(`/api/v1/events?${query}`, { signal }));
  if (!Array.isArray(page.items) || page.items.length > 100) throw invalid();
  return { items: page.items.map(parseEvent), next_cursor: page.next_cursor === null ? null : text(page.next_cursor, 1024), as_of: date(page.as_of) };
}
export function parseEventDetail(value: unknown): EventDetail {
  const row = record(value);
  if (!Array.isArray(row.occurrences) || row.occurrences.length > 100) throw invalid();
  const occurrences = row.occurrences.map((value) => {
    const item = record(value);
    if (typeof item.limited !== 'boolean' || !Array.isArray(item.recipients) || item.recipients.length > 100
      || !Array.isArray(item.attachments) || item.attachments.length > 100) throw invalid();
    return { id: id(item.id), raw_message_id: id(item.raw_message_id), received_at: date(item.received_at),
      raw_available: item.raw_available !== false, limited: item.limited, rule_name: item.rule_name == null ? null : text(item.rule_name, 120), rule_version: item.rule_version == null ? null : number(item.rule_version), snapshot: { subject: item.snapshot ? text(record(item.snapshot).subject ?? '', 500) : '', body: item.snapshot ? text(record(item.snapshot).body ?? '') : '' }, envelope_sender: text(item.envelope_sender, 320), recipients: item.recipients.map((v) => text(v, 320)),
      attachments: item.attachments.map((value) => { const a = record(value); return { id: id(a.id), filename: text(a.filename, 200), size: number(a.size) }; }) };
  });
  return { ...parseEvent(row), body: text(row.body), occurrences };
}
export async function getEvent(identifier: string, signal?: AbortSignal, offset = 0) {
  return parseEventDetail(await requestJson(`/api/v1/events/${encodeURIComponent(identifier)}?occurrence_offset=${offset}`, { signal }));
}
