import { ApiError, requestJson } from './client';
import { record, string, integer, bool } from './configuration';

export interface ConnectionInfo { rest_path: string; max_bytes: number; per_minute: number; smtp: { host: string; port: number; recipients: string[]; tls_required: boolean } }
export interface IngestKey { id: string; name: string; created_at: string; expires_at: string; status: 'ACTIVE' | 'EXPIRED' | 'REVOKED' }
export const keyStatus = { ACTIVE: 'connections.active', EXPIRED: 'connections.expired', REVOKED: 'connections.revoked' } as const;
const bad = () => new ApiError({ key: 'errors.internalWithoutId' });
export function parseKey(value: unknown): IngestKey {
  const v = record(value);
  if (!['ACTIVE', 'EXPIRED', 'REVOKED'].includes(String(v.status))) throw bad();
  const id = string(v.id, 36), created_at = string(v.created_at, 40), expires_at = string(v.expires_at, 40);
  if (!/^[a-f\d]{8}(-[a-f\d]{4}){3}-[a-f\d]{12}$/.test(id) || !Number.isFinite(Date.parse(created_at)) || !Number.isFinite(Date.parse(expires_at))) throw bad();
  return { id, name: string(v.name, 120), created_at, expires_at, status: v.status as IngestKey['status'] };
}
export async function getConnections(signal?: AbortSignal): Promise<ConnectionInfo> {
  const v = record(await requestJson('/api/v1/connections', { signal })), smtp = record(v.smtp);
  if (v.rest_path !== '/api/v1/ingest' || !Array.isArray(smtp.recipients) || smtp.recipients.length > 100) throw bad();
  return { rest_path: v.rest_path, max_bytes: integer(v.max_bytes), per_minute: integer(v.per_minute), smtp: { host: string(smtp.host, 253), port: integer(smtp.port), recipients: smtp.recipients.map((s) => string(s, 320)), tls_required: bool(smtp.tls_required) } };
}
export async function getIngestKeys(offset: number, signal?: AbortSignal) {
  const v = record(await requestJson(`/api/v1/connections/keys?offset=${offset}`, { signal }));
  if (!Array.isArray(v.items) || v.items.length > 25) throw bad();
  return { items: v.items.map(parseKey), has_more: bool(v.has_more) };
}
export async function createIngestKey(name: string, csrf: string) {
  const v = record(await requestJson('/api/v1/connections/keys', { method: 'POST', body: { name }, csrf }));
  const token = string(v.token, 128);
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) throw bad();
  return { key: parseKey(v.key), token };
}
export async function revokeIngestKey(id: string, csrf: string) {
  return parseKey(await requestJson(`/api/v1/connections/keys/${id}/revoke`, { method: 'POST', csrf }));
}
