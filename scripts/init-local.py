"""Create a local secret without printing it or replacing an existing one."""

import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
directory = root / ".local" / "secrets"
directory.mkdir(parents=True, exist_ok=True, mode=0o700)
directory.chmod(0o700)
for name in ("db_password", "db_admin_password", "db_migration_password", "auth_secret", "redis_password", "channel_secret"):
    target = directory / name
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(f"{name}: уже существует, оставлен без изменений.")
    else:
        with os.fdopen(descriptor, "w") as output:
            output.write(secrets.token_urlsafe(48) + "\n")
        # The private parent directory protects the host file; non-root containers
        # need to read the mounted file, which preserves host permissions.
        target.chmod(0o444)
        print(f"{name}: создан, содержимое не выводится.")
