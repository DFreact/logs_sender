import { AppearanceProvider } from './features/settings/AppearanceContext';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './app/App';
import { ErrorBoundary } from './app/ErrorBoundary';
import { I18nProvider } from './i18n';
import './styles.css';
import { AuthProvider } from './features/auth/AuthContext';

const container = document.getElementById('root')!;
const startupFallback = container.firstElementChild?.cloneNode(true);
createRoot(container, {
  // React's default reporting can print raw exception values. The boundary provides
  // a safe message; bootstrap failure restores the translated build-time fallback.
  onCaughtError: () => {},
  onUncaughtError: () => { if (startupFallback) container.replaceChildren(startupFallback); },
}).render(
  <StrictMode><I18nProvider><ErrorBoundary><AppearanceProvider><AuthProvider><App /></AuthProvider></AppearanceProvider></ErrorBoundary></I18nProvider></StrictMode>,
);
