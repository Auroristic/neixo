import threading
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def isolate_data_dir(monkeypatch, tmp_path):
    import utils as u

    u._local = threading.local()
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    monkeypatch.setattr('utils.DATA_DIR', str(data_dir))
    for cache_attr in ['_config_cache', '_ignore_cache', '_dm_whitelist_cache', '_aliases_cache']:
        monkeypatch.setattr(f'utils.{cache_attr}', None)
    for time_attr in ['_config_cache_time', '_ignore_cache_time', '_dm_whitelist_cache_time', '_aliases_cache_time']:
        monkeypatch.setattr(f'utils.{time_attr}', 0)
    u.init_files()


def test_disabled_empty_by_default():
    from utils import get_disabled_commands

    assert get_disabled_commands(111) == []


def test_add_and_get_disabled():
    from utils import add_disabled_command, get_disabled_commands

    add_disabled_command(111, 'welcome test')
    add_disabled_command(111, 'rtop')
    add_disabled_command(222, 'snipe')
    assert get_disabled_commands(111) == ['welcome test', 'rtop']
    assert get_disabled_commands(222) == ['snipe']


def test_remove_disabled():
    from utils import add_disabled_command, get_disabled_commands, remove_disabled_command

    add_disabled_command(111, 'rtop')
    remove_disabled_command(111, 'rtop')
    assert get_disabled_commands(111) == []
    remove_disabled_command(111, 'never-added')
    assert get_disabled_commands(111) == []


def test_is_command_disabled_exact_and_prefix():
    from utils import add_disabled_command, is_command_disabled

    add_disabled_command(111, 'welcome')
    assert is_command_disabled(111, 'welcome') is True
    assert is_command_disabled(111, 'welcome test') is True
    assert is_command_disabled(111, 'welcometest') is False
    assert is_command_disabled(222, 'welcome') is False


def test_is_command_disabled_multiword_name():
    from utils import add_disabled_command, is_command_disabled

    add_disabled_command(111, 'welcome test')
    assert is_command_disabled(111, 'welcome test') is True
    assert is_command_disabled(111, 'welcome test setup') is True
    assert is_command_disabled(111, 'welcome') is False
