import time
from pathlib import Path

if __name__ == "__main__":
    try:
        age = time.time() - float(Path("/tmp/eventhub-background-alive").read_text())
        raise SystemExit(0 if 0 <= age <= 30 else 1)
    except (OSError, ValueError):
        raise SystemExit(1) from None
