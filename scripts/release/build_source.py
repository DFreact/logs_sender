"""Rebuild source from exported local dependency images with networking disabled."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from admin import say

ROOT = Path(__file__).resolve().parent


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for data in iter(lambda: source.read(1024 * 1024), b""):
            value.update(data)
    return value.hexdigest()


def run(*args):
    result = subprocess.run(args, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError("BUILD_FAILED")
    return result.stdout


def main():
    if (ROOT / "build-result.json").exists():
        raise ValueError("BUILD_RESULT_EXISTS")
    manifest = json.loads((ROOT / "build-manifest.json").read_text())
    actual = {
        str(p.relative_to(ROOT))
        for p in ROOT.rglob("*")
        if p.is_file() and p.relative_to(ROOT).parts[0] != "__pycache__"
    }
    if actual != set(manifest["files"]) | {"build-manifest.json"}:
        raise ValueError("UNEXPECTED_BUILD_FILES")
    for name, expected in manifest["files"].items():
        path = Path(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or any(p.is_symlink() for p in [ROOT / path, *(ROOT / path).parents])
            or sha(ROOT / path) != expected
        ):
            raise ValueError("BUILD_KIT_DAMAGED")
    present = True
    for name, identifier in manifest["images"].items():
        try:
            present = present and json.loads(run("docker", "image", "inspect", name))[0]["Id"] == identifier
        except (RuntimeError, ValueError, KeyError):
            present = False
    if not present:
        run("docker", "image", "load", "--input", str(ROOT / "dependencies.tar"))
    for name, identifier in manifest["images"].items():
        actual = json.loads(run("docker", "image", "inspect", name))[0]
        if actual["Id"] != identifier or actual["Architecture"] != "amd64":
            raise ValueError("WRONG_DEPENDENCY_IMAGE")
    common = ["docker", "build", "--pull=false", "--network=none", "--no-cache"]
    base = manifest["bases"]
    results = {}
    for kind, arguments in (
        ("backend", ["--build-arg", "DEPENDENCIES=" + base["backend"]]),
        (
            "web",
            [
                "--build-arg",
                "BUILD_DEPENDENCIES=" + base["node"],
                "--build-arg",
                "RUNTIME=" + base["web"],
            ],
        ),
    ):
        tag = "eventhub-" + kind + ":0.12.2-rebuilt"
        run(
            *common,
            *arguments,
            "-f",
            str(ROOT / "source/infra/images" / (kind + ".offline.Dockerfile")),
            "-t",
            tag,
            str(ROOT / "source"),
        )
        results[tag] = json.loads(run("docker", "image", "inspect", tag))[0]["Id"]
    report = {
        "manifest_sha256": sha(ROOT / "build-manifest.json"),
        "network": "none",
        "cache": False,
        "base_images": manifest["images"],
        "outputs": results,
    }
    with (ROOT / "build-result.json").open("x") as target:
        json.dump(report, target, indent=2)
    say("sourceBuilt")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        say("sourceBuildFailed")
        sys.exit(1)
