import ipaddress
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'release'))
import admin
import configuration
import host


@pytest.mark.parametrize('value', ['0.0.0.0/0', '127.0.0.0/24', '169.254.1.0/24', '::1/128', '172.31.54.1/24', '192.0.2.0/24'])
def test_invalid_private_subnet(value):
    with pytest.raises(ValueError):
        host.subnet(value)


def test_autoselect_and_conflict():
    with patch.object(host, 'occupied', return_value=[ipaddress.ip_network('172.31.54.0/23')]):
        with pytest.raises(ValueError, match='SUBNET_CONFLICT'):
            host.choose_subnet('172.31.54.0/24')
        assert host.choose_subnet() == '172.31.56.0/24'


def test_external_install_keeps_restart_dns_and_private_secrets(tmp_path):
    kit = tmp_path / 'kit'
    kit.mkdir()
    (kit / 'compose.json').write_text(json.dumps({'networks': {'default': {}}, 'services': {
        'api': {'healthcheck': {}, 'environment': {}},
        'web': {'ports': ['127.0.0.1:${HUB_HTTP_PORT}:8080'], 'healthcheck': {}},
    }}))
    (kit / 'settings.env.example').write_text('HUB_HTTP_PORT=8080\n')
    args = SimpleNamespace(directory=tmp_path / 'new', project='test-external', isolated=False,
                           subnet='172.31.54.0/24', http_port=8080, smtp_port=2525, dns=['10.1.1.53'])
    with patch.object(admin, 'HERE', kit), patch.object(admin, 'run', return_value=b''), patch.object(admin.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
        admin.prepare(args, {'images': {}, 'release': 'test', 'schema': '0012'})
    config = json.loads((args.directory / 'compose.json').read_text())
    state = json.loads((args.directory / 'installation.json').read_text())
    assert state['network_management'] == 'external' and 'network_policy' not in state
    assert config['networks']['default']['internal'] is False
    assert config['services']['api']['dns'] == ['10.1.1.53']
    assert config['services']['api']['restart'] == 'unless-stopped'
    assert 'ports' in config['services']['web']
    assert len(list((args.directory / '.secrets').iterdir())) == 6
    assert args.directory.stat().st_mode & 0o777 == 0o700


def test_failed_reconfiguration_restores_files_and_keeps_volumes(tmp_path):
    original = {'settings.env': 'HUB_DOCKER_SUBNET=172.31.54.0/24\nHUB_HTTP_PORT=8080\nHUB_SMTP_PORT=2525\n',
                'compose.json': '{"services":{"api":{}}}',
                'installation.json': '{"ready":true,"network_management":"external","project":"test"}'}
    for name, data in original.items():
        (tmp_path / name).write_text(data)
    args = SimpleNamespace(directory=tmp_path, subnet='172.31.55.0/24', http_port=None, smtp_port=None, dns=[])
    with patch.object(admin, 'network_check'), patch.object(configuration, 'choose_subnet', return_value=args.subnet), patch.object(admin, 'run'), patch.object(admin, 'compose') as compose, patch.object(admin, 'up', side_effect=[admin.Failure('failed'), None]), pytest.raises(admin.Failure, match='configureRolledBack'):
        configuration.configure(args)
    assert all((tmp_path / name).read_text() == data for name, data in original.items())
    assert all('--volumes' not in call.args and '-v' not in call.args for call in compose.call_args_list)


def test_subnet_conflict_does_not_stop_existing_installation(tmp_path):
    (tmp_path / 'installation.json').write_text('{"ready":true,"network_management":"external","project":"test"}')
    (tmp_path / 'settings.env').write_text('HUB_DOCKER_SUBNET=172.31.54.0/24\nHUB_HTTP_PORT=8080\nHUB_SMTP_PORT=2525\n')
    args = SimpleNamespace(directory=tmp_path, subnet='172.31.55.0/24', http_port=None, smtp_port=None, dns=[])
    with patch.object(admin, 'network_check'), patch.object(configuration, 'choose_subnet', side_effect=ValueError()), patch.object(admin, 'compose') as compose, pytest.raises(admin.Failure, match='subnetUnavailable'):
        configuration.configure(args)
    compose.assert_not_called()


@pytest.mark.parametrize('source_release,target_release,schema,images,accepted', [
    ('0.12.2-offline.1', '0.12.2-offline.2', '0012', {'image': 'same'}, True),
    ('0.12.2-offline.2', '0.12.2-offline.2', '0012', {'image': 'same'}, True),
    ('0.12.1-offline.1', '0.12.2-offline.2', '0012', {'image': 'same'}, False),
    ('0.12.2-offline.1', '0.12.2-offline.2', '0011', {'image': 'same'}, False),
    ('0.12.2-offline.1', '0.12.2-offline.2', '0012', {'image': 'other'}, False),
])
def test_copy_transition_requires_exact_schema_and_images(tmp_path, source_release, target_release, schema, images, accepted):
    args = SimpleNamespace(backup=tmp_path)
    saved = {'release': source_release, 'schema': schema, 'images': images}
    target = {'release': target_release, 'schema': '0012', 'images': {'image': 'same'}}
    with (
        patch.object(admin, 'verified', return_value=saved),
        patch.object(admin, 'validate_archive', side_effect=RuntimeError('accepted')) as archive,
        pytest.raises(RuntimeError if accepted else admin.Failure, match='accepted' if accepted else 'damaged'),
    ):
        admin.restore(args, target)
    assert archive.called == accepted


def test_autoselect_can_use_another_private_range():
    with patch.object(host, 'occupied', return_value=[ipaddress.ip_network('172.16.0.0/12')]):
        assert host.choose_subnet() == '10.250.54.0/24'
