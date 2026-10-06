import { ConnectionsPage } from '../features/connections/ConnectionsPage';
import { OverviewCounts } from './OverviewCounts';
import { SettingsPage } from '../features/settings/SettingsPage';
import { useAppearance } from '../features/settings/AppearanceContext';
import type { TranslationKey } from '../i18n';
import { DigestsPage } from '../features/digests/DigestsPage';
import { PoliciesPage } from '../features/escalations/PoliciesPage';
import { useI18n } from '../i18n';
import { NotificationPage } from '../features/notifications/NotificationPage';
import { NotificationsPage } from '../features/notifications/NotificationsPage';
import { ChannelsPage } from '../features/channels/ChannelsPage';
import { TemplatesPage } from '../features/templates/TemplatesPage';
import { ConnectionCard } from './ConnectionCard';
import { useEffect, useState } from 'react';
import { useAuth } from '../features/auth/AuthContext';
import { LoginPage } from '../features/auth/LoginPage';
import { PasswordPage } from '../features/auth/PasswordPage';
import { UsersPage } from '../features/users/UsersPage';
import { SourcesPage } from '../features/configuration/SourcesPage';
import { RoutingPage } from '../features/configuration/RoutingPage';
import { SimulationPage } from '../features/configuration/SimulationPage';
import { RulesPage } from '../features/configuration/RulesPage';
import { EventsPage } from '../features/events/EventsPage';
import { EventPage } from '../features/events/EventPage';
import { HealthPage } from '../features/health/HealthPage';
import { AuditPage } from '../features/audit/AuditPage';
import { ErrorNotice } from '../components/Forms';
import { safeFailure, type SafeError } from '../api/client';
import { userRoles } from '../i18n/mappings';

