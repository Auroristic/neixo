# Quality Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generic per-guild `.disable`/`.enable`, `.welcome test` preview, `.digest now` on-demand stats, and a full help-metadata overhaul across every cog.

**Architecture:** Disabled commands live in a per-guild JSON (`data/disabled_commands.json`) with utils helpers and a bot-level global check in `neixo.py` (same pattern as `_channel_rule_check`). The leveling `.disable`/`.enable` commands expand in place with a greedy `*, command` arg. `.welcome test` reuses the existing welcome render pipeline via an extracted `_fetch_member_art` helper. `.digest now` reuses the weekly digest pipeline via a non-mutating `_baselines(update=False)` mode and an extracted `_build_digest_file`. The help sweep is metadata-only across all cogs, gated by a decorator-completeness checker script.

**Tech Stack:** Python 3.11+, discord.py, pytest (+ pytest-asyncio auto mode), PIL for card rendering.

**Spec:** `docs/superpowers/specs/2026-08-11-quality-sweep-design.md`

## Global Constraints

- Tests run on the remote server `muixo` — the local machine has no Python env. Loop per test step: commit → `git push origin main` → `ssh muixo "cd /home/ubuntu/neiXO && git pull && venv/bin/python -m pytest <file> -v"`.
- Bot voice (AGENTS.md): all bot text lowercase, no trailing periods, success react `<:pinklotus:1263556545686405170>`, failure/disable react `<:redlotus:1263556248310386800>`.
- `.disable`/`.enable` signature: greedy `async def disable_cmd(self, ctx, *, command: str = None)` (keyword-only, captures multi-word subcommand names like `welcome test`).
- Level dispatch: `command == "level"` routes to the existing bot-global creator-only system flag FIRST; everything else goes through the per-guild path. `.enable level` same branch.
- Disabled-list output format: entries as `.<qualified name>` with dot prefix and spaces — `-# disabled here: .welcome test, .lb emojis`.
- Disable/enable permission: guild owner OR creator OR whitelisted (whitelist from `get_config()[str(guild_id)]["whitelist"]`).
- Exempt commands: `disable`, `enable`, `help`.
- Disabled check matching: `qualified == name or qualified.startswith(name + " ")` (disabling a group blocks its subcommands).
- `.welcome test`: `@commands.cooldown(1, 10, commands.BucketType.user)`.
- `.digest now`: computes with `update=False` (baselines never written); sends to invoking channel; admin only.
- Help sweep: **metadata only** — no behavior, signature, alias, or permission changes. Quality bar in Task 5's step 1 (verbatim from spec).
- Every task's code must keep the full test suite green (run full suite on muixo after each task's own tests).

---

### Task 1: Disabled-commands storage helpers + global check

**Files:**
- Modify: `utils.py` (new helpers near the cmd-channel-rules section, ~line 480)
- Modify: `neixo.py` (new `_disabled_command_check` method + registration in `setup_hook`)
- Test: `tests/test_disabled.py` (create)

**Interfaces:**
- Produces:
  - `utils._disabled_file() -> str` — returns `f"{DATA_DIR}/disabled_commands.json"` computed at call time (so tests can monkeypatch `utils.DATA_DIR`)
  - `utils.get_disabled_commands(guild_id: int | str) -> list[str]`
  - `utils.add_disabled_command(guild_id: int | str, name: str) -> None`
  - `utils.remove_disabled_command(guild_id: int | str, name: str) -> None`
  - `utils.is_command_disabled(guild_id: int | str, qualified: str) -> bool` — `True` when `qualified == name or qualified.startswith(name + " ")` for any disabled name
  - `Neixo._disabled_command_check(ctx) -> bool` in `neixo.py`, registered via `self.add_check` in `setup_hook` right after the `_channel_rule_check` registration block

- [ ] **Step 1: Write the failing tests**

Create `tests/test_disabled.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ssh muixo "cd /home/ubuntu/neiXO && git pull && venv/bin/python -m pytest tests/test_disabled.py -v"`
Expected: FAIL — helpers don't exist yet (need to push first: the test file must exist on muixo, so commit+push before the run per the global loop).

- [ ] **Step 3: Implement the utils helpers**

In `utils.py`, after the cmd-channel-rules section, add:

