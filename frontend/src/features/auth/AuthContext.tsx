import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { ApiError, safeFailure, SESSION_EXPIRED_EVENT, type SafeError } from '../../api/client';
import { currentIdentity, signIn, signOut, type Identity } from '../../api/identity';
import type { TranslationKey } from '../../i18n';

interface Auth {
  identity: Identity | null; loading: boolean; error?: SafeError; notice?: TranslationKey;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>; refresh: () => Promise<void>;
  clear: (notice?: TranslationKey) => void;
}
const Context = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<SafeError>();
  const [notice, setNotice] = useState<TranslationKey>();
  const generation = useRef(0);
  function clear(message?: TranslationKey) { generation.current++; setIdentity(null); setNotice(message); setError(undefined); }
  async function refresh() {
    const current = generation.current;
    try { const value = await currentIdentity(); if (current === generation.current) { setIdentity(value); setError(undefined); } }
    catch (err) { if (current !== generation.current) return; if (err instanceof ApiError && err.status === 401) clear('errors.sessionExpired'); else setError(safeFailure(err)); }
  }
  useEffect(() => {
    const controller = new AbortController();
    currentIdentity(controller.signal).then((value) => { if (!controller.signal.aborted) setIdentity(value); })
      .catch((err) => { if (!controller.signal.aborted && !(err instanceof ApiError && err.status === 401)) setError(safeFailure(err)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    const expired = () => { if (identity) clear('errors.sessionExpired'); };
    window.addEventListener(SESSION_EXPIRED_EVENT, expired);
    const timer = identity ? window.setInterval(() => void refresh(), 60000) : undefined;
    return () => { window.removeEventListener(SESSION_EXPIRED_EVENT, expired); if (timer) window.clearInterval(timer); };
  }, [identity]);
  return <Context.Provider value={{ identity, loading, error, notice, clear, refresh,
    login: async (username, password) => { const value = await signIn(username, password); generation.current++; setIdentity(value); setNotice(undefined); setError(undefined); },
    logout: async () => { if (identity) await signOut(identity.csrf_token); clear('auth.signedOut'); },
  }}>{children}</Context.Provider>;
}
export function useAuth() {
  const value = useContext(Context);
  if (!value) throw new Error('AUTH_CONTEXT_REQUIRED');
  return value;
}
