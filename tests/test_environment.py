"""Inspector configuration parsing never needs a browser or model API."""
import os
from unittest.mock import patch

import pytest

from laya_ultrafast.demo import load_environment


@pytest.fixture(autouse=True)
def isolated_environment():
    # Keep both configuration behavior and failure diagnostics independent of host credentials.
    with patch.dict(os.environ, {}, clear=True):
        yield


def test_env_quotes_spaces_comments_and_literal_values(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in ('AUDIT_QUOTED', 'AUDIT_SPACED', 'AUDIT_LITERAL'):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / '.env').write_text('\ufeffAUDIT_QUOTED="demo value"\nAUDIT_SPACED = value # comment\n'
                                   "export AUDIT_LITERAL='${AUDIT_QUOTED}'\n", encoding='utf-8')
    load_environment()
    assert os.environ.get('AUDIT_QUOTED') == 'demo value'
    assert os.environ.get('AUDIT_SPACED') == 'value'
    assert os.environ.get('AUDIT_LITERAL') == '${AUDIT_QUOTED}'


def test_existing_environment_wins_and_missing_file_is_ok(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('AUDIT_QUOTED', 'existing')
    load_environment()
    (tmp_path / '.env').write_text('AUDIT_QUOTED="file value"\n')
    load_environment()
    assert os.environ.get('AUDIT_QUOTED') == 'existing'
