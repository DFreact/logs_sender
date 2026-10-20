"""One disposable verification stack at a time on the developer workstation."""

import fcntl
import subprocess
from functools import wraps
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def exclusive(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        directory = ROOT / ".local"
        directory.mkdir(exist_ok=True, mode=0o700)
        with (directory / "verification.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SystemExit(
                    "Уже выполняется проверка на временном стенде. Дождитесь её завершения."
                ) from None
            available = next(
                int(line.split()[1])
                for line in Path("/proc/meminfo").read_text().splitlines()
                if line.startswith("MemAvailable:")
            )
            if available < 3 * 1024 * 1024:
                raise SystemExit(
                    "Для временного стенда нужно не менее 3 ГБ доступной памяти. Освободите память и повторите проверку."
                )
            # A killed test runner can leave Docker containers behind after the lock closes.
            running = subprocess.run(
                ["docker", "ps", "--format", '{{.Label "com.docker.compose.project"}}'],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            ).stdout.splitlines()
            if any(
                name.startswith(
                    ("eventhub-check-", "eventhub-offline-", "eventhub-install-check-")
                )
                for name in running
            ):
                raise SystemExit(
                    "Остался работающий временный стенд. Остановите его перед новой проверкой."
                )
            return function(*args, **kwargs)

    return guarded
