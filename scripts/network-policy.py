"""Prepare reviewable, project-scoped network restrictions without applying them."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "release"))
from admin import MESSAGES, Parser, say  # noqa: E402
from network_policy import policy  # noqa: E402


def main():
    parser = Parser(description=MESSAGES["networkDescription"], add_help=False)
    parser.add_argument("--project", required=True)
    parser.add_argument("--subnet", required=True)
    parser.add_argument("--max-ip", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    metadata, config, rules = policy(args.project, args.subnet, args.max_ip)
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    (args.output / "policy.json").write_text(json.dumps(metadata, indent=2) + "\n")
    (args.output / "compose.network.json").write_text(json.dumps(config, indent=2) + "\n")
    (args.output / "outbound.nft").write_text(rules)
    say("networkPrepared")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        say("networkInvalid")
        sys.exit(1)
