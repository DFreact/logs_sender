import type {
  DigestState, DigestPeriod, DigestSelection, EscalationState, EscalationStepState, AttemptStatus, DeliveryMode, ErrorCode, EventStatus, HealthStatus,
  NotificationStatus, Severity, DeliveryReason, UserRole, AuditAction, WorkState, ComponentCode, HealthReason,
} from '../api/generated';
import type { TranslationKey } from './types';

export const eventStatuses = {
  NEW: 'events.status.NEW', ACKNOWLEDGED: 'events.status.ACKNOWLEDGED',
  RESOLVED: 'events.status.RESOLVED', SUPPRESSED: 'events.status.SUPPRESSED',
} satisfies Record<EventStatus, TranslationKey>;
export const notificationStatuses = {
  PENDING: 'notifications.status.PENDING', PROCESSING: 'notifications.status.PROCESSING',
  SENT: 'notifications.status.SENT', FAILED: 'notifications.status.FAILED',
  RETRYING: 'notifications.status.RETRYING', CANCELLED: 'notifications.status.CANCELLED',
} satisfies Record<NotificationStatus, TranslationKey>;
export const severities = {
  DEBUG: 'events.severity.DEBUG', INFO: 'events.severity.INFO', WARNING: 'events.severity.WARNING',
  ERROR: 'events.severity.ERROR', CRITICAL: 'events.severity.CRITICAL',
} satisfies Record<Severity, TranslationKey>;
export const deliveryModes = {
  IMMEDIATE: 'notifications.mode.IMMEDIATE', DELAYED: 'notifications.mode.DELAYED',
  SCHEDULED: 'notifications.mode.SCHEDULED', DIGEST: 'notifications.mode.DIGEST',
} satisfies Record<DeliveryMode, TranslationKey>;
export const healthStatuses = {
  HEALTHY: 'health.status.HEALTHY', DEGRADED: 'health.status.DEGRADED',
  UNAVAILABLE: 'health.status.UNAVAILABLE', UNKNOWN: 'health.status.UNKNOWN',
} satisfies Record<HealthStatus, TranslationKey>;
export const attemptStatuses = {
  PROCESSING: 'notifications.attempt.PROCESSING', SENT: 'notifications.attempt.SENT',
  FAILED: 'notifications.attempt.FAILED', UNKNOWN: 'notifications.attempt.UNKNOWN',
} satisfies Record<AttemptStatus, TranslationKey>;
export const userRoles = {
  ADMINISTRATOR: 'users.role.ADMINISTRATOR', OPERATOR: 'users.role.OPERATOR', VIEWER: 'users.role.VIEWER',
} satisfies Record<UserRole, TranslationKey>;
export const errorKeys = {
  RAW_MESSAGE_EXPIRED: 'errors.rawExpired',
  CURSOR_INVALID: 'errors.cursorInvalid',
  QUERY_TOO_BROAD: 'errors.queryTooBroad',
  DIGEST_BACKLOG: "errors.digestBacklog", DIGEST_STATE_CONFLICT: "errors.digestState",
  EVENT_STATE_CONFLICT: "errors.eventStateConflict",
  NOTIFICATION_STATE_CONFLICT: 'errors.notificationState', IDEMPOTENCY_CONFLICT: 'errors.idempotency',
  CHANNEL_SECRET_UNAVAILABLE: 'errors.channelSecret', OUTBOUND_BLOCKED: 'errors.outboundBlocked', CHANNEL_CONNECTION_FAILED: 'errors.channelConnection', CHANNEL_UNAVAILABLE: 'errors.channelUnavailable',
  ACTION_TARGET_UNAVAILABLE: 'errors.actionTargetUnavailable',
  INGEST_KEY_LIMIT: 'connections.keyLimit',
  CONFIGURATION_LIMIT: 'errors.configurationLimit', SOURCE_NAME_TAKEN: 'errors.sourceNameTaken',
  UNSUPPORTED_CONTENT_TYPE: 'errors.unsupportedContentType', INVALID_INPUT: 'errors.invalidInput',
  INTERNAL_ERROR: 'errors.internal', VALIDATION_ERROR: 'validation.invalid',
  VALIDATION_REQUIRED: 'validation.required', RESOURCE_NOT_FOUND: 'errors.notFound',
  METHOD_NOT_ALLOWED: 'errors.method', ACCESS_DENIED: 'errors.accessDenied',
  SESSION_EXPIRED: 'errors.sessionExpired', INVALID_CREDENTIALS: 'errors.credentials',
  RATE_LIMITED: 'errors.rateLimited', VERSION_CONFLICT: 'errors.conflict',
  SERVICE_UNAVAILABLE: 'errors.serviceUnavailable', REQUEST_TOO_LARGE: 'errors.tooLarge',
  MAIL_CONNECTION_FAILED: 'errors.mailConnection', DELIVERY_FAILED: 'errors.delivery',
  TEMPLATE_INVALID: 'errors.template',
  CSRF_FAILED: 'errors.csrf', USERNAME_TAKEN: 'errors.usernameTaken',
  LAST_ADMINISTRATOR: 'errors.lastAdministrator', PASSWORD_POLICY: 'errors.passwordPolicy',
  CURRENT_PASSWORD_INVALID: 'errors.currentPassword',
} satisfies Record<ErrorCode, TranslationKey>;

