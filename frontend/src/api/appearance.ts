import { ApiError, requestJson } from './client';
import type { TranslationKey } from '../i18n';

export const paletteKeys = { blue: 'appearance.blue', teal: 'appearance.teal', green: 'appearance.green', violet: 'appearance.violet', wine: 'appearance.wine', slate: 'appearance.slate' } satisfies Record<string, TranslationKey>;
export const modeKeys = { light: 'appearance.light', dark: 'appearance.dark' } satisfies Record<string, TranslationKey>;
export interface Appearance { palette: keyof typeof paletteKeys; mode: keyof typeof modeKeys; version: number }
export const defaultAppearance: Appearance = { palette: 'blue', mode: 'light', version: 1 };
export function parseAppearance(value: unknown): Appearance {
  if (!value || typeof value !== 'object' || !('palette' in value) || typeof value.palette !== 'string' || !Object.hasOwn(paletteKeys, value.palette)
    || !('mode' in value) || typeof value.mode !== 'string' || !Object.hasOwn(modeKeys, value.mode)
    || !('version' in value) || !Number.isSafeInteger(value.version) || Number(value.version) < 1) throw new ApiError({ key: 'errors.internalWithoutId' });
  return { palette: value.palette as Appearance['palette'], mode: value.mode as Appearance['mode'], version: Number(value.version) };
}
export async function getAppearance(signal?: AbortSignal) {
  return parseAppearance(await requestJson('/api/v1/settings/appearance', { signal }));
}
export async function saveAppearance(value: Appearance, csrf: string) {
  return parseAppearance(await requestJson('/api/v1/settings/appearance', { method: 'PATCH', body: value, csrf }));
}