```python
# ── Disabled commands (per-guild) ─────────────────────────────

def _disabled_file() -> str:
    return f"{DATA_DIR}/disabled_commands.json"


def get_disabled_commands(guild_id: int | str) -> list[str]:
    return list((load_json(_disabled_file()) or {}).get(str(guild_id), []))


def add_disabled_command(guild_id: int | str, name: str) -> None:
    state = load_json(_disabled_file()) or {}
    names = state.setdefault(str(guild_id), [])
    if name not in names:
        names.append(name)
    save_json(_disabled_file(), state)


def remove_disabled_command(guild_id: int | str, name: str) -> None:
    state = load_json(_disabled_file()) or {}
    names = state.get(str(guild_id), [])
    if name in names:
        names.remove(name)
    if not names:
        state.pop(str(guild_id), None)
    save_json(_disabled_file(), state)


def is_command_disabled(guild_id: int | str, qualified: str) -> bool:
    for name in get_disabled_commands(guild_id):
        if qualified == name or qualified.startswith(name + " "):
            return True
    return False
```

- [ ] **Step 4: Implement the global check in neixo.py**

In `neixo.py`, add this method to the bot class (right after `_channel_rule_check`):

```python
    async def _disabled_command_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None or not ctx.command:
            return True
        from utils import is_command_disabled

        qualified = (ctx.command.qualified_name or ctx.command.name or "").lower().strip()
        if not qualified:
            return True
        if is_command_disabled(ctx.guild.id, qualified):
            try:
                await ctx.send(f"-# `.{qualified}` is disabled.")
            except discord.HTTPException:
                pass
            return False
        return True
```

In `setup_hook`, immediately after the `self.add_check(self._channel_rule_check)` block (around line 246), add:

```python
        # Register global disabled-command check.
        try:
            self.add_check(self._disabled_command_check)  # type: ignore[arg-type]
        except Exception:
            logging.exception("failed to register disabled-command check")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `ssh muixo "cd /home/ubuntu/neiXO && git pull && venv/bin/python -m pytest tests/test_disabled.py -v"`
Expected: 5/5 PASS.

- [ ] **Step 6: Run the full suite**

Run: `ssh muixo "cd /home/ubuntu/neiXO && venv/bin/python -m pytest -q"`
Expected: no regressions (all previous tests still pass).

- [ ] **Step 7: Commit**

```bash
git add utils.py neixo.py tests/test_disabled.py
git commit -m "disable: per-guild disabled-commands store + global check"
```

---

### Task 2: `.disable` / `.enable` commands (leveling.py)

**Files:**
- Modify: `cogs/leveling.py` (replace `disable_cmd` and `enable_cmd`, ~lines 465-523)
- Test: `tests/test_disabled.py` (extend)

**Interfaces:**
- Consumes: `utils.get_disabled_commands`, `utils.add_disabled_command`, `utils.remove_disabled_command` (Task 1), `utils.get_aliases`, `utils.get_config`, `utils.is_creator`
- Produces: `Leveling.disable_cmd(self, ctx, *, command: str = None)` and `Leveling.enable_cmd(self, ctx, *, command: str = None)` — greedy keyword-only args; `level` special case; per-guild path with owner/creator/whitelist permission; exempt `disable`/`enable`/`help`; no-arg → list output.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_disabled.py`:

```python
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
    return cog


def test_disable_unknown_command():
    cog = _make_cog()
    ctx = FakeCtx()
    await cog.disable_cmd(ctx, command="nonexistent")
    assert ctx.sent[0][0][0] == "-# no command called that"


def test_disable_exempt_commands():
    cog = _make_cog({})
    for name in ("disable", "enable", "help"):
        ctx = FakeCtx()
        await cog.disable_cmd(ctx, command=name)
        assert ctx.sent[0][0][0] == "-# can't disable that", name


def test_disable_requires_permission():
    cog = _make_cog({"rtop": SimpleNamespace(qualified_name="rtop")})
    ctx = FakeCtx(owner_id=99, author_id=777)  # not owner, not creator, not whitelisted
    await cog.disable_cmd(ctx, command="rtop")
    assert ctx.sent[0][0][0] == "-# no perms"


def test_disable_subcommand_full_name():
    cog = _make_cog({"welcome test": SimpleNamespace(qualified_name="welcome test")})
    ctx = FakeCtx(owner_id=99, author_id=99)  # guild owner
    await cog.disable_cmd(ctx, command="welcome test")
    from utils import get_disabled_commands

    assert "welcome test" in get_disabled_commands(111)
    assert ctx.reacted == ["<:redlotus:1263556248310386800>"]


def test_enable_removes_from_list():
    from utils import add_disabled_command, get_disabled_commands

    add_disabled_command(111, "rtop")
    cog = _make_cog({"rtop": SimpleNamespace(qualified_name="rtop")})
    ctx = FakeCtx(owner_id=99, author_id=99)
    await cog.enable_cmd(ctx, command="rtop")
    assert get_disabled_commands(111) == []
    assert ctx.reacted == ["<:pinklotus:1263556545686405170>"]


def test_disable_no_args_lists():
    from utils import add_disabled_command

    add_disabled_command(111, "welcome test")
    add_disabled_command(111, "rtop")
    cog = _make_cog()
    ctx = FakeCtx(owner_id=99, author_id=99)
    await cog.disable_cmd(ctx)
    assert ctx.sent[0][0][0] == "-# disabled here: .welcome test, .rtop"


def test_disable_level_routes_to_system_flag():
    cog = _make_cog()
    ctx = FakeCtx(author_id=123456789)  # CREATOR_ID from conftest
    await cog.disable_cmd(ctx, command="level")
    assert cog._leveling_disabled is True
    assert ctx.sent[0][0][0].startswith("❌")


def test_enable_level_routes_to_system_flag():
    cog = _make_cog()
    ctx = FakeCtx(author_id=123456789)
    await cog.enable_cmd(ctx, command="level")
    assert cog._leveling_disabled is False
    assert ctx.sent[0][0][0].startswith("✅")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ssh muixo "cd /home/ubuntu/neiXO && git pull && venv/bin/python -m pytest tests/test_disabled.py -v"`
