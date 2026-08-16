"""
cogs/quote.py  —  quote generator (.quote) with custom dark aesthetic PIL cards
"""

import asyncio
import io
import logging
import re
from datetime import datetime, timezone

import aiohttp
import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFilter

from utils import help_meta

log = logging.getLogger(__name__)

COG_META = {
    "category": "fun",
    "label": "Fun",
    "desc": "Aesthetic quote card generator.",
}

_JUMP_URL_RE = re.compile(
    r"https?://(?:ptb\.|canary\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)"
)


def _circle_avatar(avatar_bytes: bytes, size: int) -> Image.Image:
    with Image.open(io.BytesIO(avatar_bytes)) as img:
        img = img.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _render_quote_card(
    avatar_bytes: bytes | None,
    author_name: str,
    username: str,
    quote_text: str,
    server_name: str,
    timestamp_str: str,
) -> io.BytesIO:
    from cogs.serverstats import _load_font

    f_quote = _load_font(32, bold=False)
    f_author = _load_font(24, bold=True)
    f_sub = _load_font(18, bold=False)
    f_watermark = _load_font(90, bold=True)

    # Word wrapping for quote text
    max_text_width = 620
    words = quote_text.split()
    lines = []
    curr = []
    for w in words:
        test = " ".join(curr + [w])
        if f_quote.getlength(test) <= max_text_width:
            curr.append(w)
        else:
            if curr:
                lines.append(" ".join(curr))
                curr = [w]
            else:
                lines.append(w)
                curr = []
    if curr:
        lines.append(" ".join(curr))
    if not lines:
        lines = ["..."]

    # Calculate dynamic height
    line_h = 44
    text_block_h = len(lines) * line_h
    H = max(380, 160 + text_block_h + 100)
    W = 1000

    # Background generation
    from cogs.serverstats import _make_glass_backdrop
    bg = _make_glass_backdrop(avatar_bytes, W, H, dark_tint=0.55, blur_radius=20)

    # Glass container card
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    pad_x, pad_y = 35, 30
    cd.rounded_rectangle(
        [pad_x, pad_y, W - pad_x, H - pad_y],
        radius=28,
        fill=(0, 0, 0, 95),
        outline=(255, 255, 255, 55),
        width=1,
    )
    cd.line([(pad_x + 25, pad_y + 1), (W - pad_x - 25, pad_y + 1)], fill=(255, 255, 255, 95), width=1)
    bg = Image.alpha_composite(bg, card)
    draw = ImageDraw.Draw(bg)

    # Giant watermark quote mark
    f_watermark.draw(draw, (pad_x + 30, pad_y + 10), "\u201c", fill=(255, 255, 255, 20))

    # Avatar on the left
    av_size = 140
    av_x = pad_x + 50
    av_y = (H - av_size) // 2
    if avatar_bytes:
        try:
            av = _circle_avatar(avatar_bytes, av_size)
            bg.paste(av, (av_x, av_y), av)
            # 1px border around avatar
            draw.ellipse([av_x, av_y, av_x + av_size, av_y + av_size], outline=(255, 255, 255, 60), width=1)
        except Exception:
            pass

    # Quote text on the right
    text_x = av_x + av_size + 50
    start_y = (H - (text_block_h + 75)) // 2

    cur_y = start_y
    for line in lines:
        f_quote.draw(draw, (text_x, cur_y), line, fill=(255, 255, 255, 240))
        cur_y += line_h

    cur_y += 20
    # Author & server footer
    f_author.draw(draw, (text_x, cur_y), f"— {author_name}", fill=(255, 255, 255, 220))
    cur_y += 30
    footer_text = f"@{username} · {server_name} · {timestamp_str}"
    f_sub.draw(draw, (text_x, cur_y), footer_text, fill=(255, 255, 255, 130))

    buf = io.BytesIO()
    bg.convert("RGB").save(buf, format="PNG", quality=95)
    buf.seek(0)
    return buf


class Quote(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="quote")
    @help_meta(
        usage="`.quote [message_link|message_id]`  ·  Reply with `.quote`",
        desc="Renders an aesthetic dark monochrome quote card from a message reply, jump link, or text.",
        section="Fun",
        perm_tier="public",
        examples=[".quote", ".quote https://discord.com/channels/...", '.quote "wisdom" - @user'],
        params=[
            {
                "name": "target",
                "type": "str",
                "required": False,
                "desc": "Message link, message ID, or custom quote text formatted as `\"text\" - @user`.",
            }
        ],
        note="Reply to any message with `.quote` for instant card generation.",
    )
    async def quote_cmd(self, ctx: commands.Context, *, target: str = None):
        target_msg: discord.Message | None = None
        custom_quote: str | None = None
        custom_author: discord.User | discord.Member | None = None

        # 1. Check if replying to a message
        if ctx.message.reference and ctx.message.reference.message_id:
            try:
                target_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
            except discord.HTTPException:
                pass

        # 2. Check if argument is a jump URL
        if not target_msg and target:
            m = _JUMP_URL_RE.match(target.strip())
            if m:
                gid, cid, mid = int(m.group(1)), int(m.group(2)), int(m.group(3))
                channel = self.bot.get_channel(cid)
                if channel:
                    try:
                        target_msg = await channel.fetch_message(mid)
                    except discord.HTTPException:
                        pass

        # 3. Check if argument is a raw message ID
        if not target_msg and target and target.strip().isdigit():
            try:
                target_msg = await ctx.channel.fetch_message(int(target.strip()))
            except discord.HTTPException:
                pass

        # 4. Check for custom text like `"quote text" - @user`
        if not target_msg and target:
            if "-" in target:
                parts = target.rsplit("-", 1)
                custom_quote = parts[0].strip().strip('"').strip("'")
                author_str = parts[1].strip()
                if ctx.message.mentions:
                    custom_author = ctx.message.mentions[0]
                else:
                    custom_author = ctx.guild.get_member_named(author_str) if ctx.guild else None
            else:
                custom_quote = target.strip().strip('"').strip("'")
                custom_author = ctx.author

        if not target_msg and not custom_quote:
            return await ctx.send("-# reply to a message with `.quote` or provide a message link/id")

        if target_msg:
            quote_text = target_msg.clean_content or (
                target_msg.embeds[0].description if target_msg.embeds else "..."
            )
            author = target_msg.author
            server_name = target_msg.guild.name if target_msg.guild else "dm"
            ts_str = target_msg.created_at.strftime("%b %d, %Y")
        else:
            quote_text = custom_quote or "..."
            author = custom_author or ctx.author
            server_name = ctx.guild.name if ctx.guild else "neixo"
            ts_str = datetime.now(timezone.utc).strftime("%b %d, %Y")

        avatar_bytes = None
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(author.display_avatar.url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        avatar_bytes = await r.read()
        except Exception:
            pass

        async with ctx.typing():
            buf = await asyncio.to_thread(
                _render_quote_card,
                avatar_bytes,
                author.display_name,
                author.name,
                quote_text,
                server_name.lower(),
                ts_str,
            )

        await ctx.send(file=discord.File(fp=buf, filename=f"quote_{author.name}.png"))


async def setup(bot: commands.Bot):
    await bot.add_cog(Quote(bot))
