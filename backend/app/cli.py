"""Local bootstrap only; never accepts passwords in command-line arguments."""

import argparse
import getpass
import sys

from app.api.schemas import UserCreate
from app.i18n import t
from app.persistence.database import build_engine, build_session_factory
from app.security.bootstrap import create_first_admin
from app.settings import Settings


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise SystemExit(t("invalidArguments"))


def main():
    parser = Parser(description=t("description"), add_help=False)
    parser._positionals.title = t("commands")
    parser._optionals.title = t("parameters")
    parser.add_argument("command", choices=["create-admin"])
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name", required=True)
    args = parser.parse_args()
    if not sys.stdin.isatty():
        raise SystemExit(t("interactiveRequired"))
    password = getpass.getpass(t("password"))
    if password != getpass.getpass(t("repeatPassword")):
        raise SystemExit(t("passwordMismatch"))
    engine = None
    try:
        body = UserCreate(username=args.username, display_name=args.display_name, password=password)
        engine = build_engine(Settings())
        with build_session_factory(engine)() as db:
            create_first_admin(db, body)
    except RuntimeError as error:
        if str(error) == "BOOTSTRAP_ALREADY_COMPLETED":
            raise SystemExit(t("alreadyCreated")) from None
        raise SystemExit(t("connectionError")) from None
    except Exception:
        # Never print Pydantic input or driver exception text, even in the local CLI.
        raise SystemExit(t("creationError")) from None
    finally:
        if engine:
            engine.dispose()
    print(t("created"))


if __name__ == "__main__":
    main()
