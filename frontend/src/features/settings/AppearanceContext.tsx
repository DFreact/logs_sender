import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { defaultAppearance, getAppearance, type Appearance } from '../../api/appearance';
import { safeFailure, type SafeError } from '../../api/client';

interface AppearanceContextValue { value: Appearance; loading: boolean; error?: SafeError; refresh: () => Promise<void>; accept: (value: Appearance) => void }
const Context = createContext<AppearanceContextValue | undefined>(undefined);
export function AppearanceProvider({ children }: { children: ReactNode }) {
  const [value, setValue] = useState(defaultAppearance), [loading, setLoading] = useState(true), [error, setError] = useState<SafeError>();
  const sequence = useRef(0), controller = useRef<AbortController | undefined>(undefined);
  const accept = useCallback((next: Appearance) => { sequence.current += 1; controller.current?.abort(); setValue(next); setError(undefined); setLoading(false); }, []);
  const refresh = useCallback(async () => {
    const turn = ++sequence.current; controller.current?.abort(); const c = new AbortController(); controller.current = c;
    try { const next = await getAppearance(c.signal); if (turn === sequence.current && !c.signal.aborted) { setValue(next); setError(undefined); } }
    catch (err) { if (turn === sequence.current && !c.signal.aborted) setError(safeFailure(err)); }
    finally { if (turn === sequence.current && !c.signal.aborted) setLoading(false); }
  }, []);
  useEffect(() => {
    void refresh(); const update = () => { if (document.visibilityState === 'visible') void refresh(); };
    window.addEventListener('hashchange', update); window.addEventListener('focus', update); document.addEventListener('visibilitychange', update);
    return () => { sequence.current += 1; controller.current?.abort(); window.removeEventListener('hashchange', update); window.removeEventListener('focus', update); document.removeEventListener('visibilitychange', update); };
  }, [refresh]);
  useEffect(() => { document.documentElement.dataset.palette = value.palette; document.documentElement.dataset.mode = value.mode; }, [value]);
  return <Context.Provider value={{ value, loading, error, refresh, accept }}>{children}</Context.Provider>;
}
export function useAppearance() { const value = useContext(Context); if (!value) throw new Error('APPEARANCE_PROVIDER_REQUIRED'); return value; }
