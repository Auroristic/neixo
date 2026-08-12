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
    monkeypatch.setattr('utils.DB_FILE', str(data_dir / 'bot.db'))
    monkeypatch.setattr('utils._db_initialized', False)
    for cache_attr in ['_config_cache', '_ignore_cache', '_dm_whitelist_cache', '_aliases_cache']:
        monkeypatch.setattr(f'utils.{cache_attr}', None)
    for time_attr in ['_config_cache_time', '_ignore_cache_time', '_dm_whitelist_cache_time', '_aliases_cache_time']:
        monkeypatch.setattr(f'utils.{time_attr}', 0)
    u.init_files()
    # leveling_settings is created by Leveling.cog_load(), which tests never
    # call — create it here so the level-branch tests hit a real table.
    with u._db() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS leveling_settings ("
            "guild_id TEXT PRIMARY KEY, "
            "notifications_enabled INTEGER DEFAULT 1, "
            "disabled INTEGER DEFAULT 0)"
        )


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


class FakeCtx:
    def __init__(self, guild_id=111, owner_id=99, author_id=123456789, send_to=None):
        self.guild = SimpleNamespace(id=guild_id, owner_id=owner_id)
        self.author = SimpleNamespace(id=author_id)
        self.message = SimpleNamespace(add_reaction=self._react)
        self.sent = []
        self.reacted = []

    async def _react(self, emoji):
        self.reacted.append(emoji)

    async def send(self, *a, **k):
        self.sent.append((a, k))
        return self.sent[-1]


def _make_cog(bot_commands=None):
    from cogs.leveling import Leveling

    cog = Leveling(None)
    cog.bot = SimpleNamespace(get_command=lambda n: bot_commands.get(n) if bot_commands else None)
    # Cog.__new__ binds per-instance Command copies (discord.py 2.7) that are
    # not attached to a cog; bind them as add_cog would, so
    # cog.disable_cmd(ctx, ...) invokes the callback with the cog instance.
    cog.disable_cmd.cog = cog
    cog.enable_cmd.cog = cog
    return cog


async def test_disable_unknown_command():
    cog = _make_cog()
    ctx = FakeCtx()
    await cog.disable_cmd(ctx, command="nonexistent")
    assert ctx.sent[0][0][0] == "-# no command called that"


async def test_disable_exempt_commands():
    cog = _make_cog({})
    for name in ("disable", "enable", "help"):
        ctx = FakeCtx()
        await cog.disable_cmd(ctx, command=name)
        assert ctx.sent[0][0][0] == "-# can't disable that", name


async def test_disable_requires_permission():
    cog = _make_cog({"rtop": SimpleNamespace(qualified_name="rtop")})
    ctx = FakeCtx(owner_id=99, author_id=777)  # not owner, not creator, not whitelisted
    await cog.disable_cmd(ctx, command="rtop")
    assert ctx.sent[0][0][0] == "-# no perms"


async def test_disable_subcommand_full_name():
    cog = _make_cog({"welcome test": SimpleNamespace(qualified_name="welcome test")})
    ctx = FakeCtx(owner_id=99, author_id=99)  # guild owner
    await cog.disable_cmd(ctx, command="welcome test")
    from utils import get_disabled_commands

    assert "welcome test" in get_disabled_commands(111)
    assert ctx.reacted == ["<:redlotus:1263556248310386800>"]


async def test_enable_removes_from_list():
    from utils import add_disabled_command, get_disabled_commands

    add_disabled_command(111, "rtop")
    cog = _make_cog({"rtop": SimpleNamespace(qualified_name="rtop")})
    ctx = FakeCtx(owner_id=99, author_id=99)
    await cog.enable_cmd(ctx, command="rtop")
    assert get_disabled_commands(111) == []
    assert ctx.reacted == ["<:pinklotus:1263556545686405170>"]


async def test_disable_no_args_lists():
    from utils import add_disabled_command

    add_disabled_command(111, "welcome test")
    add_disabled_command(111, "rtop")
    cog = _make_cog()
    ctx = FakeCtx(owner_id=99, author_id=99)
    await cog.disable_cmd(ctx)
    assert ctx.sent[0][0][0] == "-# disabled here: .welcome test, .rtop"


async def test_disable_level_routes_to_system_flag():
    cog = _make_cog()
    ctx = FakeCtx(author_id=123456789)  # CREATOR_ID from conftest
    await cog.disable_cmd(ctx, command="level")
    assert cog._leveling_disabled is True
    assert ctx.sent[0][0][0].startswith("❌")


async def test_enable_level_routes_to_system_flag():
    cog = _make_cog()
    ctx = FakeCtx(author_id=123456789)
    await cog.enable_cmd(ctx, command="level")
    assert cog._leveling_disabled is False
    assert ctx.sent[0][0][0].startswith("✅")
