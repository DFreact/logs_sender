import { kindKeys, kinds, type Kind } from '../../api/channels';
import { useI18n, type TranslationKey } from '../../i18n';

const helpKeys = {
  SMTP: ['channels.smtpSetup', 'channels.smtpCredentials', 'channels.smtpNetwork'],
  MAX: ['channels.maxSetup', 'channels.maxCredentials', 'channels.maxNetwork'],
  TELEGRAM: ['channels.telegramSetup', 'channels.telegramCredentials', 'channels.telegramNetwork'],
  WEBHOOK: ['channels.webhookSetup', 'channels.webhookCredentials', 'channels.webhookNetwork'],
} satisfies Record<Kind, TranslationKey[]>;
export function ChannelHelp({ kind }: { kind?: Kind }) {
  const { t } = useI18n();
  return <details className="channel-help"><summary>{t('channels.setupHelp')}</summary>
    {(kind ? [kind] : kinds).map((k) => <section key={k}><h3>{t(kindKeys[k])}</h3>{helpKeys[k].map((key) => <p key={key}>{t(key)}</p>)}</section>)}
    <p>{t('channels.setupFlow')}</p><p className="field-hint">{t('channels.probeHint')}</p>
  </details>;
}