export const auditActions = {
  RETENTION_UPDATED: 'retention.updated',
  APPEARANCE_UPDATED: 'appearance.updated',
  DIGEST_CREATED: "audit.action.DIGEST_CREATED", DIGEST_UPDATED: "audit.action.DIGEST_UPDATED", DIGEST_PARTIAL: "audit.action.DIGEST_PARTIAL", DIGEST_WAIT: "audit.action.DIGEST_WAIT",
  EVENT_ACKNOWLEDGED: "audit.action.EVENT_ACKNOWLEDGED", EVENT_RESOLVED: "audit.action.EVENT_RESOLVED", EVENT_SUPPRESSED: "audit.action.EVENT_SUPPRESSED", ESCALATION_POLICY_CREATED: "audit.action.ESCALATION_POLICY_CREATED", ESCALATION_POLICY_UPDATED: "audit.action.ESCALATION_POLICY_UPDATED",
  NOTIFICATION_RETRIED: 'audit.action.NOTIFICATION_RETRIED', NOTIFICATION_CANCELLED: 'audit.action.NOTIFICATION_CANCELLED', TEST_NOTIFICATION_CREATED: 'audit.action.TEST_NOTIFICATION_CREATED',
  CHANNEL_CREATED: 'audit.action.CHANNEL_CREATED', CHANNEL_UPDATED: 'audit.action.CHANNEL_UPDATED', CHANNEL_TESTED: 'audit.action.CHANNEL_TESTED', OUTPUT_ADAPTER_UPDATED: 'audit.action.OUTPUT_ADAPTER_UPDATED', TEMPLATE_CREATED: 'audit.action.TEMPLATE_CREATED', TEMPLATE_UPDATED: 'audit.action.TEMPLATE_UPDATED',
  ROUTING_RULE_CREATED: 'audit.action.ROUTING_RULE_CREATED', ROUTING_RULE_UPDATED: 'audit.action.ROUTING_RULE_UPDATED',
  SOURCE_CREATED: 'audit.action.SOURCE_CREATED', SOURCE_UPDATED: 'audit.action.SOURCE_UPDATED',
  IDENTIFICATION_RULE_CREATED: 'audit.action.IDENTIFICATION_RULE_CREATED', IDENTIFICATION_RULE_UPDATED: 'audit.action.IDENTIFICATION_RULE_UPDATED', DEDUP_POLICY_UPDATED: 'audit.action.DEDUP_POLICY_UPDATED',
  INGEST_KEY_CREATED: 'audit.action.INGEST_KEY_CREATED', INGEST_KEY_REVOKED: 'audit.action.INGEST_KEY_REVOKED',
  LOGIN_SUCCEEDED: 'audit.action.LOGIN_SUCCEEDED', LOGIN_FAILED: 'audit.action.LOGIN_FAILED',
  LOGOUT: 'audit.action.LOGOUT', USER_CREATED: 'audit.action.USER_CREATED',
  USER_UPDATED: 'audit.action.USER_UPDATED', USER_PASSWORD_CHANGED: 'audit.action.USER_PASSWORD_CHANGED',
  ADMIN_BOOTSTRAPPED: 'audit.action.ADMIN_BOOTSTRAPPED',
} satisfies Record<AuditAction, TranslationKey>;

export const formFields = {
  retention_enabled: 'retention.enabled', retention_inherit: 'retention.inherit',
  event_days: 'retention.event', raw_days: 'retention.raw', notification_days: 'retention.notification',
  attempt_days: 'retention.attempt', audit_days: 'retention.audit', task_days: 'retention.task',
  history_days: 'retention.history', health_days: 'retention.health',
  appearance_palette: 'appearance.palette', appearance_mode: 'appearance.mode',
  username: 'auth.username', display_name: 'users.displayName', password: 'auth.password',
  role: 'users.role', active: 'users.enabled', current_password: 'auth.currentPassword',
  new_password: 'auth.newPassword',
} satisfies Record<string, TranslationKey>;

