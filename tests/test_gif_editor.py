from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import utils  # noqa: E402
from cogs.gif_editor import get_image_from_ctx  # noqa: E402


def _make_png_bytes(color=(255, 0, 0, 255), size=(10, 10)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _make_gif_bytes(color=(255, 0, 0, 255), size=(10, 10)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="GIF")
    return buf.getvalue()


def _make_attachment(content_type: str, filename: str, data: bytes) -> MagicMock:
    """Build a mock discord Attachment that supports `await attach.read()`."""
    att = MagicMock()
    att.content_type = content_type
    att.filename = filename
    att.read = AsyncMock(return_value=data)
    att.url = f"https://example.com/{filename}"
    return att


def _build_ctx(*, attachments=None, reference=None, channel=None):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _typing():
        yield

    ctx = MagicMock()
    ctx.message.attachments = attachments or []
    ctx.message.reference = reference
    ctx.channel = channel or MagicMock()
    # Make ctx.send / ctx.reply return awaitable coroutines when called so the
    # implementation under test can `await` them.
    ctx.send = AsyncMock()
    ctx.reply = AsyncMock()
    ctx.typing = _typing
    return ctx


@pytest.fixture(autouse=True)
def _reset_gif_cooldown():
    """Clear global gif cooldowns so tests don't bleed state."""
    utils._gif_cooldowns.clear()
    yield
    utils._gif_cooldowns.clear()


class TestGetImageFromCtx:
    @pytest.mark.asyncio
    async def test_picks_first_attachment_when_no_reply(self):
        data = _make_png_bytes()
        att = _make_attachment("image/png", "a.png", data)
        ctx = _build_ctx(attachments=[att])

        img_bytes, is_gif = await get_image_from_ctx(ctx)

        assert img_bytes == data
        assert is_gif is False
        att.read.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_attachment_overrides_empty_reference(self):
        data = _make_png_bytes()
        att = _make_attachment("image/png", "a.png", data)
        ref = MagicMock()
        ref.message_id = 111
        ctx = _build_ctx(attachments=[att], reference=ref)
        ctx.channel.fetch_message = AsyncMock(side_effect=AssertionError("should not fetch reply"))

        img_bytes, is_gif = await get_image_from_ctx(ctx)

        assert img_bytes == data
        assert is_gif is False

    @pytest.mark.asyncio
    async def test_detects_gif_via_content_type(self):
        data = _make_gif_bytes()
        att = _make_attachment("image/gif", "a.gif", data)
        ctx = _build_ctx(attachments=[att])

        img_bytes, is_gif = await get_image_from_ctx(ctx)

        assert img_bytes == data
        assert is_gif is True

    @pytest.mark.asyncio
    async def test_detects_gif_via_filename(self):
        data = _make_gif_bytes()
        att = _make_attachment("image/gif", "weird.GIF", data)
        ctx = _build_ctx(attachments=[att])

        _, is_gif = await get_image_from_ctx(ctx)

        assert is_gif is True

    @pytest.mark.asyncio
    async def test_returns_none_when_nothing_attached(self):
        ctx = _build_ctx(attachments=[], reference=None)

        img_bytes, is_gif = await get_image_from_ctx(ctx)

        assert img_bytes is None
        assert is_gif is False

    @pytest.mark.asyncio
    async def test_multi_image_collects_all_attachments(self):
        png = _make_png_bytes()
        gif = _make_gif_bytes()
        atts = [
            _make_attachment("image/png", "a.png", png),
            _make_attachment("image/gif", "b.gif", gif),
            _make_attachment("image/png", "c.png", png),
        ]
        ctx = _build_ctx(attachments=atts)

        results = await get_image_from_ctx(ctx, all_images=True)

        assert len(results) == 3
        assert results[0] == (png, False)
        assert results[1] == (gif, True)
        assert results[2] == (png, False)

    @pytest.mark.asyncio
    async def test_multi_image_returns_empty_when_nothing(self):
        ctx = _build_ctx(attachments=[])

        results = await get_image_from_ctx(ctx, all_images=True)

        assert results == []

    @pytest.mark.asyncio
    async def test_collect_all_skips_non_image_attachments(self):
        png = _make_png_bytes()
        atts = [
            _make_attachment("image/png", "a.png", png),
            _make_attachment("text/plain", "notes.txt", b"not an image"),
            _make_attachment("image/jpeg", "b.jpg", png),
        ]
        ctx = _build_ctx(attachments=atts)

        results = await get_image_from_ctx(ctx, all_images=True)

        assert len(results) == 2


def _is_image_content_type(ct: str) -> bool:
    return ct.startswith("image/")


# Patch the helper used by get_image_from_ctx for the skip test.
# The attribute is added by our implementation in cogs/gif_editor.py.
@pytest.fixture(autouse=True)
def _patch_content_type_checker():
    import cogs.gif_editor as ge

    ge._is_image_content_type = _is_image_content_type
    yield
    if hasattr(ge, "_is_image_content_type"):
        delattr(ge, "_is_image_content_type")


class TestGifCmdWithAttachments:
    """Integration tests for the `.gif` command: must work with attachments and multiple images."""

    @pytest.mark.asyncio
    async def test_gif_cmd_works_with_attachment_no_reply(self, monkeypatch):
        from cogs.gif_editor import GifEditorCog

        monkeypatch.setattr("discord.File", lambda fp, filename: MagicMock(fp=fp, filename=filename))

        data = _make_png_bytes()
        att = _make_attachment("image/png", "p.png", data)
        ctx = _build_ctx(attachments=[att])

        cog = GifEditorCog(bot=MagicMock())

        # Bypass real decorators (help_meta) by calling the underlying coroutine.
        await cog.gif_cmd.callback(cog, ctx)

        # Should NOT have sent the "reply to an image" error message.
        assert ctx.send.await_count == 0
        # Should have replied with the converted file.
        ctx.reply.assert_awaited()
        kwargs = ctx.reply.await_args.kwargs
        assert kwargs["file"].filename == "You_Should_Read_Grand_Blue_Dreaming.gif"

    @pytest.mark.asyncio
    async def test_gif_cmd_processes_multiple_attachments(self, monkeypatch):
        from cogs.gif_editor import GifEditorCog

        monkeypatch.setattr("discord.File", lambda fp, filename: MagicMock(fp=fp, filename=filename))

        png_a = _make_png_bytes((255, 0, 0, 255))
        png_b = _make_png_bytes((0, 255, 0, 255))
        atts = [
            _make_attachment("image/png", "a.png", png_a),
            _make_attachment("image/png", "b.png", png_b),
        ]
        ctx = _build_ctx(attachments=atts)

        cog = GifEditorCog(bot=MagicMock())

        await cog.gif_cmd.callback(cog, ctx)

        ctx.send.assert_not_awaited()
        # Should have sent TWO gif replies, one per attachment.
        assert ctx.reply.await_count == 2
        a = ctx.reply.await_args_list[0].kwargs["file"].filename
        b = ctx.reply.await_args_list[1].kwargs["file"].filename
        assert a.endswith(".gif")
        assert b.endswith(".gif")