Expected: FAIL — current signature is `system: str` positional and no per-guild logic.

- [ ] **Step 3: Implement**

Replace both methods in `cogs/leveling.py` with:

```python
    @commands.command(name="disable")
    @help_meta(
        section="Leveling",
        usage="`.disable <command>`  ·  `.disable`",
        desc="Disables a command for this server (owner/creator/whitelist only).",
        examples=[".disable rtop", ".disable welcome test"],
        params=[
            {"name": "command", "type": "str", "required": False,
             "desc": "Command to disable (subcommands like `welcome test` work). Omit to list disabled commands. `level` disables the leveling system."},
        ],
        note="guild owner, creator, or whitelisted users only. `.enable <command>` turns it back on.",
    )
    async def disable_cmd(self, ctx, *, command: str = None):
        """Disable a command for this guild, or the leveling system."""
        # 1. level = the existing bot-global leveling system flag — CREATOR-ONLY.
        #    Pre-existing behavior (leveling.py today checks only is_creator);
        #    the flag is bot-global so only the creator toggles it. Deliberately
        #    differs from the per-guild path's owner/creator/whitelist permission.
        if command == "level":
            if not is_creator(ctx.author.id):
                return await ctx.send("creator only")
            self._leveling_disabled = True
            from utils import _db
            with _db() as conn:
                conn.execute(
                    "INSERT INTO leveling_settings (guild_id, notifications_enabled, disabled) "
                    "VALUES ('__global__', 1, 1) "
                    "ON CONFLICT(guild_id) DO UPDATE SET disabled = 1"
                )
            return await ctx.send("❌ Leveling system **disabled**. Use `.enable level` to re-enable.")

        # 2. generic per-guild command disable
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if not await self._can_disable(ctx):
            return await ctx.send("-# no perms")
        if command is None:
            return await ctx.send(self._disabled_list_text(ctx))
        cmd_name = command.lower().lstrip(".")
        from utils import get_aliases
        cmd_name = get_aliases().get(cmd_name, cmd_name)
        if cmd_name in ("disable", "enable", "help"):
            return await ctx.send("-# can't disable that")
        if self.bot.get_command(cmd_name) is None:
            return await ctx.send("-# no command called that")
        from utils import add_disabled_command
        add_disabled_command(ctx.guild.id, cmd_name)
        await ctx.message.add_reaction("<:redlotus:1263556248310386800>")

    @commands.command(name="enable")
    @help_meta(
        section="Leveling",
        usage="`.enable <command>`  ·  `.enable`",
        desc="Re-enables a disabled command for this server (owner/creator/whitelist only).",
        examples=[".enable rtop"],
        params=[
            {"name": "command", "type": "str", "required": False,
             "desc": "Command to re-enable. Omit to list disabled commands. `level` re-enables the leveling system."},
        ],
        note="guild owner, creator, or whitelisted users only.",
    )
    async def enable_cmd(self, ctx, *, command: str = None):
        """Re-enable a command for this guild, or the leveling system."""
        # 1. level = the existing bot-global leveling system flag — CREATOR-ONLY.
        #    Pre-existing behavior (leveling.py today checks only is_creator);
        #    the flag is bot-global so only the creator toggles it. Deliberately
        #    differs from the per-guild path's owner/creator/whitelist permission.
        if command == "level":
            if not is_creator(ctx.author.id):
                return await ctx.send("creator only")
            self._leveling_disabled = False
            from utils import _db
            with _db() as conn:
                conn.execute(
                    "INSERT INTO leveling_settings (guild_id, notifications_enabled, disabled) "
                    "VALUES ('__global__', 1, 0) "
                    "ON CONFLICT(guild_id) DO UPDATE SET disabled = 0"
                )
            return await ctx.send("✅ Leveling system **enabled**.")

        # 2. generic per-guild command enable
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if not await self._can_disable(ctx):
            return await ctx.send("-# no perms")
        if command is None:
            return await ctx.send(self._disabled_list_text(ctx))
        cmd_name = command.lower().lstrip(".")
        from utils import get_aliases
        cmd_name = get_aliases().get(cmd_name, cmd_name)
        if cmd_name in ("disable", "enable", "help"):
            return await ctx.send("-# can't enable that")
        if self.bot.get_command(cmd_name) is None:
            return await ctx.send("-# no command called that")
        from utils import remove_disabled_command
        remove_disabled_command(ctx.guild.id, cmd_name)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")

    async def _can_disable(self, ctx) -> bool:
        if is_creator(ctx.author.id):
            return True
        if ctx.author.id == ctx.guild.owner_id:
            return True
        from utils import get_config
        whitelist = (get_config() or {}).get(str(ctx.guild.id), {}).get("whitelist", [])
        return str(ctx.author.id) in {str(uid) for uid in whitelist}

    def _disabled_list_text(self, ctx) -> str:
        from utils import get_disabled_commands
        names = get_disabled_commands(ctx.guild.id)
        if not names:
            return "-# nothing disabled here."
        return "-# disabled here: " + ", ".join(f".{n}" for n in names)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ssh muixo "cd /home/ubuntu/neiXO && git pull && venv/bin/python -m pytest tests/test_disabled.py -v"`
