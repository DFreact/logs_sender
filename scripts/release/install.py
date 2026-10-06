"""Single-entry offline installation. All displayed text lives in messages.ru.json."""
import json
import os
import subprocess
import sys
from pathlib import Path

import admin
from host import choose_subnet, local_engine


def main():
    os.umask(0o077)
    parser = admin.Parser(description=admin.MESSAGES["installDescription"], usage=admin.MESSAGES["usage"] + admin.MESSAGES["installUsage"], add_help=False)
    parser._optionals.title = admin.MESSAGES["options"]
    parser.add_argument("--help", action="help", help=admin.MESSAGES["installDescription"])
    parser.add_argument("--directory", type=Path, default=Path("/srv/eventhub"), metavar=admin.MESSAGES["path"], help=admin.MESSAGES["directoryHelp"])
    parser.add_argument("--project", default="eventhub", metavar=admin.MESSAGES["project"], help=admin.MESSAGES["projectHelp"])
    parser.add_argument("--subnet", metavar=admin.MESSAGES["subnet"], help=admin.MESSAGES["subnetHelp"])
    parser.add_argument("--http-port", type=int, default=8080, metavar=admin.MESSAGES["port"], help=admin.MESSAGES["httpPortHelp"])
    parser.add_argument("--smtp-port", type=int, default=2525, metavar=admin.MESSAGES["port"], help=admin.MESSAGES["smtpPortHelp"])
    parser.add_argument("--dns", action="append", default=[], metavar=admin.MESSAGES["address"], help=admin.MESSAGES["dnsHelp"])
    parser.add_argument("--ca-file", type=Path, metavar=admin.MESSAGES["path"], help=admin.MESSAGES["caHelp"])
    parser.add_argument("--skip-admin", action="store_true", help=admin.MESSAGES["skipAdminHelp"])
    parser.add_argument("--admin-user", default="admin", metavar=admin.MESSAGES["project"], help=admin.MESSAGES["adminUserHelp"])
    args = parser.parse_args()
    args.isolated = False
    if args.directory.exists():
        raise admin.Failure("exists")
    if not args.skip_admin and not sys.stdin.isatty():
        raise admin.Failure("installNeedsTerminal")
    manifest = admin.verified(admin.HERE, "release")
    try:
        local_engine()
        args.subnet = choose_subnet(args.subnet)
    except (RuntimeError, ValueError, OSError):
        raise admin.Failure("subnetUnavailable") from None
    # The preflight is read-only; retain evidence next to the installation.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="eventhub-check-") as temporary:
        report = Path(temporary) / "preflight.json"
        admin.run([sys.executable, str(admin.HERE / "preflight.py"), "--output", str(report)])
        evidence = json.loads(report.read_text())
    admin.say("installLoading")
    admin.run(["docker", "image", "load", "--input", str(admin.HERE / "images.tar")])
    admin.images(manifest)
    directory = admin.prepare(args, manifest)
    admin.write_json(directory / "preflight.json", evidence)
    with admin.lock(directory):
        admin.up(directory)
        admin.mark_ready(directory)
    if not args.skip_admin:
        admin.say("installAdmin")
        command = ["docker", "compose", "--project-name", args.project,
                   "--env-file", str(directory / "settings.env"),
                   "-f", str(directory / "compose.json"), "exec", "api",
                   "python", "-m", "app.cli", "create-admin", "--username", args.admin_user,
                   "--display-name", admin.MESSAGES["administrator"]]
        result = subprocess.run(command, cwd=directory, check=False,
                                env={k: v for k, v in os.environ.items() if not k.startswith(("HUB_", "COMPOSE_"))})
        if result.returncode:
            raise admin.Failure("installAdminFailed")
    print(admin.MESSAGES["installComplete"].format(directory=directory, subnet=args.subnet, port=args.http_port))
    if args.skip_admin:
        admin.say("installAdminSkipped")


if __name__ == "__main__":
    try:
        main()
    except admin.Failure as error:
        admin.say(str(error))
        sys.exit(1)
    except KeyboardInterrupt:
        admin.say("interrupted")
        sys.exit(130)
    except Exception:  # noqa: BLE001 - restore safely or show a sanitized CLI error
        admin.say("failed")
        sys.exit(1)
