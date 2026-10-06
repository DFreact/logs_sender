"""Check a copied or switched local installation without printing credentials."""

import argparse
import http.cookiejar
import json
import smtplib
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/release"))
import admin

sys.path.insert(0, str(ROOT / "scripts"))
from database_checks import verify as database_verify


def verify(kit, directory, base, credentials, smtp_port, exercise=False, origin=None):
    admin.HERE = kit.resolve()

    def run(*args, input_text=None):
        return admin.compose(
            directory.resolve(),
            *args,
            input=input_text.encode() if input_text else None,
        ).decode()

    report = database_verify(run)
    info = json.loads((directory / "installation.json").read_text())
    ids = run("ps", "-q").split()
    containers = json.loads(admin.run(["docker", "inspect", *ids]))
    assert len(containers) == 10
    networks = {
        n["NetworkID"]
        for c in containers
        for n in c["NetworkSettings"]["Networks"].values()
    }
    assert len(networks) == 1
    network = json.loads(admin.run(["docker", "network", "inspect", *networks]))[0]
    assert network["Internal"] and not network["EnableIPv6"]
    assert all(
        c["State"].get("Health", {}).get("Status") == "healthy" for c in containers
    )
    code = """import errno,json,socket
from pathlib import Path
rows=Path('/proc/net/route').read_text().splitlines()[1:]
assert all(row.split()[1] != '00000000' for row in rows)
s=socket.socket();s.settimeout(1)
assert s.connect_ex(('192.0.2.10',443)) == errno.ENETUNREACH
s.close()
print('isolated')
"""
    for service in (
        "api",
        "delivery",
        "smtp",
        "ingestion",
        "worker",
        "scheduler",
        "dispatcher",
    ):
        assert run("exec", "-T", service, "python", "-c", code).strip() == "isolated"
    identity = json.loads(credentials.read_text())
    client = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )

    def request(path, body=None, csrf=None, token=None):
        headers = {"Origin": origin or base}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if csrf:
            headers["X-CSRF-Token"] = csrf
        if token:
            headers["Authorization"] = "Bearer " + token
        with client.open(
            urllib.request.Request(
                base + path,
                data=json.dumps(body).encode() if body is not None else None,
                headers=headers,
            ),
            timeout=15,
        ) as response:
            raw = response.read()
            return json.loads(raw) if raw else None

    csrf = request("/api/v1/auth/csrf")["csrf_token"]
    logged = request(
        "/api/v1/auth/login",
        {"username": identity["username"], "password": identity["password"]},
        csrf,
    )
    assert logged["user"]["role"] == "ADMINISTRATOR"
    csrf = logged["csrf_token"]
    connections = request("/api/v1/connections")
    assert (
        connections["rest_path"] == "/api/v1/ingest"
        and connections["smtp"]["host"] == "127.0.0.1"
    )
    assert connections["smtp"]["port"] == smtp_port
    for path in (
        "/api/v1/events",
        "/api/v1/channels",
        "/api/v1/connections/keys",
        "/api/v1/users",
        "/api/v1/output-adapters",
    ):
        request(path)
    if exercise:
        key = request("/api/v1/connections/keys", {"name": "Проверка переноса"}, csrf)
        receipt = request(
            "/api/v1/ingest",
            {"subject": "Проверка обновления", "body": "Пробная копия"},
            token=key["token"],
        )
        for _ in range(40):
            progress = request(
                "/api/v1/ingest/" + receipt["receipt_id"], token=key["token"]
            )
            if progress.get("event_id"):
                break
            time.sleep(1)
        assert progress.get("event_id")
        request("/api/v1/connections/keys/" + key["key"]["id"] + "/revoke", {}, csrf)
        with smtplib.SMTP("127.0.0.1", smtp_port, timeout=10) as smtp:
            assert smtp.noop()[0] == 250
            assert not smtp.sendmail(
                "probe@example.org",
                ["events@localhost"],
                b"Subject: Upgrade probe\r\n\r\nLocal rehearsal only\r\n",
            )
    request("/api/v1/auth/logout", {}, csrf)
    report.update(
        {
            "release": info["release"],
            "all_ten_services_healthy": True,
            "single_internal_network": True,
            "ipv6_disabled": True,
            "no_default_route_and_outside_destination_unreachable": True,
            "saved_admin_login_verified": True,
            "connections_api_available": True,
            "local_rest_and_smtp_exercised": exercise,
        }
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--kit", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--smtp-port", type=int, required=True)
    parser.add_argument("--exercise", action="store_true")
    parser.add_argument("--origin")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(
        args.kit,
        args.directory,
        args.base_url,
        args.credentials,
        args.smtp_port,
        args.exercise,
        args.origin,
    )
    admin.write_json(args.output, result)
    print("Права, изоляция сети, доступ администратора и новый интерфейс проверены.")
