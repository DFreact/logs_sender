import json
from pathlib import Path

_messages = json.loads(Path(__file__).with_name("cli.ru.json").read_text())


def t(key: str) -> str:
    return _messages[key]
