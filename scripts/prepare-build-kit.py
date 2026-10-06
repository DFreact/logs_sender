"""Export the exact dependency images and whitelisted source; no package downloads."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.run(args, capture_output=True, check=True).stdout


def main(destination):
    os.umask(0o077)
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    source = destination / "source"
    source.mkdir()
    ignore = shutil.ignore_patterns(
        ".venv",
        ".git",
        ".agents",
        ".codex",
        ".ruff_cache",
        "*.pyc",
        "node_modules",
        "dist",
        "__pycache__",
        ".pytest_cache",
        "test-results*",
        "playwright-report",
        ".env*",
        ".local",
    )
    for name in ("backend", "frontend", "infra"):
        shutil.copytree(ROOT / name, source / name, ignore=ignore)
    shutil.copyfile(ROOT / ".dockerignore", source / ".dockerignore")
    shutil.copyfile(ROOT / "scripts/release/build_source.py", destination / "build.py")
    shutil.copyfile(ROOT / "scripts/release/preflight.py", destination / "preflight.py")
    for name in (
        "admin.py",
        "messages.ru.json",
        "network_guard.py",
        "network_policy.py",
    ):
        shutil.copyfile(ROOT / "scripts/release" / name, destination / name)
    shutil.copyfile(ROOT / "docs/astra-linux.md", destination / "instructions.md")
    images, bases = {}, {}
    for kind, name in [
        ("backend", "eventhub-backend-deps:0.12.0"),
        ("web", "eventhub-web-runtime:0.12.0"),
        ("node", "eventhub-web-build-deps:0.12.0"),
    ]:
        item = json.loads(run("docker", "image", "inspect", name))[0]
        alias = "eventhub-build:" + item["Id"].split(":", 1)[1]
        run("docker", "image", "tag", name, alias)
        bases[kind] = alias
        images[alias] = item["Id"]
    run(
        "docker",
        "image",
        "save",
        "--output",
        str(destination / "dependencies.tar"),
        *images,
    )
    files = {}
    for path in destination.rglob("*"):
        if path.is_file():
            value = hashlib.sha256()
            with path.open("rb") as handle:
                for data in iter(lambda: handle.read(1024 * 1024), b""):
                    value.update(data)
            files[str(path.relative_to(destination))] = value.hexdigest()
    (destination / "build-manifest.json").write_text(
        json.dumps(
            {"format": 1, "images": images, "bases": bases, "files": files}, indent=2
        )
        + "\n"
    )
    print("Комплект исходников и зависимостей подготовлен для сборки без Интернета.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output.resolve())
