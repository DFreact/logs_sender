import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'release'))
import admin
import upgrade


def source(**patches):
    return {'release': '0.11.0-offline.1', 'schema': '0011',
            'data_fingerprint': {'users': ['1', '42', '81']}, **patches}


def test_only_explicit_forward_upgrade_is_allowed():
    target = {'release': '0.12.2-offline.1', 'schema': '0012'}
    upgrade.validate_transition(source(), target)
    for saved in (source(schema='0012'), source(release='0.12.1-offline.1'),
                  source(data_fingerprint={}), source(data_fingerprint=None)):
        with pytest.raises(admin.Failure, match='damaged'):
            upgrade.validate_transition(saved, target)
    with pytest.raises(admin.Failure, match='damaged'):
        upgrade.validate_transition(source(), {'release': '0.12.2-offline.1', 'schema': '0013'})


def test_fingerprint_omits_migration_version_and_does_not_export_rows():
    calls = []
    def execute(statement):
        calls.append(statement)
        return 'alembic_version\nusers\nevents' if len(calls) == 1 else '5|123|456'
    assert upgrade.fingerprint(execute) == {'users': ['5', '123', '456'],
                                           'events': ['5', '123', '456']}
    assert all('row_to_json' in value and 'sum(' in value for value in calls[1:])
    assert all('string_agg' not in value for value in calls)


def test_incompatible_backup_is_rejected_before_preparing_destination(tmp_path):
    from types import SimpleNamespace
    with patch.object(admin, 'verified', return_value=source(schema='0012')), \
         patch.object(admin, 'prepare') as prepare, \
         pytest.raises(admin.Failure, match='damaged'):
        upgrade.restore(SimpleNamespace(backup=tmp_path),
                        {'release': '0.12.2-offline.1', 'schema': '0012'})
    prepare.assert_not_called()
