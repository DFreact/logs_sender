import json
import resource
import sys
from pathlib import Path

resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from app.adapters.output import Adapter  # noqa: E402
from app.api.errors import ApiFailure  # noqa: E402
from app.settings import Settings  # noqa: E402

try:
    data = json.loads(sys.stdin.buffer.read(32768))
    result = Adapter(
        Settings(**data["settings"]), data["configuration"], lambda: data["secret"]
    ).test_connection()
    print(json.dumps({"result": result}))
except ApiFailure as exc:
    print(json.dumps({"code": exc.code}))
except Exception:
    print(json.dumps({"code": "CHANNEL_CONNECTION_FAILED"}))
