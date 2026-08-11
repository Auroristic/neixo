# Snipe Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the single `.snipe` command into a three-command snipe suite — deleted messages (`.snipe`/`.s`), edited messages (`.esnipe`/`.es`), removed reactions (`.rsnipe`/`.rs`) — each keeping 50 snapshots per channel, with all attachments rendered.

**Architecture:** All state lives in per-channel `deque` dicts (`_deleted`, `_edited`, `_reactions`) on the `Snipe` cog, populated by the three listeners and read by the three commands. Snapshot building and embed rendering are extracted into pure module-level helpers so they're testable with `SimpleNamespace` fakes (the existing test-suite pattern in `tests/test_critical_bugfixes.py`).

**Tech Stack:** Python 3.11+, discord.py, pytest.

**Spec:** `docs/superpowers/specs/2026-08-11-snipe-suite-design.md`

## Global Constraints

- `_SNIPE_KEEP = 50` — all three deques keep 50 snapshots per channel.
- Deque keys are `(guild_id, channel_id)` tuples.
- Embed color via `get_embed_color(ctx.guild.id)`, timestamps in UTC.
- Every command uses the `help_meta` decorator (section "Fun") — copy the existing `.snipe` help meta style.
- Every command has a guild-only guard: `if ctx.guild is None: return await ctx.send("-# this command only works in servers.")`
- Every command validates `n >= 1` → `"-# \`n\` has to be at least 1"`.
- Empty-state messages: "nothing deleted here. yet." / "nothing edited here. yet." / "no reactions removed here. yet."
- Listeners skip bot-authored messages/users (`message.author.bot` / `user.bot`).
- The existing test conventions: plain pytest functions, `from types import SimpleNamespace`, import the cog module directly, instantiate `Snipe(None)` (constructor only stores bot). `pytest-asyncio` is in auto mode (`asyncio_mode = "auto"` in `pyproject.toml`), so listener tests are `async def` and `await` the listener methods directly — no `@pytest.mark.asyncio` decorator needed.

---

### Task 1: Message snipe — `.s` alias, 50-snapshot history, all attachments

**Files:**
- Modify: `cogs/snipe.py` (constructor, `_SNIPE_KEEP`, `on_message_delete`, `snipe` command; add `_add_attachments` and `_render_deleted_embed` helpers)
- Test: `tests/test_snipe.py` (create)

**Interfaces:**
- Produces:
  - `_SNIPE_KEEP = 50` (module constant)
  - `Snipe(bot)` with attribute `self._deleted: dict[tuple[int, int], deque]`
  - `Snipe.on_message_delete(message)` — async listener, appends snapshot to `self._deleted[(guild.id, channel.id)]`
  - `_add_attachments(embed: discord.Embed, images: list[str], sticker: str | None, label: str) -> None` — sets embed image to first image (or sticker), adds remaining images as embed fields `[attachment N](url)`
  - `_render_deleted_embed(snap: dict, guild_id: int, n: int) -> discord.Embed`
  - Command `snipe(self, ctx, n: int = 1)` with `aliases=["s"]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_snipe.py`:

```python
from types import SimpleNamespace
from collections import deque

import discord

from cogs.snipe import Snipe, _SNIPE_KEEP, _render_deleted_embed, _add_attachments


def _fake_message(**overrides):
    author = SimpleNamespace(
        bot=False,
        display_name="alice",
        display_avatar=SimpleNamespace(url="https://avatar"),
    )
    base = dict(
        guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=2),
        author=author,
        content="hello world",
        attachments=[SimpleNamespace(url="https://img1"), SimpleNamespace(url="https://img2")],
        stickers=[],
        reference=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_snipe_keep_is_50():
    assert _SNIPE_KEEP == 50


async def test_deleted_snapshot_captures_content_author_and_images():
    cog = Snipe(None)
    await cog.on_message_delete(_fake_message())

    (key, dq), = cog._deleted.items()
    assert key == (1, 2)
    snap = dq[0]
    assert snap["content"] == "hello world"
    assert snap["author"].display_name == "alice"
    assert snap["attachments"] == ["https://img1", "https://img2"]


async def test_deleted_snapshot_ignores_bot_messages():
    cog = Snipe(None)
    msg = _fake_message()
    msg.author = SimpleNamespace(bot=True, display_name="bob")
    await cog.on_message_delete(msg)
    assert cog._deleted == {}


async def test_deleted_deque_caps_at_50():
    cog = Snipe(None)
    for i in range(55):
        await cog.on_message_delete(_fake_message(content=f"msg {i}"))
    dq = cog._deleted[(1, 2)]
    assert len(dq) == 50
    assert dq[0]["content"] == "msg 54"
    assert dq[-1]["content"] == "msg 5"


def test_embed_shows_all_attachments():
    snap = {
        "content": "hello world",
        "author": SimpleNamespace(display_name="alice"),
        "avatar": "https://avatar",
        "attachments": ["https://img1", "https://img2", "https://img3"],
        "sticker": None,
        "deleted_at": 1_700_000_000,
        "reference": None,
    }
    embed = _render_deleted_embed(snap, 1, 1)
    assert embed.image.url == "https://img1"
    field_urls = [f.value for f in embed.fields]
    assert any("https://img2" in v for v in field_urls)
    assert any("https://img3" in v for v in field_urls)


def test_embed_sticker_wins_over_first_attachment():
    snap = {
        "content": "hi",
        "author": SimpleNamespace(display_name="alice"),
        "avatar": "https://avatar",
        "attachments": ["https://img1"],
        "sticker": "https://sticker.png",
        "deleted_at": 1_700_000_000,
        "reference": None,
    }
    embed = _render_deleted_embed(snap, 1, 1)
    assert embed.image.url == "https://sticker.png"


def test_add_attachments_single_image_no_fields():
    embed = discord.Embed()
    _add_attachments(embed, ["https://img1"], None, "image")
    assert embed.image.url == "https://img1"
    assert len(embed.fields) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_snipe.py -v`