export function App() {
  const { t } = useI18n();
  const auth = useAuth();
  const appearance = useAppearance();
  const [menuOpen, setMenuOpen] = useState(false);
  const [page, setPage] = useState(location.hash.slice(1) || 'overview');
  const [error, setError] = useState<SafeError>();
  const [leaving, setLeaving] = useState(false);
  useEffect(() => {
    const navigate = () => { setPage(location.hash.slice(1) || 'overview'); setError(undefined); setMenuOpen(false); document.getElementById('content')?.focus(); };
    window.addEventListener('hashchange', navigate);
    return () => window.removeEventListener('hashchange', navigate);
  }, []);
  if (auth.loading) return <main className="fatal-page" role="status">{t('common.loading')}</main>;
  if (!auth.identity) return <LoginPage />;
  const administrator = auth.identity.permissions.includes('MANAGE_USERS');
  const canHealth = auth.identity.permissions.includes('VIEW_SYSTEM_HEALTH');
  const canAudit = auth.identity.permissions.includes('VIEW_AUDIT');
  async function logout() {
    setLeaving(true); setError(undefined);
    try { await auth.logout(); } catch (err) { setError(safeFailure(err)); }
    finally { setLeaving(false); }
  }
  return <div className="workspace">
    <a className="skip-link" href="#content" onClick={(event) => { event.preventDefault(); document.getElementById('content')?.focus(); }}>{t('common.skipToContent')}</a>
    <aside className="sidebar">
      <a className="brand" href="/">
        <svg viewBox="0 0 32 32" width="36" height="36" aria-hidden="true">
          <rect x="1" y="1" width="30" height="30" rx="9" fill="currentColor" />
          <path d="M9 10h14M9 16h9M9 22h14" stroke="white" strokeWidth="2.4" strokeLinecap="round" />
        </svg>
        <span>{t('common.appShortName')}</span>
      </a>
      <button className="navigation-toggle secondary" aria-expanded={menuOpen} aria-controls="main-navigation" onClick={() => setMenuOpen(!menuOpen)}>{t(menuOpen ? 'common.closeNavigation' : 'common.openNavigation')}</button>
      <nav id="main-navigation" className={menuOpen ? 'is-open' : ''} aria-label={t('common.navigation')}>
        {([
          { title: 'navigation.monitoring', items: [
            ['overview', 'navigation.overview', true], ['events', 'navigation.events', true],
            ['notifications', 'navigation.notifications', true], ['dead-letters', 'navigation.deadLetters', true], ['digests', 'digests.title', true],
          ] },
          { title: 'navigation.configuration', items: [
            ['sources', 'navigation.sources', administrator], ['connections', 'connections.title', administrator], ['identification', 'rules.identification', administrator],
            ['routing', 'routing.title', administrator], ['escalations', 'escalation.title', administrator],
            ['simulation', 'routing.test', administrator], ['channels', 'navigation.channels', administrator], ['templates', 'navigation.templates', administrator],
          ] },
          { title: 'navigation.administration', items: [
            ['users', 'navigation.users', administrator], ['audit', 'navigation.audit', canAudit],
            ['health', 'navigation.health', canHealth], ['settings', 'navigation.settings', administrator],
          ] },
        ] satisfies { title: TranslationKey; items: [string, TranslationKey, boolean][] }[]).filter((group) => group.items.some((item) => item[2])).map((group) => <div className="nav-group" key={group.title}>
          <p className="nav-label">{t(group.title)}</p>
          {group.items.filter((item) => item[2]).map(([target, label]) => {
            const active = page === target || page.startsWith(`${target}/`) || page.startsWith(`${target}?`);
            return <a key={target} className={`nav-item ${active ? 'active' : ''}`} aria-current={active ? 'page' : undefined} href={`#${target}`}><span className="nav-marker" aria-hidden="true" />{t(label)}</a>;
          })}
        </div>)}
      </nav>
    </aside>
    <div className="main-column">
      <header className="topbar"><div className="identity"><strong>{auth.identity.user.display_name}</strong><span>{t(userRoles[auth.identity.user.role])}</span></div>
        <div className="account-actions"><a href="#password">{t('auth.changePassword')}</a><button className="secondary compact" disabled={leaving} onClick={() => void logout()}>{t('auth.signOut')}</button></div>
      </header>
      <main id="content" tabIndex={-1}>
        <ErrorNotice error={error || auth.error} />
        {appearance.error && <p role="status" className="notice warning">{t('appearance.loadError')}</p>}
        {page === 'connections' ? administrator ? <ConnectionsPage /> : <ErrorNotice error={{ key: 'errors.accessDenied' }} /> : page === 'settings' ? administrator ? <SettingsPage /> : <ErrorNotice error={{ key: 'errors.accessDenied' }} /> : page === 'digests' ? <DigestsPage /> : page.startsWith('notifications/') ? <NotificationPage key={page} identifier={page.slice(14)} /> : page === 'notifications' || page.startsWith('notifications?') || page === 'dead-letters' ? <NotificationsPage key={page} deadLetters={page === 'dead-letters'} eventId={new URLSearchParams(page.split('?')[1] ?? '').get('event_id') ?? undefined} /> : page === 'channels' ? administrator ? <ChannelsPage /> : <ErrorNotice error={{ key: 'errors.accessDenied' }} /> : page === 'templates' ? administrator ? <TemplatesPage /> : <ErrorNotice error={{ key: 'errors.accessDenied' }} /> : page === 'escalations' ? administrator ? <PoliciesPage /> : <ErrorNotice error={{ key: 'errors.accessDenied' }} /> : page === 'routing' ? administrator ? <RoutingPage /> : <ErrorNotice error={{ key: 'errors.accessDenied' }} /> : page === 'simulation' ? administrator ? <SimulationPage /> : <ErrorNotice error={{ key: 'errors.accessDenied' }} /> : page === 'sources' ? administrator ? <SourcesPage /> : <ErrorNotice error={{ key: 'errors.accessDenied' }} /> : page === 'identification' ? administrator ? <RulesPage /> : <ErrorNotice error={{ key: 'errors.accessDenied' }} /> : page === 'events' ? <EventsPage /> : page.startsWith('events/') ? <EventPage key={page} identifier={page.slice(7)} /> : page === 'users' ? administrator ? <UsersPage /> : <ErrorNotice error={{ key: 'errors.accessDenied' }} />
          : page === 'audit' ? canAudit ? <AuditPage /> : <ErrorNotice error={{ key: 'errors.accessDenied' }} />
          : page === 'health' ? canHealth ? <HealthPage /> : <ErrorNotice error={{ key: 'errors.accessDenied' }} />
          : page === 'password' ? <PasswordPage /> : page === 'overview' || page === 'content' ? <>
        <div className="page-heading">
          <h1>{t('overview.title')}</h1>
          <p>{t('overview.subtitle')}</p>
        </div>
        <section className="card introduction" aria-labelledby="setup-title">
          <span className="eyebrow">{t('overview.setupLabel')}</span>
          <h2 id="setup-title">{t('overview.setupTitle')}</h2>
          <p>{t('overview.setupBody')}</p>
          <p className="muted">{t('overview.setupHint')}</p>
        </section>
        <div className="overview-links">
          {([
            ['events', 'navigation.events', 'overview.eventsHint'],
            ['notifications', 'navigation.notifications', 'overview.notificationsHint'],
            ['digests', 'digests.title', 'overview.digestsHint'],
          ] satisfies [string, TranslationKey, TranslationKey][]).map(([target, title, description]) => <a className="card overview-link" href={`#${target}`} key={target}><h2>{t(title)}</h2><p>{t(description)}</p></a>)}
        </div>
        <OverviewCounts />
        <ConnectionCard />
        </> : <ErrorNotice error={{ key: 'errors.pageNotFoundBody' }} />}
      </main>
    </div>
  </div>;
}