Expected: 14/14 PASS (5 Task-1 + 9 Task-2).

- [ ] **Step 5: Run the full suite**

Run: `ssh muixo "cd /home/ubuntu/neiXO && venv/bin/python -m pytest -q"`
Expected: no regressions.

- [ ] **Step 6: Commit**

```bash
git add cogs/leveling.py tests/test_disabled.py
git commit -m "disable: generic per-guild .disable/.enable (level special case kept)"
```

---

### Task 3: `.welcome test`

**Files:**
- Modify: `cogs/welcome.py` (extract `_fetch_member_art`, add `welcome_test` subcommand)
- Test: `tests/test_welcome.py` (create)

**Interfaces:**
- Produces:
  - `welcome._fetch_member_art(member) -> tuple[bytes | None, bytes | None]` — module-level async helper: fetches member avatar + guild banner via aiohttp (the exact logic currently inlined in `on_member_join`), returns `(avatar_bytes, banner_bytes)`
  - `Welcome.welcome_test(self, ctx)` — subcommand on `welcome` group with `@commands.cooldown(1, 10, commands.BucketType.user)` and `help_meta` (usage `.welcome test`, desc "Previews the welcome card with your own avatar.", section General, note "anyone can preview — does not change settings.")

- [ ] **Step 1: Write the failing tests**

Create `tests/test_welcome.py`:

```python
from types import SimpleNamespace

import discord
import pytest
from discord.ext import commands


@pytest.mark.asyncio
async def test_welcome_test_registered_with_cooldown():
    import cogs.welcome

    bot = commands.Bot(command_prefix='.', intents=discord.Intents.all())
    await bot.add_cog(cogs.welcome.Welcome(bot))
    cmd = bot.get_command('welcome test')
    assert cmd is not None
    # the @commands.cooldown decorator on the plain function becomes
    # Command._buckets (a CooldownMapping) at command creation
    assert getattr(cmd, '_buckets', None) is not None


@pytest.mark.asyncio
async def test_welcome_test_help_meta_present():
    import cogs.welcome
    from utils import get_help_meta

    bot = commands.Bot(command_prefix='.', intents=discord.Intents.all())
    await bot.add_cog(cogs.welcome.Welcome(bot))
    meta = get_help_meta(bot.get_command('welcome test'))
    assert meta is not None
    assert meta['section'] == 'General'
    assert meta['usage'].startswith('.welcome test')


@pytest.mark.asyncio
async def test_fetch_member_art_returns_avatar_and_banner():
    from cogs.welcome import _fetch_member_art

    member = SimpleNamespace(
        display_avatar=SimpleNamespace(url=''),
        guild=SimpleNamespace(banner=None),
    )
    avatar_bytes, banner_bytes = await _fetch_member_art(member)
    assert avatar_bytes is None
    assert banner_bytes is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ssh muixo "cd /home/ubuntu/neiXO && git pull && venv/bin/python -m pytest tests/test_welcome.py -v"`
Expected: FAIL — `welcome test` and `_fetch_member_art` don't exist.

- [ ] **Step 3: Implement**

In `cogs/welcome.py`:

1. Add the module-level helper (after the `_render_welcome_card` function):