Expected: FAIL — `_SNIPE_KEEP`, `_render_deleted_embed`, `_add_attachments` do not exist yet.

- [ ] **Step 3: Implement**

Rewrite `cogs/snipe.py`:

```python
"""
cogs/snipe.py  —  deleted / edited message and removed-reaction sniping
"""

import logging
import time
from collections import deque
from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils import get_embed_color, help_meta

log = logging.getLogger(__name__)

COG_META = {
    "category": "fun",
    "label": "Fun",
    "desc": "Snipe deleted/edited messages and removed reactions.",
}

_SNIPE_KEEP = 50  # per-channel history


def _add_attachments(embed: discord.Embed, images: list[str], sticker: str | None, label: str) -> None:
    """Set the embed image to the first attachment (or sticker), and add the rest as fields."""
    if sticker:
        embed.set_image(url=sticker)
    elif images:
        embed.set_image(url=images[0])
    for i, url in enumerate(images[1:], start=2):
        embed.add_field(name=f"{label} {i}", value=f"[attachment {i}]({url})", inline=False)


def _render_deleted_embed(snap: dict, guild_id: int, n: int) -> discord.Embed:
    author = snap["author"]
    embed = discord.Embed(
        description=snap["content"] or "*no text*",
        color=get_embed_color(guild_id),
        timestamp=datetime.fromtimestamp(snap["deleted_at"], tz=timezone.utc),
    )
    embed.set_author(name=author.display_name, icon_url=snap["avatar"])
    embed.set_footer(
        text=f"snipe #{n} · deleted {int(time.time() - snap['deleted_at'])}s ago"
    )
    if snap["reference"]:
        embed.add_field(name="replying to", value=snap["reference"][:150], inline=False)
    _add_attachments(embed, snap["attachments"], snap["sticker"], "attachment")
    return embed


class Snipe(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._deleted: dict[tuple[int, int], deque] = {}

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        key = (message.guild.id, message.channel.id)
        snap = {
            "content": message.content,
            "author": message.author,
            "avatar": message.author.display_avatar.url,
            "attachments": [a.url for a in message.attachments],
            "sticker": message.stickers[0].url if message.stickers else None,
            "deleted_at": time.time(),
            "reference": message.reference.resolved.content if (
                message.reference and message.reference.resolved
            ) else None,
        }
        dq = self._deleted.setdefault(key, deque(maxlen=_SNIPE_KEEP))
        dq.appendleft(snap)

    @commands.command(name="snipe", aliases=["s"])
    @help_meta(
        usage="`.snipe [n]`",
        desc="Shows the last deleted message in this channel (or the nth one).",
        section="Fun",
        examples=[".snipe", ".s 2"],
        params=[
            {
                "name": "n",
                "type": "int",
                "required": False,
                "desc": "Which deleted message to show, 1 = most recent.",
            },
        ],
        note="only works while the message is still in my memory (up to 50 per channel).",
    )
    async def snipe(self, ctx: commands.Context, n: int = 1):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if n < 1:
            return await ctx.send("-# `n` has to be at least 1")
        key = (ctx.guild.id, ctx.channel.id)
        dq = self._deleted.get(key)
        if not dq or n > len(dq):
            return await ctx.send("-# nothing deleted here. yet.")
        snap = dq[n - 1]
        await ctx.send(embed=_render_deleted_embed(snap, ctx.guild.id, n))


async def setup(bot: commands.Bot):
    await bot.add_cog(Snipe(bot))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_snipe.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 5: Manual sanity check — verify `.s` alias registers**

Run: `python -c "import discord; from discord.ext import commands; import cogs.snipe; bot = commands.Bot(command_prefix='.', intents=discord.Intents.all()); import asyncio; asyncio.run(bot.add_cog(cogs.snipe.Snipe(bot))); print(bot.get_command('s').name); print(bot.get_command('snipe').name)"`
Expected: prints `snipe` twice, no exception.

- [ ] **Step 6: Commit**

```bash
git add cogs/snipe.py tests/test_snipe.py
git commit -m "snipe: .s alias, 50-snapshot history, all attachments rendered"
```

---

### Task 2: Editsnipe — `.esnipe` / `.es`

**Files:**
- Modify: `cogs/snipe.py` (add `self._edited`, `on_message_edit`, `_render_edit_embed`, `esnipe` command)
- Test: `tests/test_snipe.py` (extend)

**Interfaces:**
- Consumes: `_SNIPE_KEEP` (Task 1), `_add_attachments` (Task 1), `get_embed_color`, `help_meta`
- Produces:
  - `Snipe.on_message_edit(before, after)` — async listener storing snapshots on `self._edited`
  - `_render_edit_embed(snap: dict, guild_id: int, n: int) -> discord.Embed`
  - Command `esnipe(self, ctx, n: int = 1)` with `aliases=["es"]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_snipe.py`:

```python
def _fake_edit_pair(content_before="old text", content_after="new text", **overrides):
    author = SimpleNamespace(
        bot=False,
        display_name="alice",
        display_avatar=SimpleNamespace(url="https://avatar"),
    )
    before = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=2),
        author=author,
        content=content_before,
        attachments=[SimpleNamespace(url="https://img1")],
        stickers=[],
        jump_url="https://discord.com/channels/1/2/9",
    )
    after = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=2),
        author=author,
        content=content_after,
    )
    for attr, value in overrides.items():
        setattr(before, attr, value)
    return before, after