export function labelKey(map: Readonly<Record<string, TranslationKey>>, value: unknown,
  fallback: TranslationKey = 'common.unknownStatus'): TranslationKey {
  return typeof value === 'string' && Object.hasOwn(map, value) ? map[value] : fallback;
}

export const workStates = {
  PENDING: 'health.work.PENDING', RUNNING: 'health.work.RUNNING',
  SUCCEEDED: 'health.work.SUCCEEDED', FAILED: 'health.work.FAILED',
} satisfies Record<WorkState, TranslationKey>;
export const componentCodes = {
  DELIVERY: 'health.component.DELIVERY',
  SMTP: 'health.component.SMTP', INGESTION: 'health.component.INGESTION',
  DATABASE: 'health.component.DATABASE', BROKER: 'health.component.BROKER',
  STORAGE: 'health.component.STORAGE', DISPATCHER: 'health.component.DISPATCHER',
  SCHEDULER: 'health.component.SCHEDULER', WORKER: 'health.component.WORKER',
} satisfies Record<ComponentCode, TranslationKey>;
export const healthReasons = {
  OK: 'health.reason.OK', UNREACHABLE: 'health.reason.UNREACHABLE', STALE: 'health.reason.STALE',
  NOT_OBSERVED: 'health.reason.NOT_OBSERVED', LOW_SPACE: 'health.reason.LOW_SPACE',
} satisfies Record<HealthReason, TranslationKey>;
export const workCounts = {
  PENDING: 'health.count.PENDING', RUNNING: 'health.count.RUNNING',
  SUCCEEDED: 'health.count.SUCCEEDED', FAILED: 'health.count.FAILED',
} satisfies Record<WorkState, TranslationKey>;

export const deliveryReasons = {
  EVENT_ACKNOWLEDGED: "delivery.reason.EVENT_ACKNOWLEDGED", EVENT_RESOLVED: "delivery.reason.EVENT_RESOLVED",
  CHANNEL_DISABLED: 'delivery.reason.CHANNEL_DISABLED',
  ADAPTER_DISABLED: 'delivery.reason.ADAPTER_DISABLED',
  CHANNEL_CHANGED: 'delivery.reason.CHANNEL_CHANGED',
  SECRET_UNAVAILABLE: 'delivery.reason.SECRET_UNAVAILABLE',
  TEMPLATE_INVALID: 'delivery.reason.TEMPLATE_INVALID',
  CHANNEL_NOT_CONFIGURED: 'delivery.reason.CHANNEL_NOT_CONFIGURED',
  OUTBOUND_BLOCKED: 'delivery.reason.OUTBOUND_BLOCKED',
  CONNECTION_FAILED: 'delivery.reason.CONNECTION_FAILED',
  AUTHENTICATION_FAILED: 'delivery.reason.AUTHENTICATION_FAILED',
  RECIPIENT_REJECTED: 'delivery.reason.RECIPIENT_REJECTED',
  RATE_LIMITED: 'delivery.reason.RATE_LIMITED',
  REMOTE_REJECTED: 'delivery.reason.REMOTE_REJECTED',
  REMOTE_UNAVAILABLE: 'delivery.reason.REMOTE_UNAVAILABLE',
  UNKNOWN_RESULT: 'delivery.reason.UNKNOWN_RESULT',
  EVENT_SUPPRESSED: 'delivery.reason.EVENT_SUPPRESSED',
} satisfies Record<DeliveryReason, TranslationKey>;

export const escalationStates = { ACTIVE: "escalation.run.ACTIVE", COMPLETED: "escalation.run.COMPLETED", STOPPED: "escalation.run.STOPPED" } satisfies Record<EscalationState, TranslationKey>;
export const escalationStepStates = { WAITING: "escalation.step.WAITING", QUEUED: "escalation.step.QUEUED", CANCELLED: "escalation.step.CANCELLED" } satisfies Record<EscalationStepState, TranslationKey>;

export const digestStates = { WAITING: "digests.state.WAITING", ATTENTION: "digests.state.ATTENTION", BUILDING: "digests.state.BUILDING", READY: "digests.state.READY", EMPTY: "digests.state.EMPTY", SKIPPED: "digests.state.SKIPPED" } satisfies Record<DigestState, TranslationKey>;
export const digestPeriods = { CALENDAR: "digests.period.CALENDAR", LAST_24_HOURS: "digests.period.LAST_24_HOURS" } satisfies Record<DigestPeriod, TranslationKey>;
export const digestSelections = { FILTER: "digests.selection.FILTER", RULE: "digests.selection.RULE" } satisfies Record<DigestSelection, TranslationKey>;
