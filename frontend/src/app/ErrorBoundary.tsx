import { Component, type ReactNode } from 'react';
import { useI18n } from '../i18n';

function ErrorPage() {
  const { t } = useI18n();
  return <main className="fatal-page" role="alert">
    <h1>{t('errors.pageTitle')}</h1>
    <p>{t('errors.pageBody')}</p>
    <button onClick={() => window.location.reload()}>{t('common.refreshPage')}</button>
  </main>;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <ErrorPage /> : this.props.children; }
}