async def test_edit_snapshot_stores_before_content():
    from cogs.snipe import Snipe

    cog = Snipe(None)
    before, after = _fake_edit_pair()
    await cog.on_message_edit(before, after)

    snap = cog._edited[(1, 2)][0]
    assert snap["content"] == "old text"
    assert snap["jump_url"] == "https://discord.com/channels/1/2/9"


async def test_edit_snapshot_skips_noop_edits():
    cog = Snipe(None)
    before, after = _fake_edit_pair(content_before="same", content_after="same")
    await cog.on_message_edit(before, after)
    assert cog._edited == {}


async def test_edit_snapshot_skips_bot_messages():
    cog = Snipe(None)
    before, after = _fake_edit_pair()
    before.author = SimpleNamespace(bot=True, display_name="botty")
    after.author = before.author
    await cog.on_message_edit(before, after)
    assert cog._edited == {}


def test_edit_embed_shows_before_content_and_image():
    from cogs.snipe import _render_edit_embed

    snap = {
        "content": "old text",
        "author": SimpleNamespace(display_name="alice"),
        "avatar": "https://avatar",
        "attachments": ["https://img1"],
        "sticker": None,
        "edited_at": 1_700_000_000,
        "jump_url": "https://discord.com/channels/1/2/9",
    }
    embed = _render_edit_embed(snap, 1, 1)
    assert "old text" in embed.description
    assert embed.image.url == "https://img1"
    assert "edit #1" in embed.footer.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_snipe.py -v`
Expected: FAIL — `_edited`, `on_message_edit`, `_render_edit_embed` missing.

- [ ] **Step 3: Implement**

In `cogs/snipe.py` add the `_render_edit_embed` helper (above the class):

```python
def _render_edit_embed(snap: dict, guild_id: int, n: int) -> discord.Embed:
    author = snap["author"]
    embed = discord.Embed(
        description=snap["content"] or "*no text*",
        color=get_embed_color(guild_id),
        timestamp=datetime.fromtimestamp(snap["edited_at"], tz=timezone.utc),
    )
    embed.set_author(name=author.display_name, icon_url=snap["avatar"])
    embed.set_footer(
        text=f"edit #{n} · edited {int(time.time() - snap['edited_at'])}s ago"
    )
    embed.add_field(
        name="message",
        value=f"[jump]({snap['jump_url']})",
        inline=False,
    )
    _add_attachments(embed, snap["attachments"], snap["sticker"], "attachment")
    return embed
