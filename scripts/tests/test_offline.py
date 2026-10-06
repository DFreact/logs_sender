"""Safety boundaries for destructive-adjacent operations; no Docker required."""

import io
import json
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "release"))
import admin
import storage


def archive(path, name, kind=tarfile.REGTYPE):
    with tarfile.open(path, "w:gz") as target:
        item = tarfile.TarInfo(name)
        item.type = kind
        item.linkname = "/tmp/outside"
        target.addfile(item, io.BytesIO())


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../outside", tarfile.REGTYPE),
        ("/tmp/outside", tarfile.REGTYPE),
        ("00/" + "0" * 32, tarfile.SYMTYPE),
        ("00/" + "0" * 32, tarfile.LNKTYPE),
        ("00/" + "0" * 32, tarfile.FIFOTYPE),
        ("00/../../outside", tarfile.REGTYPE),
    ],
)
def test_unsafe_archives_rejected(tmp_path, name, kind):
    path = tmp_path / "data.tar.gz"
    archive(path, name, kind)
    with pytest.raises(admin.Failure, match="damaged"):
        admin.validate_archive(path)


def test_backup_hash_and_exact_members(tmp_path):
    for name in admin.BACKUP_FILES:
        (tmp_path / name).write_bytes(b"test")
    admin.seal(tmp_path, {"format": 1, "kind": "backup"}, admin.BACKUP_FILES)
    admin.verified(tmp_path, "backup")
    (tmp_path / "channel_secret").write_bytes(b"changed")
    with pytest.raises(admin.Failure, match="damaged"):
        admin.verified(tmp_path, "backup")
    data = json.loads((tmp_path / "manifest.json").read_text())
    data["files"] = {"../outside": "fake"}
    (tmp_path / "manifest.json").write_text(json.dumps(data))
    with pytest.raises(admin.Failure, match="damaged"):
        admin.verified(tmp_path, "backup")


def test_restore_never_overwrites_storage(tmp_path):
    (tmp_path / "important").write_text("keep")
    with pytest.raises(ValueError, match="STORAGE_NOT_EMPTY"):
        storage.transfer("restore", tmp_path)
    assert (tmp_path / "important").read_text() == "keep"


def test_file_lock_prevents_overlap(tmp_path):
    with (
        admin.lock(tmp_path),
        pytest.raises(admin.Failure, match="busy"),
        admin.lock(tmp_path),
    ):
        pass


def test_existing_project_rejected_before_directory_created(tmp_path):
    args = SimpleNamespace(
        directory=tmp_path / "new",
        project="eventhub",
        http_port=8080,
        smtp_port=2525,
        subnet="172.31.54.0/24",
        isolated=True,
    )
    with (
        patch.object(admin, "run", return_value=b"existing\n"),
        pytest.raises(admin.Failure, match="exists"),
    ):
        admin.prepare(args, {})
    assert not args.directory.exists()


def test_failure_restarts_only_previously_running_writers(tmp_path):
    install, output = tmp_path / "install", tmp_path / "copy"
    install.mkdir()
    (install / "installation.json").write_text(
        json.dumps({"ready": True, "images": {}})
    )
    calls = []

    def compose(directory, *args, **kwargs):
        calls.append(args)
        if args[:1] == ("ps",):
            return b"api\npostgres\n" if len(calls) == 1 else b"postgres\n"
        if args[:1] == ("exec",):
            raise admin.Failure("failed")
        return b""

    with (
        patch.object(admin, "network_check"),
        patch.object(admin, "compose", side_effect=compose),
        pytest.raises(admin.Failure),
    ):
        admin.backup(install, output)
    assert calls[-1] == ("start", "--wait", "--wait-timeout", "180", "api")
    assert not (output / "manifest.json").exists()


def test_compose_does_not_inherit_hub_overrides(tmp_path, monkeypatch):
    (tmp_path / "installation.json").write_text('{"project":"test"}')
    monkeypatch.setenv("HUB_HTTP_PORT", "9999")
    monkeypatch.setenv("COMPOSE_FILE", "unexpected.yml")
    with patch.object(admin, "run", return_value=b"") as run:
        admin.compose(tmp_path, "ps")
    assert "HUB_HTTP_PORT" not in run.call_args.kwargs["env"]
    assert "COMPOSE_FILE" not in run.call_args.kwargs["env"]


def test_storage_round_trip_and_private_permissions(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "ab").mkdir()
    (source / "locks").mkdir()
    name = "ab" + "1" * 30
    payload = b"original\x00" * 150000
    (source / "ab" / name).write_bytes(payload)
    (source / "locks" / "ab").write_bytes(b"")
    stream = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=stream))
    storage.transfer("save", source)
    monkeypatch.setattr(
        sys, "stdin", SimpleNamespace(buffer=io.BytesIO(stream.getvalue()))
    )
    storage.transfer("restore", target)
    assert (target / "ab" / name).read_bytes() == payload
    assert (target / "ab" / name).stat().st_mode & 0o777 == 0o600


def test_wrong_shard_and_incomplete_manifest_rejected(tmp_path):
    assert not storage.valid("00/" + "ff" * 16)
    with pytest.raises(admin.Failure, match="damaged"):
        admin.verified(tmp_path, "backup")


def test_standard_parser_does_not_add_english_usage_prefix():
    parser = admin.Parser(usage=admin.MESSAGES["usage"] + admin.MESSAGES["adminUsage"])
    assert parser.format_usage().startswith("Использование: ")
    assert parser.format_help().startswith("Использование: ")
