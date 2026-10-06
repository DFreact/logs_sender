from enum import StrEnum

from app.domain.enums import UserRole


class Permission(StrEnum):
    VIEW_EVENTS = "VIEW_EVENTS"
    ACKNOWLEDGE_EVENTS = "ACKNOWLEDGE_EVENTS"
    SUPPRESS_EVENTS = "SUPPRESS_EVENTS"
    RESOLVE_EVENTS = "RESOLVE_EVENTS"
    RETRY_NOTIFICATIONS = "RETRY_NOTIFICATIONS"
    CANCEL_NOTIFICATIONS = "CANCEL_NOTIFICATIONS"
    MANAGE_USERS = "MANAGE_USERS"
    MANAGE_CONFIGURATION = "MANAGE_CONFIGURATION"
    VIEW_AUDIT = "VIEW_AUDIT"
    VIEW_SYSTEM_HEALTH = "VIEW_SYSTEM_HEALTH"
    SEND_TEST_NOTIFICATION = "SEND_TEST_NOTIFICATION"


ROLE_PERMISSIONS = {
    UserRole.ADMINISTRATOR: frozenset(Permission),
    UserRole.OPERATOR: frozenset(
        {
            Permission.VIEW_EVENTS,
            Permission.ACKNOWLEDGE_EVENTS,
            Permission.RESOLVE_EVENTS,
            Permission.SUPPRESS_EVENTS,
            Permission.RETRY_NOTIFICATIONS,
            Permission.CANCEL_NOTIFICATIONS,
        }
    ),
    UserRole.VIEWER: frozenset({Permission.VIEW_EVENTS}),
}


def permitted(role: str, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset())