```python
async def _fetch_member_art(member) -> tuple[bytes | None, bytes | None]:
    """Fetch a member's avatar and their guild's banner bytes (best-effort).

    The outer try only guards session creation; per-URL failures are
    tolerated inside _get so one bad URL never blanks the other.
    """
    avatar_bytes = banner_bytes = None
    urls = [member.display_avatar.url]
    if member.guild.banner:
        urls.append(member.guild.banner.url)
    try:
        async with aiohttp.ClientSession() as s:
            async def _get(url):
                try:
                    async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                        return await r.read() if r.status == 200 else None
                except Exception:
                    return None
            results = await asyncio.gather(*[_get(u) for u in urls])
    except Exception:
        return None, None
    avatar_bytes = results[0]
    banner_bytes = results[1] if len(results) > 1 else None
    return avatar_bytes, banner_bytes
```

2. Replace the fetch block in `on_member_join` (lines ~183-196) with:

```python
        avatar_bytes, banner_bytes = await _fetch_member_art(member)
```

3. Add the subcommand after `welcome_status`:

```python
    @welcome.command(name="test")
    @commands.cooldown(1, 10, commands.BucketType.user)
    @help_meta(
        usage="`.welcome test`",
        desc="Previews the welcome card with your own avatar.",
        section="General",
        examples=[".welcome test"],
        params=[],
        note="anyone can preview — does not change settings. 1 use per 10 seconds.",
    )
    async def welcome_test(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        avatar_bytes, banner_bytes = await _fetch_member_art(ctx.author)
        buf = await asyncio.to_thread(
            _render_welcome_card,
            avatar_bytes,
            banner_bytes,
            ctx.guild.name,
            ctx.author.display_name,
            ctx.guild.member_count or len(ctx.guild.members),
        )
        await ctx.send(file=discord.File(fp=buf, filename="welcome.png"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ssh muixo "cd /home/ubuntu/neiXO && git pull && venv/bin/python -m pytest tests/test_welcome.py -v"`
Expected: 3/3 PASS.

- [ ] **Step 5: Run the full suite**

Run: `ssh muixo "cd /home/ubuntu/neiXO && venv/bin/python -m pytest -q"`
Expected: no regressions.

- [ ] **Step 6: Commit**

```bash
git add cogs/welcome.py tests/test_welcome.py
git commit -m "welcome: .welcome test preview card with cooldown"
```

---

### Task 4: `.digest now`

**Files:**
- Modify: `cogs/digest.py` (`_baselines` gains `update: bool = True`; extract `_build_digest_file`; add `digest_now` subcommand)
- Test: `tests/test_digest.py` (create)

**Interfaces:**
- Consumes: `_render_digest_card`, `_fmt_vc`, `_week_start_iso`, `_load_digest` (existing)
- Produces:
  - `Digest._baselines(self, gid_str: str, conf: dict, update: bool = True) -> tuple[dict, dict, dict]` — when `update=False`, computes deltas but never mutates `conf['baselines']`
  - `Digest._build_digest_file(self, guild, conf: dict, update: bool) -> io.BytesIO | None` — the compute+render body extracted from `_run_digest` (icon fetch, row building, card render); returns the PNG buffer
  - `Digest.digest_now(self, ctx)` — `digest` group subcommand: admin only, guild-only, `_build_digest_file(guild, conf, update=False)`, sends file to invoking channel, `help_meta` usage `.digest now`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_digest.py`:

```python
from types import SimpleNamespace

import pytest


def _fake_conn(rows):
    class FakeConn:
        def execute(self, sql, params=()):
            return self
        def fetchall(self):
            return rows
    return FakeConn()


def _baselines_conf():
    return {
        "channel_id": "5",
        "baselines": {"10": {"msgs": 100, "vc": 0, "bumps": 0}},
        "member_base": 20,
        "last_run_iso": "",
    }


def test_baselines_update_false_does_not_mutate(monkeypatch):
    from cogs.digest import Digest

    monkeypatch.setattr('cogs.serverstats._get_conn', lambda: _fake_conn([(10, 150)]))
    monkeypatch.setattr('cogs.bumps._get_conn', lambda: _fake_conn([(10, 3)]))

    cog = Digest(None)
    conf = _baselines_conf()
    delta_msgs, delta_vc, delta_bumps = cog._baselines('111', conf, update=False)

    assert delta_msgs == {10: 50}
    assert delta_bumps == {10: 3}
    # baselines must NOT have been advanced to current totals
    assert conf['baselines']['10']['msgs'] == 100
    assert conf['baselines']['10']['bumps'] == 0


def test_baselines_update_true_advances(monkeypatch):
    from cogs.digest import Digest

    monkeypatch.setattr('cogs.serverstats._get_conn', lambda: _fake_conn([(10, 150)]))
    monkeypatch.setattr('cogs.bumps._get_conn', lambda: _fake_conn([]))

    cog = Digest(None)
    conf = _baselines_conf()
    cog._baselines('111', conf, update=True)

    assert conf['baselines']['10']['msgs'] == 150


