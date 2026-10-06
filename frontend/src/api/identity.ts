import { ApiError, requestJson } from './client';
import { PermissionValues, UserRoleValues, type Permission, type UserRole } from './generated';

export interface User {
  id: string; username: string; display_name: string; role: UserRole;
  active: boolean; version: number; created_at: string;
}
export interface Identity { user: User; permissions: Permission[]; csrf_token: string; expires_at: string }
export interface AuditItem {
  id: string; timestamp: string; actor: string | null; target: string | null; action: string;
  before: Record<string, unknown>; after: Record<string, unknown>; request_ip: string | null;
}
export interface Page<T> { items: T[]; total: number }

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid();
  return value as Record<string, unknown>;
}
function invalid() { return new ApiError({ key: 'errors.internalWithoutId' }); }
function text(value: unknown, limit = 120): string {
  if (typeof value !== 'string' || value.length > limit) throw invalid();
  return value;
}
function timestamp(value: unknown) {
  const string = text(value, 64);
  if (!/^\d{4}-\d{2}-\d{2}T/.test(string) || !Number.isFinite(Date.parse(string))) throw invalid();
  return string;
}
export function parseUser(value: unknown): User {
  const user = record(value);
  if (!UserRoleValues.includes(user.role as UserRole) || typeof user.active !== 'boolean'
    || !Number.isInteger(user.version) || Number(user.version) < 1) throw invalid();
  return { id: text(user.id, 36), username: text(user.username, 64), display_name: text(user.display_name),
    role: user.role as UserRole, active: user.active, version: Number(user.version), created_at: timestamp(user.created_at) };
}
function parseIdentity(value: unknown): Identity {
  const data = record(value);
  if (!Array.isArray(data.permissions) || !data.permissions.every((p) => PermissionValues.includes(p))) throw invalid();
  const token = text(data.csrf_token, 64);
  if (!/^[a-f0-9]{64}$/.test(token)) throw invalid();
  return { user: parseUser(data.user), permissions: data.permissions, csrf_token: token, expires_at: timestamp(data.expires_at) };
}
function parsePage<T>(value: unknown, parse: (item: unknown) => T): Page<T> {
  const page = record(value);
  if (!Array.isArray(page.items) || page.items.length > 100 || !Number.isInteger(page.total) || Number(page.total) < 0) throw invalid();
  return { items: page.items.map(parse), total: Number(page.total) };
}

export async function currentIdentity(signal?: AbortSignal) {
  return parseIdentity(await requestJson('/api/v1/auth/me', { signal }));
}
export async function signIn(username: string, password: string) {
  const preauth = record(await requestJson('/api/v1/auth/csrf'));
  const csrf = text(preauth.csrf_token, 64);
  return parseIdentity(await requestJson('/api/v1/auth/login', { method: 'POST', csrf, body: { username, password } }));
}
export async function signOut(csrf: string) { await requestJson('/api/v1/auth/logout', { method: 'POST', csrf }); }
export async function changePassword(csrf: string, current_password: string, new_password: string) {
  await requestJson('/api/v1/auth/password', { method: 'POST', csrf, body: { current_password, new_password } });
}
export async function getUsers(offset = 0, signal?: AbortSignal) {
  return parsePage(await requestJson(`/api/v1/users?limit=25&offset=${offset}`, { signal }), parseUser);
}
export async function saveUser(csrf: string, body: unknown, id?: string) {
  return parseUser(await requestJson(id ? `/api/v1/users/${encodeURIComponent(id)}` : '/api/v1/users', {
    method: id ? 'PATCH' : 'POST', body, csrf,
  }));
}
export async function getAudit(offset = 0, action = '', signal?: AbortSignal): Promise<Page<AuditItem>> {
  return parsePage(await requestJson(`/api/v1/audit?limit=25&offset=${offset}${action ? `&action=${encodeURIComponent(action)}` : ''}`, { signal }), (value) => {
    const data = record(value);
    return { id: text(data.id, 36), timestamp: timestamp(data.timestamp),
      actor: data.actor === null ? null : text(data.actor), target: data.target === null ? null : text(data.target, 500),
      action: text(data.action, 40), before: record(data.before), after: record(data.after),
      request_ip: data.request_ip === null ? null : text(data.request_ip, 45) };
  });
}