```

In `Snipe.__init__` add: `self._edited: dict[tuple[int, int], deque] = {}`

Add the listener (after `on_message_delete`):

```python
    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.guild is None or after.author.bot:
            return
        if before.content == after.content:
            return
        key = (after.guild.id, after.channel.id)
        snap = {
            "content": before.content,
            "author": before.author,
            "avatar": before.author.display_avatar.url,
            "attachments": [a.url for a in before.attachments],
            "sticker": before.stickers[0].url if before.stickers else None,
            "edited_at": time.time(),
            "jump_url": before.jump_url,
        }
        dq = self._edited.setdefault(key, deque(maxlen=_SNIPE_KEEP))
        dq.appendleft(snap)
```

Add the command (after `snipe`):

```python
    @commands.command(name="esnipe", aliases=["es"])
    @help_meta(
        usage="`.esnipe [n]`",
        desc="Shows the last edited message's pre-edit content in this channel (or the nth one).",
        section="Fun",
        examples=[".esnipe", ".es 2"],
        params=[
            {
                "name": "n",
                "type": "int",
                "required": False,
                "desc": "Which edit to show, 1 = most recent.",
            },
        ],
        note="only the pre-edit content is shown — the after-content is still in chat.",
    )
    async def esnipe(self, ctx: commands.Context, n: int = 1):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if n < 1:
            return await ctx.send("-# `n` has to be at least 1")
        key = (ctx.guild.id, ctx.channel.id)
        dq = self._edited.get(key)
        if not dq or n > len(dq):
            return await ctx.send("-# nothing edited here. yet.")
        snap = dq[n - 1]
        await ctx.send(embed=_render_edit_embed(snap, ctx.guild.id, n))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_snipe.py -v`
Expected: all 11 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add cogs/snipe.py tests/test_snipe.py
git commit -m "snipe: add editsnipe (.esnipe/.es) for pre-edit content"
```

---

### Task 3: Reactionsnipe — `.rsnipe` / `.rs`

**Files:**
- Modify: `cogs/snipe.py` (add `self._reactions`, `_reaction_emoji_str`, `on_reaction_remove`, `_render_reaction_embed`, `rsnipe` command)
- Test: `tests/test_snipe.py` (extend)

**Interfaces:**
- Consumes: `_SNIPE_KEEP` (Task 1), `get_embed_color`, `help_meta`
- Produces:
  - `_reaction_emoji_str(emoji) -> str` — module-level; returns the emoji unchanged if already a str, else `<a:name:id>` if `animated`, else `<:name:id>`
  - `Snipe.on_reaction_remove(reaction, user)` — async listener storing snapshots on `self._reactions`
  - `_render_reaction_embed(snap: dict, guild_id: int, n: int) -> discord.Embed`
  - Command `rsnipe(self, ctx, n: int = 1)` with `aliases=["rs"]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_snipe.py`:

```python
def test_reaction_emoji_str_unicode_passthrough():
    from cogs.snipe import _reaction_emoji_str

    assert _reaction_emoji_str("🚀") == "🚀"


def test_reaction_emoji_str_custom_static():
    from cogs.snipe import _reaction_emoji_str

    emoji = SimpleNamespace(name="lotus", id=456, animated=False)
    assert _reaction_emoji_str(emoji) == "<:lotus:456>"


def test_reaction_emoji_str_custom_animated():
    from cogs.snipe import _reaction_emoji_str

    emoji = SimpleNamespace(name="dance", id=123, animated=True)
    assert _reaction_emoji_str(emoji) == "<a:dance:123>"


def _fake_reaction_remove(emoji="🚀", reactor_bot=False, **overrides):
    message = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=2),
        id=9,
        jump_url="https://discord.com/channels/1/2/9",
        author=SimpleNamespace(display_name="alice"),
    )
    reactor = SimpleNamespace(
        bot=reactor_bot,
        display_name="bob",
        display_avatar=SimpleNamespace(url="https://avatar"),
    )
    reaction = SimpleNamespace(emoji=emoji, message=message)
    for attr, value in overrides.items():
        setattr(reaction, attr, value)
    return reaction, reactor


async def test_reaction_snapshot_stores_emoji_reactor_and_message():
    cog = Snipe(None)
    reaction, reactor = _fake_reaction_remove()
    await cog.on_reaction_remove(reaction, reactor)

    snap = cog._reactions[(1, 2)][0]
    assert snap["emoji"] == "🚀"
    assert snap["reactor"].display_name == "bob"
    assert snap["message_author"].display_name == "alice"
    assert snap["message_jump_url"] == "https://discord.com/channels/1/2/9"


async def test_reaction_snapshot_skips_bot_reactors():
    cog = Snipe(None)
    reaction, reactor = _fake_reaction_remove(reactor_bot=True)
    await cog.on_reaction_remove(reaction, reactor)
    assert cog._reactions == {}


def test_reaction_embed_shows_reactor_emoji_and_target():
    from cogs.snipe import _render_reaction_embed

    snap = {
        "emoji": "<a:dance:123>",
        "reactor": SimpleNamespace(display_name="bob"),
        "reactor_avatar": "https://avatar",
        "message_author": SimpleNamespace(display_name="alice"),
        "message_jump_url": "https://discord.com/channels/1/2/9",
        "removed_at": 1_700_000_000,
    }
    embed = _render_reaction_embed(snap, 1, 1)
    assert "<a:dance:123>" in embed.description
    assert "bob" in embed.description
    assert "alice" in embed.description
    assert "rsnipe #1" in embed.footer.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_snipe.py -v`
