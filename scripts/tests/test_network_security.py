"""The installer must fail closed before any container can be started."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "release"))
import admin
import network_guard
from network_policy import policy


def test_invalid_subnet_rejected_before_install_mutation(tmp_path):
    args = SimpleNamespace(directory=tmp_path / "install", isolated=False,
                           project="test-external", http_port=8080, smtp_port=2525,
                           subnet="0.0.0.0/0")
    with patch.object(admin, "run") as run, pytest.raises(admin.Failure, match="invalid"):
        admin.prepare(args, {})
    run.assert_not_called()
    assert not args.directory.exists()


@pytest.mark.parametrize(
    "project,subnet,ips",
    [
        ("abc;evil", "172.31.50.0/24", []),
        ("abc", "0.0.0.0/0", []),
        ("abc", "172.31.50.0/24", ["127.0.0.1"]),
        ("abc", "172.31.50.0/24", ["8.8.8.0/24"]),
        ("abc", "172.31.50.0/24", ["::1"]),
    ],
)
def test_policy_rejects_wildcards_and_injection(project, subnet, ips):
    with pytest.raises(ValueError):
        policy(project, subnet, ips)


def test_changed_compose_stops_before_docker(tmp_path):
    (tmp_path / "compose.json").write_text("{}")
    (tmp_path / "installation.json").write_text(
        json.dumps({"compose_sha256": "changed", "isolated": True})
    )
    with (
        patch.object(admin, "compose") as compose,
        pytest.raises(admin.Failure, match="configurationChanged"),
    ):
        admin.up(tmp_path)
    compose.assert_not_called()


def test_missing_live_policy_stops_before_docker(tmp_path):
    (tmp_path / "compose.json").write_text("{}")
    (tmp_path / "installation.json").write_text(
        json.dumps(
            {
                "compose_sha256": admin.digest(tmp_path / "compose.json"),
                "isolated": False,
                "network_policy": str(tmp_path),
            }
        )
    )
    with (
        patch.object(network_guard, "local_engine"),
        patch.object(network_guard, "check", side_effect=ValueError()),
        patch.object(admin, "compose") as compose,
        pytest.raises(admin.Failure, match="legacyInstallation"),
    ):
        admin.up(tmp_path)
    compose.assert_not_called()


def test_remote_docker_rejected(monkeypatch):
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("DOCKER_HOST", "ssh://remote-host")
    with (
        pytest.raises(ValueError, match="LOCAL_DOCKER_REQUIRED"),
        patch.object(network_guard, "run") as run,
    ):
        network_guard.local_engine()
    run.assert_not_called()


def test_rootless_docker_rejected(monkeypatch):
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("DOCKER_HOST", "unix:///run/user/1000/docker.sock")
    with (
        patch.object(
            network_guard,
            "run",
            return_value=b'{"OSType":"linux","SecurityOptions":["name=rootless"]}',
        ),
        pytest.raises(ValueError, match="ROOTFUL"),
    ):
        network_guard.local_engine()


def test_no_max_ips_means_no_external_access():
    _, config, rules = policy("test-secure", "172.31.50.0/24", [])
    assert "tcp dport 443" not in rules
    for service in config["services"].values():
        assert service["dns"] == ["127.0.0.1"]
        assert service["restart"] == "no"
    assert config["services"]["api"]["environment"]["HUB_MAX_ALLOWED"] == "false"


def test_private_key_is_not_accepted_as_ca(tmp_path):
    certificate = tmp_path / 'ca.pem'
    certificate.write_text('-----BEGIN PRIVATE KEY-----\nprivate\n')
    args = SimpleNamespace(directory=tmp_path / 'install', ca_file=certificate)
    with pytest.raises(admin.Failure, match='invalid'), patch.object(admin, 'run') as run:
        admin.prepare(args, {})
    run.assert_not_called()
    assert not args.directory.exists()


def test_changed_ca_prevents_start(tmp_path):
    (tmp_path / 'compose.json').write_text('{}')
    (tmp_path / 'trusted-ca.pem').write_text('unexpected')
    (tmp_path / 'installation.json').write_text(json.dumps({
        'compose_sha256': admin.digest(tmp_path / 'compose.json'),
        'ca_sha256': 'trusted-hash', 'isolated': True,
    }))
    with patch.object(admin, 'compose') as compose, pytest.raises(admin.Failure, match='configurationChanged'):
        admin.up(tmp_path)
    compose.assert_not_called()


def test_unlisted_bytecode_in_source_kit_is_rejected(tmp_path):
    import build_source

    source = tmp_path / 'source/backend/app/__pycache__'
    source.mkdir(parents=True)
    (source / 'main.cpython-312.pyc').write_bytes(b'untrusted')
    (tmp_path / 'build-manifest.json').write_text('{"files":{}}')
    with patch.object(build_source, 'ROOT', tmp_path), patch.object(build_source, 'run') as run, pytest.raises(ValueError, match='UNEXPECTED_BUILD_FILES'):
        build_source.main()
    run.assert_not_called()


@pytest.mark.parametrize("subnet", ["172.31.58.0/27", "172.31.58.0/24", "172.30.0.0/16"])
def test_dynamic_addresses_never_claim_max_senders(subnet):
    import ipaddress

    _, config, _ = policy("test-addresses", subnet, [])
    dynamic = ipaddress.ip_network(config["networks"]["default"]["ipam"]["config"][0]["ip_range"])
    for name in ("api", "delivery"):
        address = config["services"][name]["networks"]["default"]["ipv4_address"]
        assert ipaddress.ip_address(address) not in dynamic
    assert dynamic.num_addresses >= 16
