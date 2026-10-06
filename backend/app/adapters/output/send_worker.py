import json
import resource
import sys
from dataclasses import asdict
from pathlib import Path

resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from app.adapters.output import Adapter, PreparedNotification  # noqa: E402
from app.settings import Settings  # noqa: E402

try:
    data = json.loads(sys.stdin.buffer.read(512 * 1024))
    adapter = Adapter(Settings(**data["settings"]), data["configuration"], lambda: data["secret"])
    print(json.dumps(asdict(adapter.send(PreparedNotification(**data["prepared"])))))
except Exception:
    print('{"outcome":"UNKNOWN","reason":"UNKNOWN_RESULT","retry_after":null}')