def test_digest_now_registered():
    import discord
    from discord.ext import commands

    import cogs.digest

    bot = commands.Bot(command_prefix='.', intents=discord.Intents.all())
    import asyncio
    asyncio.run(bot.add_cog(cogs.digest.Digest(bot)))
    assert bot.get_command('digest now') is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ssh muixo "cd /home/ubuntu/neiXO && git pull && venv/bin/python -m pytest tests/test_digest.py -v"`
Expected: FAIL — `update` param and `digest now` don't exist.

- [ ] **Step 3: Implement**

In `cogs/digest.py`:

1. Change `_baselines` signature to `def _baselines(self, gid_str: str, conf: dict, update: bool = True) -> tuple[dict, dict, dict]:` and wrap the "update baselines to current totals" block (the three `for uid, cur in ...` loops) in `if update:`.

2. Extract `_build_digest_file` from `_run_digest` — replace the compute+render portion of `_run_digest` (from `delta_msgs, ... = self._baselines(...)` through the `buf = await asyncio.to_thread(...)` block) with:

```python
    async def _build_digest_file(self, guild: discord.Guild, conf: dict, update: bool) -> io.BytesIO | None:
        gid_str = str(guild.id)
        delta_msgs, delta_vc, delta_bumps = self._baselines(gid_str, conf, update=update)

        def _name(uid):
            m = guild.get_member(uid)
            return m.display_name if m else f"<@{uid}>"

        chatters = sorted(delta_msgs.items(), key=lambda x: -x[1])[:5]
        vc_rows = sorted(delta_vc.items(), key=lambda x: -x[1])[:5]
        bumper_rows = sorted(delta_bumps.items(), key=lambda x: -x[1])[:5]

        member_growth = (guild.member_count or len(guild.members)) - conf.get("member_base", guild.member_count or len(guild.members))

        icon_bytes = None
        try:
            if guild.icon:
                async with aiohttp.ClientSession() as s:
                    async with s.get(guild.icon.url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                        if r.status == 200:
                            icon_bytes = await r.read()
        except Exception:
            pass

        week_label = f"week of {datetime.now(timezone.utc).strftime('%b %d')}"
        return await asyncio.to_thread(
            _render_digest_card,
            icon_bytes,
            guild.name,
            week_label,
            sum(delta_msgs.values()),
            _fmt_vc(sum(delta_vc.values())),
            sum(delta_bumps.values()),
            member_growth,
            [(i + 1, _name(uid), n) for i, (uid, n) in enumerate(chatters)],
            [(i + 1, _name(uid), n // 60) for i, (uid, n) in enumerate(vc_rows)],
            [(i + 1, _name(uid), n) for i, (uid, n) in enumerate(bumper_rows)],
        )
```

3. Rewrite `_run_digest` to use it:

```python
    async def _run_digest(self, guild: discord.Guild, conf: dict):
        buf = await self._build_digest_file(guild, conf, update=True)
        if buf is None:
            return
        channel = guild.get_channel(int(conf["channel_id"]))
        if channel is not None:
            try:
                await channel.send(file=discord.File(fp=buf, filename="digest.png"))
            except discord.HTTPException:
                pass
```

4. Add the subcommand after `digest_status`:

```python
    @digest.command(name="now")
    @help_meta(
        usage="`.digest now`",
        desc="Sends the current week's digest card on demand.",
        section="General",
        examples=[".digest now"],
        params=[],
        note="admin only. shows the same stats as the sunday card — does not affect the scheduled run.",
    )
    async def digest_now(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if not await self._admin(ctx):
            return await ctx.send("-# admin only")
        conf = _load_digest().get(str(ctx.guild.id)) or {}
        buf = await self._build_digest_file(ctx.guild, conf, update=False)
        if buf is None:
            return
        await ctx.send(file=discord.File(fp=buf, filename="digest.png"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ssh muixo "cd /home/ubuntu/neiXO && git pull && venv/bin/python -m pytest tests/test_digest.py -v"`
Expected: 3/3 PASS.

- [ ] **Step 5: Run the full suite**

Run: `ssh muixo "cd /home/ubuntu/neiXO && venv/bin/python -m pytest -q"`
Expected: no regressions.

- [ ] **Step 6: Commit**

```bash
git add cogs/digest.py tests/test_digest.py
git commit -m "digest: .digest now on-demand stats card (non-mutating baselines)"
```

---

### Task 5: Help sweep — music.py (45 commands)

**Files:**
- Modify: `cogs/music.py` (help_meta metadata only)
- Test: none (verification via checker script + full suite)

**Interfaces:**
- Consumes: the quality bar below
- Produces: complete, accurate `help_meta` on every music command; zero `@commands.command`/`@commands.group` without an adjacent `@help_meta`

- [ ] **Step 1: Read the quality bar (verbatim from the spec — applies to every sweep task)**

1. **`desc`:** one line, ≤ 100 characters, all lowercase, starts with a verb or short phrase ("shows...", "sends...", "turns...").
2. **`usage`:** always present, starts with `.`, matches the real signature (greedy `*` args shown as `<text>`).
3. **`section`:** always set; uses the cog's existing section label when the cog already has one.
4. **`examples`:** only where genuinely useful — ≤ 3, with a real `.` prefix; omit for trivial commands.
5. **`params`:** one entry per documented argument; `required` reflects the actual default (no default = True, has default = False).
6. **`note`:** restrictions (admin/owner/staff/cooldown), or `{user}`-style placeholder explanations when relevant; omit when nothing to add.
7. **Every command decorated with `@help_meta`** — zero missing.

Also: metadata only — do NOT change behavior, aliases, signatures, or permission flags. Do NOT add cooldowns or checks. Only edit strings inside `@help_meta(...)` calls (and add `@help_meta(...)` decorators where missing, filling in usage/desc/section from the command's actual signature and current docstring).

- [ ] **Step 2: Sweep `cogs/music.py`**

Work through all 45 commands. For each: read the command signature + current help_meta + docstring, then write/rewrite the help_meta to the quality bar. Where a command lacks `@help_meta`, add it directly below the `@commands.command(...)` decorator line with accurate values. Keep the bot voice in desc/note text. Commands already meeting the bar: leave them.

- [ ] **Step 3: Run the checker script**

From the repo root run:

```bash
python3 - <<'EOF'
import pathlib
import re

pat_cmd = re.compile(r'^\s*@[\w.]+\.(command|group)\(')
pat_meta = re.compile(r'^\s*@help_meta\(')
bad = []
total = 0
for f in sorted(pathlib.Path('cogs').glob('*.py')):
    lines = f.read_text().splitlines()
    for i, ln in enumerate(lines):
        if pat_cmd.match(ln):
            total += 1
            window = lines[max(0, i - 3):i + 4]
            if not any(pat_meta.match(l) for l in window):
                bad.append(f'{f}:{i + 1}: {ln.strip()}')
print(f'checked {total} command decorators')
print('\n'.join(bad) if bad else 'OK: every command decorated with @help_meta')
EOF
```

Expected: `OK: every command decorated with @help_meta`.

- [ ] **Step 4: Run the full suite on muixo**

Run: `ssh muixo "cd /home/ubuntu/neiXO && git pull && venv/bin/python -m pytest -q"`
Expected: no regressions.

- [ ] **Step 5: Commit**

```bash
git add cogs/music.py
git commit -m "help: overhaul music command metadata"
```

---

### Task 6: Help sweep — ai.py, gif_editor.py, admin.py, profile.py (43 commands)

**Files:**
- Modify: `cogs/ai.py`, `cogs/gif_editor.py`, `cogs/admin.py`, `cogs/profile.py` (help_meta metadata only)

**Interfaces:**
- Consumes: the quality bar (Task 5, Step 1 — read it from the plan if working this task in isolation)

- [ ] **Step 1: Sweep `cogs/ai.py` (13), `cogs/gif_editor.py` (11), `cogs/admin.py` (10), `cogs/profile.py` (9)**

Apply the quality bar exactly as in Task 5. Metadata only.

- [ ] **Step 2: Run the checker script**

Run the same heredoc as Task 5 Step 3.
Expected: `OK: every command decorated with @help_meta`.

- [ ] **Step 3: Run the full suite on muixo**

Run: `ssh muixo "cd /home/ubuntu/neiXO && git pull && venv/bin/python -m pytest -q"`
Expected: no regressions.

- [ ] **Step 4: Commit**

```bash
git add cogs/ai.py cogs/gif_editor.py cogs/admin.py cogs/profile.py
git commit -m "help: overhaul ai/gif_editor/admin/profile command metadata"
```

---

### Task 7: Help sweep — misc, leveling, reactions, snipe, serverstats, counting (29 commands)

**Files:**
- Modify: `cogs/misc.py`, `cogs/leveling.py`, `cogs/reactions.py`, `cogs/snipe.py`, `cogs/serverstats.py`, `cogs/counting.py` (help_meta metadata only)

**Interfaces:**
- Consumes: the quality bar (Task 5, Step 1)

- [ ] **Step 1: Sweep `cogs/misc.py` (8), `cogs/leveling.py` (7), `cogs/reactions.py` (5), `cogs/snipe.py` (3), `cogs/serverstats.py` (3), `cogs/counting.py` (3)**

Apply the quality bar exactly as in Task 5. Metadata only. The `snipe.py` commands were recently written to this bar in the earlier snipe-suite work — review them anyway and only leave them if they already meet every point of the bar.

- [ ] **Step 2: Run the checker script**

Run the same heredoc as Task 5 Step 3.
Expected: `OK: every command decorated with @help_meta`.

- [ ] **Step 3: Run the full suite on muixo**

Run: `ssh muixo "cd /home/ubuntu/neiXO && git pull && venv/bin/python -m pytest -q"`
Expected: no regressions.

- [ ] **Step 4: Commit**

```bash
git add cogs/misc.py cogs/leveling.py cogs/reactions.py cogs/snipe.py cogs/serverstats.py cogs/counting.py
git commit -m "help: overhaul misc/leveling/reactions/snipe/serverstats/counting metadata"
```

---

### Task 8: Help sweep — all remaining cogs (24 commands)

**Files:**
- Modify: `cogs/theme.py`, `cogs/reminders.py`, `cogs/giveaways.py`, `cogs/fun.py`, `cogs/welcome.py`, `cogs/vanity.py`, `cogs/userinfo.py`, `cogs/translate.py`, `cogs/reverse.py`, `cogs/playlists.py`, `cogs/milestones.py`, `cogs/imagine.py`, `cogs/help.py`, `cogs/embedmaker.py`, `cogs/digest.py`, `cogs/confessions.py`, `cogs/check.py`, `cogs/bumps.py`, `cogs/autoresponse.py`, `cogs/afk.py` (help_meta metadata only)

**Interfaces:**
- Consumes: the quality bar (Task 5, Step 1)

- [ ] **Step 1: Sweep all remaining cogs (24 commands total)**

Apply the quality bar exactly as in Task 5. Metadata only. `welcome.py` and `digest.py` commands were just written/updated to the bar — leave them unless clearly off.

- [ ] **Step 2: Run the checker script**

Run the same heredoc as Task 5 Step 3.
Expected: `OK: every command decorated with @help_meta`.

- [ ] **Step 3: Run the full suite on muixo**

Run: `ssh muixo "cd /home/ubuntu/neiXO && git pull && venv/bin/python -m pytest -q"`
Expected: no regressions.

- [ ] **Step 4: Commit**

```bash
git add cogs/theme.py cogs/reminders.py cogs/giveaways.py cogs/fun.py cogs/welcome.py cogs/vanity.py cogs/userinfo.py cogs/translate.py cogs/reverse.py cogs/playlists.py cogs/milestones.py cogs/imagine.py cogs/help.py cogs/embedmaker.py cogs/digest.py cogs/confessions.py cogs/check.py cogs/bumps.py cogs/autoresponse.py cogs/afk.py
git commit -m "help: overhaul remaining cog metadata"
```

---

### Task 9: AGENTS.md patch

**Files:**
- Modify: `.agents/AGENTS.md`

**Interfaces:**
- Consumes: quality bar (Task 5, Step 1)

- [ ] **Step 1: Add the help-metadata rule**

Append a new section to `.agents/AGENTS.md` after the existing "Embed Minimalism" section:

```markdown
### Command Help Metadata (REQUIRED)
Every new or modified command MUST ship complete `@help_meta` metadata:
- `usage`: starts with `.`, matches the real signature
- `desc`: one line, ≤ 100 chars, lowercase, says what the command does
- `section`: always set to the cog's existing section label
- `examples`: ≤ 3, only where genuinely useful
- `params`: one entry per documented argument, `required` matches the default
- `note`: restrictions (admin/owner/staff/cooldown) or placeholder explanations
When touching a command, verify `.help <command>` renders sensibly. Never leave a
command without `@help_meta` — the bot warns on missing metadata at startup.
Metadata changes only: never alter behavior, aliases, or signatures in a help sweep.
```

- [ ] **Step 2: Commit**

```bash
git add .agents/AGENTS.md
git commit -m "agents: require complete help_meta on every command"
```

---

### Task 10: Deploy

- [ ] **Step 1: Push and restart**

```bash
git push origin main
ssh muixo "cd /home/ubuntu/neiXO && git pull && pm2 restart neixo"
```

- [ ] **Step 2: Verify boot**

Run: `sleep 5 && ssh muixo "pm2 logs neixo --lines 40 --nostream 2>/dev/null | tail -20; pm2 status neixo"`
Expected: `Logged in:` line present, no traceback, status online.

- [ ] **Step 3: Verify no command is missing help_meta**

Run the checker script from Task 5 Step 3 (same heredoc, repo root on muixo or locally — it's pure file scanning).
Expected: `OK: every command decorated with @help_meta`.
