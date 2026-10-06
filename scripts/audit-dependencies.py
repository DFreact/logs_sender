"""Explicit online audit on a preparation workstation; sends only package names/versions."""

import argparse
import json
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.run(args, capture_output=True, check=True).stdout.decode()


def scan(queries):
    results = []
    for offset in range(0, len(queries), 100):
        request = urllib.request.Request(
            "https://api.osv.dev/v1/querybatch",
            data=json.dumps({"queries": queries[offset : offset + 100]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            results.extend(json.load(response)["results"])
    return results


def main(output, previous_report=None):
    queries = []
    for line in (ROOT / "backend/requirements.txt").read_text().splitlines():
        if line and not line.startswith("#"):
            name, version = line.split("==")
            queries.append(
                {"package": {"name": name, "ecosystem": "PyPI"}, "version": version}
            )
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text())
    for key, data in lock["packages"].items():
        if key:
            queries.append(
                {
                    "package": {
                        "name": key.rsplit("node_modules/", 1)[1],
                        "ecosystem": "npm",
                    },
                    "version": data["version"],
                }
            )
    images = {}
    for image in (
        "eventhub-backend:0.12.2",
        "eventhub-web:0.12.2",
        "eventhub-redis:0.12.2",
        "eventhub-postgres:16.15-security.1",
    ):
        # Packages are read with all external networking disabled. No image pulls.
        info = json.loads(run("docker", "image", "inspect", image))[0]
        command = "if command -v dpkg-query >/dev/null; then dpkg-query -W -f='${source:Package}\\t${source:Version}\\n'; else cat /lib/apk/db/installed; fi"
        packages = run(
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--entrypoint=sh",
            image,
            "-c",
            command,
        )
        os_release = run(
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--entrypoint=cat",
            image,
            "/etc/os-release",
        )
        images[image] = {
            "id": info["Id"],
            "os_release": os_release,
            "packages": packages,
        }
        if "ID=debian" in os_release:
            version = next(
                line.split("=", 1)[1].strip('"')
                for line in os_release.splitlines()
                if line.startswith("VERSION_ID=")
            )
            for line in packages.splitlines():
                name, version_package = line.split("\t")
                queries.append(
                    {
                        "package": {"name": name, "ecosystem": "Debian:" + version},
                        "version": version_package,
                    }
                )
        elif 'ID=alpine' in os_release:
            version = next(line.split('=', 1)[1].strip('"') for line in os_release.splitlines() if line.startswith('VERSION_ID='))
            ecosystem = 'Alpine:v' + '.'.join(version.split('.')[:2])
            for block in packages.split('\n\n'):
                data = dict(line.split(':', 1) for line in block.splitlines() if ':' in line)
                if 'P' in data and 'V' in data:
                    queries.append({'package': {'name': data.get('o', data['P']), 'ecosystem': ecosystem}, 'version': data['V']})
        else:
            raise ValueError("UNSUPPORTED_OS_INVENTORY")
    queries = list({json.dumps(q, sort_keys=True): q for q in queries}.values())
    query_timestamp = datetime.now(timezone.utc).isoformat()
    if previous_report:
        previous = json.loads(previous_report.read_text())
        key = lambda value: json.dumps(value, sort_keys=True)
        cached = {key(q): result for q, result in zip(previous["queries"], previous["results"])}
        if {key(q) for q in queries} != set(cached):
            raise ValueError("PACKAGE_INVENTORY_CHANGED")
        results = [cached[key(q)] for q in queries]
        query_timestamp = previous.get("query_timestamp", previous["timestamp"])
    else:
        results = scan(queries)
    findings = [
        {"query": q, "vulns": result["vulns"]}
        for q, result in zip(queries, results)
        if result.get("vulns")
    ]
    output.write_text(
        json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "service": "OSV",
                "query_timestamp": query_timestamp,
                "mode": "unchanged_inventory_cached_results" if previous_report else "online",
                "queries": queries,
                "results": results,
                "findings": findings,
                "images": images,
            },
            indent=2,
        )
        + "\n"
    )
    print(
        json.dumps(
            {"packages": len(queries), "packages_with_advisories": len(findings)}
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--previous-report", type=Path)
    args = parser.parse_args()
    main(args.output, args.previous_report)