Expected: FAIL — `_reaction_emoji_str`, `_reactions`, `on_reaction_remove`, `_render_reaction_embed` missing.

- [ ] **Step 3: Implement**

In `cogs/snipe.py` add these module-level helpers (above the class):

```python
def _reaction_emoji_str(emoji) -> str:
    """Render a reaction emoji as a display string (unicode passthrough, custom → <:name:id>)."""
    if isinstance(emoji, str):
        return emoji
    if emoji.animated:
        return f"<a:{emoji.name}:{emoji.id}>"
    return f"<:{emoji.name}:{emoji.id}>"


def _render_reaction_embed(snap: dict, guild_id: int, n: int) -> discord.Embed:
    embed = discord.Embed(
        description=(
            f"removed {snap['emoji']} on **{snap['message_author'].display_name}**'s "
            f"[message]({snap['message_jump_url']})"
        ),
        color=get_embed_color(guild_id),
        timestamp=datetime.fromtimestamp(snap["removed_at"], tz=timezone.utc),
    )
    embed.set_author(name=snap["reactor"].display_name, icon_url=snap["reactor_avatar"])
    embed.set_footer(
        text=f"rsnipe #{n} · removed {int(time.time() - snap['removed_at'])}s ago"
    )
    return embed
```

In `Snipe.__init__` add: `self._reactions: dict[tuple[int, int], deque] = {}`

Add the listener (after `on_message_edit`):

```python
    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction: discord.Reaction, user: discord.User):
        message = reaction.message
        if message.guild is None or user.bot:
            return
        key = (message.guild.id, message.channel.id)
        snap = {
            "emoji": _reaction_emoji_str(reaction.emoji),
            "reactor": user,
            "reactor_avatar": user.display_avatar.url,
            "message_author": message.author,
            "message_jump_url": message.jump_url,
            "removed_at": time.time(),
        }
        dq = self._reactions.setdefault(key, deque(maxlen=_SNIPE_KEEP))
        dq.appendleft(snap)
```

Add the command (after `esnipe`):

```python
    @commands.command(name="rsnipe", aliases=["rs"])
    @help_meta(
        usage="`.rsnipe [n]`",
        desc="Shows the last removed reaction in this channel (or the nth one).",
        section="Fun",
        examples=[".rsnipe", ".rs 2"],
        params=[
            {
                "name": "n",
                "type": "int",
                "required": False,
                "desc": "Which removed reaction to show, 1 = most recent.",
            },
        ],
        note="only works while the reaction removal is still in my memory (up to 50 per channel).",
    )
    async def rsnipe(self, ctx: commands.Context, n: int = 1):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if n < 1:
            return await ctx.send("-# `n` has to be at least 1")
        key = (ctx.guild.id, ctx.channel.id)
        dq = self._reactions.get(key)
        if not dq or n > len(dq):
            return await ctx.send("-# no reactions removed here. yet.")
        snap = dq[n - 1]
        await ctx.send(embed=_render_reaction_embed(snap, ctx.guild.id, n))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_snipe.py -v`
Expected: all 18 tests PASS.

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `pytest -v`
Expected: all tests pass (existing suite + new snipe tests).

- [ ] **Step 6: Commit**

```bash
git add cogs/snipe.py tests/test_snipe.py
git commit -m "snipe: add reactionsnipe (.rsnipe/.rs) for removed reactions"
```
