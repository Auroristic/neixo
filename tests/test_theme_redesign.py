from __future__ import annotations

import unittest.mock as mock
import pytest
from cogs.theme_helpers import _resolve_role_slot


def test_resolve_role_slot_numeric():
    role_map = {
        "owner": 1001,
        "bots": 1002,
        "co owner": 1003,
        "head of security": 1004,
        "admin": 1005,
    }

    mock_guild = mock.MagicMock()
    mock_role = mock.MagicMock()
    mock_guild.get_role.return_value = mock_role

    # Test #1
    slot, role, rest = _resolve_role_slot(mock_guild, role_map, "1 true dragon")
    assert slot == "owner"
    assert rest == "true dragon"
    mock_guild.get_role.assert_called_with(1001)

    # Test #3 (multi-word slot via index)
    slot, role, rest = _resolve_role_slot(mock_guild, role_map, "3 Vice King")
    assert slot == "co owner"
    assert rest == "Vice King"
    mock_guild.get_role.assert_called_with(1003)

    # Test with '#' prefix
    slot, role, rest = _resolve_role_slot(mock_guild, role_map, "#4 Chief")
    assert slot == "head of security"
    assert rest == "Chief"

    # Test with dot
    slot, role, rest = _resolve_role_slot(mock_guild, role_map, "1. true dragon")
    assert slot == "owner"
    assert rest == "true dragon"


def test_resolve_role_slot_multiword_and_case():
    role_map = {
        "owner": 1001,
        "bots": 1002,
        "co owner": 1003,
        "head of security": 1004,
    }

    mock_guild = mock.MagicMock()

    # Case insensitive single word
    slot, role, rest = _resolve_role_slot(mock_guild, role_map, "OWNER God Emperor")
    assert slot == "owner"
    assert rest == "God Emperor"

    # Multi-word slot matching
    slot, role, rest = _resolve_role_slot(mock_guild, role_map, "co owner True Dragon")
    assert slot == "co owner"
    assert rest == "True Dragon"

    # Multi-word 3-word slot
    slot, role, rest = _resolve_role_slot(mock_guild, role_map, "head of security Defender")
    assert slot == "head of security"
    assert rest == "Defender"

    # Slot only
    slot, role, rest = _resolve_role_slot(mock_guild, role_map, "co owner")
    assert slot == "co owner"
    assert rest == ""


def test_resolve_role_slot_mention():
    role_map = {
        "owner": 1001,
        "bots": 1002,
    }

    mock_guild = mock.MagicMock()

    slot, role, rest = _resolve_role_slot(mock_guild, role_map, "<@&1001> Emperor")
    assert slot == "owner"
    assert rest == "Emperor"


def test_resolve_role_slot_invalid():
    role_map = {
        "owner": 1001,
        "bots": 1002,
    }

    mock_guild = mock.MagicMock()

    slot, role, rest = _resolve_role_slot(mock_guild, role_map, "999 invalid")
    assert slot is None
    assert rest == "999 invalid"

    slot, role, rest = _resolve_role_slot(mock_guild, role_map, "nonexistent slot")
    assert slot is None


def test_theme_cog_fast_shortcuts():
    import cogs.theme

    cog = cogs.theme.ThemeCog(mock.MagicMock())
    commands_dict = {cmd.name: cmd for cmd in cog.get_commands()}

    expected_shortcuts = [
        "tr", "tri", "troles", "tmap", "trevert",
        "tfont", "tfonts", "tprefix", "tapply", "tsave", "tsetup", "treset"
    ]

    for sc in expected_shortcuts:
        assert sc in commands_dict, f"Shortcut .{sc} should be registered in ThemeCog"

    # Check .theme subcommands and aliases
    theme_group = commands_dict["theme"]
    sub_names = {sub.name for sub in theme_group.commands}
    assert "role" in sub_names
    assert "roles" in sub_names
    assert "setrole" in sub_names
    assert "roleicon" in sub_names
