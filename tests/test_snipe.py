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
