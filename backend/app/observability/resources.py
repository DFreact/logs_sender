"""Linux resources visible through /proc; not container quota measurements.

Field semantics: https://docs.kernel.org/filesystems/proc.html
Never expose host names, mount paths, boot IDs or process data in the API.
"""

from pathlib import Path


def read_resources(root=Path("/proc")):
    result = {"cpu": None, "memory": None}
    try:
        with (root / "stat").open() as stream:
            cpu = [int(value) for value in stream.readline().split()[1:9]]
        boot = (root / "sys/kernel/random/boot_id").read_text().strip()
        if len(cpu) == 8 and all(value >= 0 for value in cpu):
            result["cpu"] = {"total": sum(cpu), "busy": sum(cpu[:3] + cpu[5:7]), "boot": boot}
    except (OSError, ValueError):
        pass
    try:
        values = {}
        with (root / "meminfo").open() as stream:
            for line in stream:
                key, _, value = line.partition(":")
                if key in {"MemTotal", "MemAvailable"}:
                    values[key] = int(value.split()[0]) * 1024
        total, available = values["MemTotal"], values["MemAvailable"]
        if 0 <= available <= total and total > 0:
            result["memory"] = {"total": total, "available": available}
    except (OSError, ValueError, KeyError, IndexError):
        pass
    return result


def cpu_percent(latest, previous):
    if not latest or not previous or latest["boot"] != previous["boot"]:
        return None
    total, busy = latest["total"] - previous["total"], latest["busy"] - previous["busy"]
    return 100 * busy / total if total > 0 and 0 <= busy <= total else None
