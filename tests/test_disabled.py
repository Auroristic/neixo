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
    from cogs.admin import AdminCog

    lvl_mock = SimpleNamespace(_leveling_disabled=False)
    cog = AdminCog(None)
    cog.bot = SimpleNamespace(
        get_command=lambda n: bot_commands.get(n) if bot_commands else None,
        get_cog=lambda n: lvl_mock if n == "Leveling" else None,
    )
    cog.lvl_mock = lvl_mock
    cog.disable_cmd.cog = cog
    cog.enable_cmd.cog = cog
    return cog


async def test_disable_unknown_command():
    cog = _make_cog()
    ctx = FakeCtx()
    await cog.disable_cmd(ctx, command="nonexistent")
    assert ctx.sent[0][0][0] == "-# no command called that"


async def test_disable_exempt_commands():
    for name in ("disable", "enable", "help"):
        cog = _make_cog()
        ctx = FakeCtx()
        await cog.disable_cmd(ctx, command=name)
        assert ctx.sent[0][0][0] == "-# can't disable that"


async def test_disable_requires_permission():
    cog = _make_cog({"rtop": SimpleNamespace(qualified_name="rtop")})
    ctx = FakeCtx(owner_id=99, author_id=123)  # author is not owner, not creator
    await cog.disable_cmd(ctx, command="rtop")
    assert ctx.sent[0][0][0] == "-# no perms"


async def test_disable_subcommand_full_name():
    from utils import get_disabled_commands

    cmd = SimpleNamespace(qualified_name="welcome test")
    cog = _make_cog({"welcome test": cmd})
    ctx = FakeCtx(owner_id=99, author_id=99)
    await cog.disable_cmd(ctx, command="welcome test")
    assert get_disabled_commands(111) == ["welcome test"]
    assert ctx.reacted == ["<:redlotus:1263556248310386800>"]


async def test_enable_removes_from_list():
    from utils import add_disabled_command, get_disabled_commands

    add_disabled_command(111, "rtop")
    cmd = SimpleNamespace(qualified_name="rtop")
    cog = _make_cog({"rtop": cmd})
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
    cog.lvl_mock._leveling_disabled = False
    await cog.disable_cmd(ctx, command="level")
    assert cog.lvl_mock._leveling_disabled is True
    from utils import _db
    with _db() as conn:
        row = conn.execute("SELECT disabled FROM leveling_settings WHERE guild_id = '__global__'").fetchone()
        assert row and row[0] == 1


async def test_enable_level_routes_to_system_flag():
    cog = _make_cog()
    ctx = FakeCtx(author_id=123456789)
    cog.lvl_mock._leveling_disabled = True
    await cog.enable_cmd(ctx, command="level")
    assert cog.lvl_mock._leveling_disabled is False
    from utils import _db
    with _db() as conn:
        row = conn.execute("SELECT disabled FROM leveling_settings WHERE guild_id = '__global__'").fetchone()
        assert row and row[0] == 0


async def test_disable_whitelist_allowed():
    from utils import CONFIG_FILE, get_disabled_commands, save_json

    save_json(CONFIG_FILE, {str(111): {"whitelist": ["42"]}})
    cog = _make_cog({"rtop": SimpleNamespace(qualified_name="rtop")})
    ctx = FakeCtx(owner_id=99, author_id=42)  # not owner/creator — whitelisted
    await cog.disable_cmd(ctx, command="rtop")
    assert get_disabled_commands(111) == ["rtop"]
    assert ctx.reacted == ["<:redlotus:1263556248310386800>"]
    assert not ctx.sent


async def test_disable_alias_stores_canonical_name():
    import inspect
    import discord
    from discord.ext import commands
    from cogs.admin import AdminCog

    bot = commands.Bot(command_prefix='.', intents=discord.Intents.all())
    res = bot.add_cog(AdminCog(bot))
    if inspect.isawaitable(res):
        await res
    cog = bot.get_cog('Admin')
    ctx = FakeCtx(owner_id=99, author_id=99)
    cmd_mock = SimpleNamespace(qualified_name="levelleaderboard")
    bot.get_command = lambda n: cmd_mock if n in ("levelleaderboard", "llb") else None
    await cog.disable_cmd(ctx, command="llb")
    from utils import get_disabled_commands

    assert get_disabled_commands(111) == ['levelleaderboard']
    assert ctx.reacted == ["<:redlotus:1263556248310386800>"]
