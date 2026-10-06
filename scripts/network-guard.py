"""Host administration boundary: no shell, no global firewall flush."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "release"))
from admin import MESSAGES, Parser, say  # noqa: E402
from network_guard import apply, check  # noqa: E402

if __name__ == "__main__":
    parser = Parser(description=MESSAGES["networkGuardDescription"], add_help=False)
    parser.add_argument("command", choices=("apply", "check"))
    parser.add_argument("--policy", required=True, type=Path)
    try:
        args = parser.parse_args()
        (apply if args.command == "apply" else check)(args.policy.resolve())
        say("networkGuardOK")
    except Exception:
        say("networkGuardFailed")
        sys.exit(1)
