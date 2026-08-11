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


async def test_deleted_snapshot_captures_reply_content():
    cog = Snipe(None)
    msg = _fake_message()
    msg.reference = SimpleNamespace(resolved=SimpleNamespace(content="the original"))
    await cog.on_message_delete(msg)
    assert cog._deleted[(1, 2)][0]["reference"] == "the original"


async def test_deleted_snapshot_handles_deleted_reply():
    # resolved is a DeletedReferencedMessage (no .content attr) when the
    # replied-to message was itself deleted — must not raise.
    cog = Snipe(None)
    msg = _fake_message()
    msg.reference = SimpleNamespace(resolved=SimpleNamespace(message_id=9, channel_id=2, guild_id=1))
    await cog.on_message_delete(msg)
    assert cog._deleted[(1, 2)][0]["reference"] is None


def test_embed_sticker_wins_over_first_attachment():
    snap = {
        "content": "hi",
        "author": SimpleNamespace(display_name="alice"),
        "avatar": "https://avatar",
        "attachments": ["https://img1", "https://img2"],
        "sticker": "https://sticker.png",
        "deleted_at": 1_700_000_000,
        "reference": None,
    }
    embed = _render_deleted_embed(snap, 1, 1)
    assert embed.image.url == "https://sticker.png"
    # sticker took the image slot, so every attachment must still appear as a field
    field_urls = [f.value for f in embed.fields]
    assert any("https://img1" in v for v in field_urls)
    assert any("https://img2" in v for v in field_urls)


def test_add_attachments_single_image_no_fields():
    embed = discord.Embed()
    _add_attachments(embed, ["https://img1"], None, "image")
    assert embed.image.url == "https://img1"
    assert len(embed.fields) == 0


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
    assert embed.author.name == "bob"
    assert "alice" in embed.description
    assert "rsnipe #1" in embed.footer.text
