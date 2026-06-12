from __future__ import annotations
import asyncio
import io
import os
import tempfile

import aiohttp
import discord
from discord.ext import commands
from PIL import Image, ImageSequence, ImageFont, ImageDraw
from curl_cffi import requests as req
from bs4 import BeautifulSoup
import re as _re

from utils import get_embed_color, check_gif_cooldown, gif_cooldown_msg, help_meta

# ── Font paths (Ubuntu 22 compatible) ──────────────────────────────
_FONT_REG_PATHS = [
    "/usr/share/fonts/truetype/jetbrains/JetBrainsMono-Regular.ttf",
    "/usr/share/fonts/opentype/jetbrains/JetBrainsMono-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSans-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]
_FONT_BOLD_PATHS = [
    "/usr/share/fonts/truetype/jetbrains/JetBrainsMono-Bold.ttf",
    "/usr/share/fonts/opentype/jetbrains/JetBrainsMono-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSans-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]

def _load_font(size: int, bold: bool = False):
    for p in (_FONT_BOLD_PATHS if bold else _FONT_REG_PATHS):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()

# ── cogs/gif_editor.py ──────────────────────────────────────────
COG_META = {
    "category": "image",
    "label": "Image",
    "desc": "Image and GIF manipulation commands.",
}


# ─────────────────────────────────────────────────────────────
# TENOR FETCH
# ─────────────────────────────────────────────────────────────

def tenor_fetch(link: str) -> str | None:
    """Scrape a tenor page link and download the GIF. Returns local path or None."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    media_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0",
        "Referer": "https://tenor.com/",
    }
    if not link.startswith("https"):
        return None
    try:
        resp = req.get(url=link, headers=headers, impersonate="chrome110", timeout=15)
        print(f"tenor_fetch: got {resp.status_code} for {link}")
        if resp.status_code != 200:
            return None

        # strategy 1: find media urls directly in page source
        matches = _re.findall(
            r'https://(?:media\d*\.tenor\.com|c\.tenor\.com)/[^\s"\'<>]+\.gif[^\s"\'<>]*',
            resp.text
        )
        full = [m for m in matches if 'AAAAM' in m or 'full' in m.lower()]
        gif_url = full[0] if full else (matches[0] if matches else None)
        print(f"tenor_fetch: strategy1 gif_url={gif_url}")

        # strategy 2: og:image meta tag
        if not gif_url:
            soup = BeautifulSoup(resp.text, "html.parser")
            og = soup.find("meta", property="og:image")
            if og:
                gif_url = og.get("content")
                print(f"tenor_fetch: strategy2 og:image gif_url={gif_url}")

        if not gif_url:
            print("tenor_fetch: no gif url found in page")
            return None

        gif_url = gif_url.split("?")[0]

        gif_resp = req.get(url=gif_url, headers=media_headers, impersonate="chrome110", timeout=20)
        print(f"tenor_fetch: media fetch {gif_resp.status_code}, size={len(gif_resp.content)}")
        if gif_resp.status_code == 200 and len(gif_resp.content) > 5000:
            tmp = tempfile.NamedTemporaryFile(suffix=".gif", delete=False)
            tmp.write(gif_resp.content)
            tmp.close()
            return tmp.name
    except Exception as e:
        import traceback
        print(f"tenor_fetch error: {e}")
        print(traceback.format_exc())
    return None


# ─────────────────────────────────────────────────────────────
# IMAGE FROM CONTEXT HELPER
# ─────────────────────────────────────────────────────────────

async def get_image_from_ctx(ctx) -> tuple[bytes | None, bool]:
    """
    Returns (img_bytes, is_gif) from the message attachment or a replied-to message.
    Returns (None, False) if nothing found.
    """
    target = None

    if ctx.message.attachments:
        target = ctx.message.attachments[0]
    elif ctx.message.reference:
        replied_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        if replied_msg.attachments:
            target = replied_msg.attachments[0]
        elif replied_msg.embeds:
            embed = replied_msg.embeds[0]
            image_url = None
            if embed.image:
                image_url = embed.image.url
            elif embed.thumbnail:
                image_url = embed.thumbnail.url
            elif embed.type == "image":
                image_url = embed.url

            if image_url:
                if "tenor.com" in image_url:
                    if any(image_url.lower().endswith(ext) for ext in ('.gif', '.png', '.jpg', '.webp', '.mp4')):
                        gif_attempt = _re.sub(r'\.(png|webp|jpg)$', '.gif', image_url)
                        gif_attempt = _re.sub(r'AAAA[a-zA-Z]+', 'AAAAM', gif_attempt)
                        async with aiohttp.ClientSession() as session:
                            async with session.get(gif_attempt) as resp:
                                if resp.status == 200:
                                    return await resp.read(), True
                            async with session.get(image_url) as resp:
                                if resp.status == 200:
                                    return await resp.read(), image_url.lower().endswith('.gif')
                    else:
                        gif_path = await asyncio.to_thread(tenor_fetch, image_url)
                        if gif_path:
                            try:
                                with open(gif_path, "rb") as f:
                                    data = f.read()
                                return data, True
                            finally:
                                try:
                                    os.unlink(gif_path)
                                except Exception:
                                    pass
                else:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(image_url) as resp:
                            if resp.status == 200:
                                return await resp.read(), (
                                    image_url.lower().endswith('.gif')
                                    or 'gif' in resp.headers.get('content-type', '').lower()
                                )
            return None, False

    if target:
        img_bytes = await target.read()
        is_gif = (
            (target.content_type and "gif" in target.content_type)
            or target.filename.lower().endswith(".gif")
        )
        return img_bytes, is_gif

    return None, False


# ─────────────────────────────────────────────────────────────
# GIF CREATION HELPER
# ─────────────────────────────────────────────────────────────

def create_gif(frames: list, duration: int = 100, loop: int = 0, filename: str = "output.gif") -> discord.File:
    b = io.BytesIO()
    frames[0].save(
        b,
        format='GIF',
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=loop,
        optimize=True,
    )
    b.seek(0)
    return discord.File(fp=b, filename=filename)


# ─────────────────────────────────────────────────────────────
# COG
# ─────────────────────────────────────────────────────────────

class GifEditorCog(commands.Cog, name="GifEditor"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── shared cooldown check ──────────────────────────────
    async def _cooldown(self, ctx) -> bool:
        """Returns True if on cooldown (already responded). False if free to proceed."""
        cd = check_gif_cooldown(ctx.author.id)
        if cd:
            if cd != "silent":
                await ctx.send(gif_cooldown_msg(int(cd)))
            return True
        return False

    # ─────────────────────────────────────────────────────────
    # .gif — convert image to GIF
    # ─────────────────────────────────────────────────────────
    @help_meta(usage="`.gif [name]`", desc="converts an image to a GIF. reply to any image.")
    @commands.command(name='gif')
    async def gif_cmd(self, ctx, name: str = "You_Should_Read_Grand_Blue_Dreaming"):
        if await self._cooldown(ctx):
            return

        if ctx.message.reference:
            replied_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
            if replied_msg.attachments:
                img = replied_msg.attachments[0]
            else:
                return await ctx.send("the message u replied to doesn't have an image!")
        else:
            return await ctx.send("reply to an image to use this")

        async with ctx.typing():
            try:
                image_bytes = await img.read()
                with Image.open(io.BytesIO(image_bytes)) as img_obj:
                    img_converted = img_obj.convert("RGBA")
                    img_converted.thumbnail((600, 600), Image.Resampling.LANCZOS)
                    frames = [img_converted.copy()] * 2
                    b = io.BytesIO()
                    frames[0].save(
                        b,
                        format='GIF',
                        save_all=True,
                        append_images=frames[1:],
                        duration=100,
                        loop=0,
                        optimize=True,
                        disposal=2,
                    )
                    b.seek(0)
                await ctx.reply(file=discord.File(fp=b, filename=f"{name}.gif"))
            except Exception as e:
                await ctx.send(f"error: {str(e)}")

    # ─────────────────────────────────────────────────────────
    # .fadegif — fade-in animation from background color
    # ─────────────────────────────────────────────────────────
    @help_meta(usage="`.fadegif [color] [name]`", desc="creates a fade-in animation from a solid color.")
    @commands.command(name='fadegif')
    async def fadegif_cmd(self, ctx, color: str = "black", name: str = "You_Should_Read_Grand_Blue_Dreaming"):
        if await self._cooldown(ctx):
            return

        COLORS = {
            "black": (0, 0, 0, 255), "white": (255, 255, 255, 255),
            "red": (255, 0, 0, 255), "green": (0, 255, 0, 255),
            "blue": (0, 0, 255, 255), "yellow": (255, 255, 0, 255),
            "purple": (128, 0, 128, 255), "orange": (255, 165, 0, 255),
        }
        color = color.lower()
        if color not in COLORS:
            return await ctx.send('❌ Choose from: black, white, red, green, blue, yellow, purple, orange')

        async with ctx.typing():
            try:
                img_bytes, _ = await get_image_from_ctx(ctx)
                if not img_bytes:
                    return await ctx.send("❌ Reply to an image to create a fade GIF.")

                with Image.open(io.BytesIO(img_bytes)) as img:
                    img_obj = img.convert("RGBA")
                img_obj.thumbnail((600, 600), Image.Resampling.LANCZOS)

                bg_layer = Image.new("RGBA", img_obj.size, COLORS[color])
                steps = 20
                final_frames = []

                for i in range(steps):
                    alpha = i / (steps - 1)
                    blended = Image.blend(bg_layer, img_obj, alpha)
                    final_frames.append(blended.convert("RGB"))

                # hold on final frame
                final_frames += [img_obj.convert("RGB")] * steps

                ioB = io.BytesIO()
                final_frames[0].save(
                    ioB, format='GIF', save_all=True, append_images=final_frames[1:],
                    duration=50, loop=0, optimize=True
                )
                if ioB.tell() > 25 * 1024 * 1024:
                    return await ctx.send("❌ GIF too large! Try a smaller source image.")
                ioB.seek(0)
                await ctx.reply(file=discord.File(fp=ioB, filename=f"{name}_{color}.gif"))
            except Exception as e:
                await ctx.send(f"❌ Error: {str(e)}")

    # ─────────────────────────────────────────────────────────
    # .spingif — rotation animation
    # ─────────────────────────────────────────────────────────
    @help_meta(usage="`.spingif [name]`", desc="makes the image spin (45° steps).")
    @commands.command(name='spingif')
    async def spingif_cmd(self, ctx, name: str = "spin"):
        if await self._cooldown(ctx):
            return

        async with ctx.typing():
            try:
                image_bytes, _ = await get_image_from_ctx(ctx)
                if not image_bytes:
                    return await ctx.send("❌ Attach or reply to an image.")

                with Image.open(io.BytesIO(image_bytes)) as img:
                    img_obj = img.convert("RGBA")
                img_obj.thumbnail((600, 600), Image.Resampling.LANCZOS)

                frames = []
                for angle in [0, 45, 90, 135, 180, 225, 270, 315]:
                    rotated = img_obj.rotate(
                        angle, expand=False,
                        resample=Image.Resampling.BICUBIC,
                        fillcolor=(255, 255, 255, 0)
                    )
                    frames.append(rotated.convert("RGB"))

                file = create_gif(frames, duration=100, filename=f"{name}.gif")
                file.fp.seek(0, 2)
                if file.fp.tell() > 25 * 1024 * 1024:
                    return await ctx.send("❌ GIF too large!")
                file.fp.seek(0)
                await ctx.reply(file=file)
            except Exception as e:
                await ctx.send(f"❌ Error: {str(e)}")

    # ─────────────────────────────────────────────────────────
    # .zoomgif — smooth zoom in/out
    # ─────────────────────────────────────────────────────────
    @help_meta(usage="`.zoomgif [in/out] [fade] [color] [name]`", desc="creates a smooth zoom animation.")
    @commands.command(name='zoomgif')
    async def zoomgif_cmd(self, ctx, type: str = "in", fade: str = "none",
                          fcolor: str = "black", gif_name: str = None):
        if await self._cooldown(ctx):
            return

        if not gif_name:
            gif_name = "You_Should_Read_Grand_Blue_Dreaming"

        async with ctx.typing():
            img_bytes, _ = await get_image_from_ctx(ctx)
            if not img_bytes:
                return await ctx.send("Reply to an image to use this command.")

            with Image.open(io.BytesIO(img_bytes)) as img:
                so = img.convert("RGB")
            so.thumbnail((600, 600), Image.Resampling.LANCZOS)
            orig_w, orig_h = so.size

            type = type.lower()
            fade = fade.lower()
            fcolor = fcolor.lower()

            # build zoom frames
            zoom_frames = []
            default_zoom, max_zoom, num_steps = 1.0, 1.2, 30
            step_size = (max_zoom - default_zoom) / num_steps

            for i in range(num_steps + 1):
                current_zoom = default_zoom + (i * step_size)
                new_w, new_h = int(orig_w * current_zoom), int(orig_h * current_zoom)
                resized = so.resize((new_w, new_h), Image.Resampling.LANCZOS)
                left, top = (new_w - orig_w) // 2, (new_h - orig_h) // 2
                zoom_frames.append(resized.crop((left, top, left + orig_w, top + orig_h)))

            if type == "out":
                zoom_frames.reverse()

            # apply fade overlay
            bg_rgb = (255, 255, 255, 255) if fcolor == "white" else (0, 0, 0, 255)
            bg_layer = Image.new("RGBA", (orig_w, orig_h), bg_rgb)
            total_frames = len(zoom_frames)
            final_sequence = []

            for i, frame in enumerate(zoom_frames):
                rgba_frame = frame.convert("RGBA")
                if fade == "in":
                    alpha = i / (total_frames - 1)
                elif fade == "out":
                    alpha = 1.0 - (i / (total_frames - 1))
                else:
                    alpha = 1.0

                if alpha < 1.0:
                    blended = Image.blend(bg_layer, rgba_frame, alpha)
                    final_sequence.append(blended.convert("RGB"))
                else:
                    final_sequence.append(frame)

            ioB = io.BytesIO()
            final_sequence[0].save(
                ioB, format='GIF', save_all=True, append_images=final_sequence[1:],
                duration=50, loop=0, optimize=True
            )
            ioB.seek(0)
            await ctx.reply(file=discord.File(fp=ioB, filename=f"{gif_name}.gif"))

    # ─────────────────────────────────────────────────────────
    # .bouncegif — bounce with easing
    # ─────────────────────────────────────────────────────────
    @help_meta(usage="`.bouncegif [name]`", desc="makes the image bounce with easing.")
    @commands.command(name='bouncegif')
    async def bouncegif_cmd(self, ctx, name: str = "bounce"):
        if await self._cooldown(ctx):
            return

        async with ctx.typing():
            try:
                image_bytes, _ = await get_image_from_ctx(ctx)
                if not image_bytes:
                    return await ctx.send("❌ Attach or reply to an image.")

                with Image.open(io.BytesIO(image_bytes)) as img:
                    img_obj = img.convert("RGBA")
                img_obj.thumbnail((600, 600), Image.Resampling.LANCZOS)

                w, h = img_obj.size
                bg = Image.new("RGBA", (w, h + 100), (255, 255, 255, 0))
                frames = []

                for i in range(20):
                    progress = i / 19
                    # ease-in-out quadratic
                    if progress < 0.5:
                        offset = int(80 * (2 * progress) ** 2)
                    else:
                        offset = int(80 * (1 - (2 * progress - 1) ** 2))
                    frame = bg.copy()
                    frame.paste(img_obj, (0, offset), img_obj)
                    frames.append(frame.crop((0, 0, w, h)).convert("RGB"))

                file = create_gif(frames, duration=80, filename=f"{name}.gif")
                file.fp.seek(0, 2)
                if file.fp.tell() > 25 * 1024 * 1024:
                    return await ctx.send("❌ GIF too large!")
                file.fp.seek(0)
                await ctx.reply(file=file)
            except Exception as e:
                await ctx.send(f"❌ Error: {str(e)}")

    # ─────────────────────────────────────────────────────────
    # .textgif — typing text animation
    # ─────────────────────────────────────────────────────────
    @help_meta(usage="`.textgif <text>`", desc="creates a typing animation GIF (max 30 chars).")
    @commands.command(name='textgif')
    async def textgif_cmd(self, ctx, *, text: str):
        if await self._cooldown(ctx):
            return

        async with ctx.typing():
            try:
                frames = []
                bg = Image.new("RGB", (400, 200), "white")
                font = _load_font(32)

                current = ""
                for char in text[:30]:
                    current += char
                    frame = bg.copy()
                    draw = ImageDraw.Draw(frame)
                    bbox = draw.textbbox((0, 0), current, font=font)
                    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
                    x, y = (400 - tw) // 2, (200 - th) // 2
                    draw.text((x, y), current, fill="black", font=font)
                    frames.append(frame)

                # hold final frame
                for _ in range(3):
                    frames.append(frames[-1])

                file = create_gif(frames, duration=150, filename="text.gif")
                file.fp.seek(0, 2)
                if file.fp.tell() > 25 * 1024 * 1024:
                    return await ctx.send("❌ GIF too large! Try shorter text.")
                file.fp.seek(0)
                await ctx.reply(file=file)
            except Exception as e:
                await ctx.send(f"❌ Error: {str(e)}")

    # ─────────────────────────────────────────────────────────
    # .upscale — upscale image by factor
    # ─────────────────────────────────────────────────────────
    @help_meta(usage="`.upscale [factor] [name]`", desc="upscales an image by a given factor (1x-6x).")
    @commands.command(name='upscale')
    async def upscale_cmd(self, ctx, factor: str = "2x", gif_name: str = None):
        if await self._cooldown(ctx):
            return
        if not gif_name:
            gif_name = "You_Should_Read_Grand_Blue_Dreaming"

        factor_int = int(factor.lower().replace("x", ""))
        if not 1 <= factor_int <= 6:
            return await ctx.send("Please use a factor between 1 and 6.")

        async with ctx.typing():
            try:
                img_bytes, _ = await get_image_from_ctx(ctx)
                if not img_bytes:
                    return await ctx.send("Reply to an image to use this command.")

                with Image.open(io.BytesIO(img_bytes)) as img:
                    img = img.convert("RGB")
                w, h = img.size
                new_size = (w * factor_int, h * factor_int)
                if new_size[0] > 4000 or new_size[1] > 4000:
                    return await ctx.send("idk if i can send this image to discord bro.")

                upscaled = img.resize(new_size, Image.Resampling.LANCZOS)
                ioB = io.BytesIO()
                upscaled.save(ioB, format='PNG')
                ioB.seek(0)
                await ctx.reply(file=discord.File(fp=ioB, filename=f"{gif_name}_{factor_int}x.png"))
            except Exception as e:
                await ctx.send(f"❌ Error: {str(e)}")

    # ─────────────────────────────────────────────────────────
    # .downscale — shrink image by factor
    # ─────────────────────────────────────────────────────────
    @help_meta(usage="`.downscale [factor] [name]`", desc="shrinks an image by a given factor.")
    @commands.command(name='downscale')
    async def downscale_cmd(self, ctx, factor: str = "2x", gif_name: str = None):
        if await self._cooldown(ctx):
            return
        if not gif_name:
            gif_name = "You_Should_Read_Grand_Blue_Dreaming"

        factor_int = int(factor.lower().replace("x", ""))
        if not 1 <= factor_int <= 6:
            return await ctx.send("Please use a factor between 1 and 6.")

        async with ctx.typing():
            try:
                img_bytes, _ = await get_image_from_ctx(ctx)
                if not img_bytes:
                    return await ctx.send("Reply to an image to use this command.")

                with Image.open(io.BytesIO(img_bytes)) as img:
                    img = img.convert("RGB")
                w, h = img.size
                new_size = (max(1, w // factor_int), max(1, h // factor_int))
                downscaled = img.resize(new_size, Image.Resampling.LANCZOS)
                ioB = io.BytesIO()
                downscaled.save(ioB, format='PNG')
                ioB.seek(0)
                await ctx.reply(file=discord.File(fp=ioB, filename=f"{gif_name}_{factor_int}x.png"))
            except Exception as e:
                await ctx.send(f"❌ Error: {str(e)}")

    # ─────────────────────────────────────────────────────────
    # .lowquality — crunch quality (pixelated cursed look)
    # ─────────────────────────────────────────────────────────
    @help_meta(usage="`.lowquality [factor] [name]`", desc="crunches image quality (cursed pixelated look).")
    @commands.command(name='lowquality')
    async def lowquality_cmd(self, ctx, factor: str = "4x", gif_name: str = None):
        if await self._cooldown(ctx):
            return
        if not gif_name:
            gif_name = "You_Should_Read_Grand_Blue_Dreaming"

        factor_int = int(factor.lower().replace("x", ""))
        if not 1 <= factor_int <= 10:
            return await ctx.send("Please use a factor between 1 and 10.")

        async with ctx.typing():
            try:
                img_bytes, _ = await get_image_from_ctx(ctx)
                if not img_bytes:
                    return await ctx.send("Reply to an image to use this command.")

                with Image.open(io.BytesIO(img_bytes)) as img:
                    img = img.convert("RGB")
                orig_size = img.size
                small_size = (max(1, orig_size[0] // factor_int), max(1, orig_size[1] // factor_int))
                temp_small = img.resize(small_size, Image.Resampling.BILINEAR)
                final = temp_small.resize(orig_size, Image.Resampling.NEAREST)
                ioB = io.BytesIO()
                final.save(ioB, format='PNG')
                ioB.seek(0)
                await ctx.reply(file=discord.File(fp=ioB, filename=f"{gif_name}_{factor_int}x.png"))
            except Exception as e:
                await ctx.send(f"❌ Error: {str(e)}")

    # ─────────────────────────────────────────────────────────
    # .bar — add colored bar/border to image
    # ─────────────────────────────────────────────────────────
    @help_meta(usage="`.bar [type] [color] [size] [name]`", desc="adds a coloured bar/border to an image.")
    @commands.command(name='bar')
    async def bar_cmd(self, ctx, type: str = "horizontal", color: str = "black",
                      bar_size: int = 40, gif_name: str = None):
        if not gif_name:
            gif_name = "You_Should_Read_Grand_Blue_Dreaming"

        type = type.lower()
        color = color.lower()

        VALID_TYPES = ["horizontal", "vertical", "top", "bottom", "right", "left"]
        COLORS_DICT = {
            "red": "#FF0000", "green": "#00FF00", "blue": "#0000FF",
            "white": "#FFFFFF", "black": "#000000", "yellow": "#FFFF00",
            "cyan": "#00FFFF", "magenta": "#FF00FF",
        }

        if type not in VALID_TYPES:
            return await ctx.send(f"Invalid type! Use: {', '.join(VALID_TYPES)}")
        if color not in COLORS_DICT:
            return await ctx.send(f"Invalid color! Use: {', '.join(COLORS_DICT.keys())}")
        if not 1 <= bar_size <= 500:
            return await ctx.send("Keep the bar size between 1 and 500.")

        async with ctx.typing():
            try:
                img_bytes, _ = await get_image_from_ctx(ctx)
                if not img_bytes:
                    return await ctx.send("No image found in that message.")

                with Image.open(io.BytesIO(img_bytes)) as img:
                    img = img.convert("RGB")
                w, h = img.size
                hex_color = COLORS_DICT[color]

                if type == "horizontal":
                    new_size = (w, h + (bar_size * 2))
                    paste_coords = (0, bar_size)
                elif type == "vertical":
                    new_size = (w + (bar_size * 2), h)
                    paste_coords = (bar_size, 0)
                elif type == "top":
                    new_size = (w, h + bar_size)
                    paste_coords = (0, bar_size)
                elif type == "bottom":
                    new_size = (w, h + bar_size)
                    paste_coords = (0, 0)
                elif type == "left":
                    new_size = (w + bar_size, h)
                    paste_coords = (bar_size, 0)
                elif type == "right":
                    new_size = (w + bar_size, h)
                    paste_coords = (0, 0)

                bg = Image.new("RGB", new_size, hex_color)
                bg.paste(img, paste_coords)
                ioB = io.BytesIO()
                bg.save(ioB, format='PNG')
                ioB.seek(0)
                await ctx.reply(file=discord.File(fp=ioB, filename=f"{gif_name}.png"))
            except Exception as e:
                await ctx.send(f"❌ Error: {str(e)}")

    # ─────────────────────────────────────────────────────────
    # .thisu — POV overlay effect
    # ─────────────────────────────────────────────────────────
    @help_meta(usage="`.thisu`", desc="applies a POV overlay effect to an image or GIF.")
    @commands.command(name='thisu')
    async def thisu_cmd(self, ctx):
        if await self._cooldown(ctx):
            return

        img_bytes, is_gif = await get_image_from_ctx(ctx)
        if not img_bytes:
            return await ctx.send("You gotta give me an image or reply to one, fam.")

        async with ctx.typing():
            try:
                # Offload heavy image processing to a thread to keep the bot responsive
                file = await asyncio.to_thread(self._process_thisu, img_bytes, is_gif)
                await ctx.reply(file=file)
            except Exception as e:
                print(f"error for the thisu command: {e}")
                await ctx.send("Something went wrong while processing the image.")

    def _process_thisu(self, img_bytes: bytes, is_gif: bool) -> discord.File:
        """Synchronous CPU-bound processing for the POV mask overlay."""
        with Image.open(os.path.join("assets", "pov.gif")) as pov_gif:
            pov_gif.seek(0)
            pov_mask = pov_gif.copy().convert("RGBA")

        def apply_mask(frame: Image.Image) -> Image.Image:
            frame = frame.convert("RGBA")
            fw, fh = frame.size
            pw, ph = pov_mask.size
            scale = max(fw / pw, fh / ph)
            new_pw = int(pw * scale)
            new_ph = int(ph * scale)
            scaled_mask = pov_mask.resize((new_pw, new_ph), Image.Resampling.LANCZOS)
            frame_pixels = frame.load()
            mask_pixels = scaled_mask.load()
            x_offset = (new_pw - fw) // 2
            y_offset = (new_ph - fh) // 2
            for y in range(min(new_ph, fh)):
                for x in range(fw):
                    mx = x + x_offset
                    my = y + y_offset
                    if 0 <= mx < new_pw and 0 <= my < new_ph and mask_pixels[mx, my][3] < 255:
                        frame_pixels[x, y] = (0, 0, 0, 0)
            return frame

        output_buffer = io.BytesIO()
        if is_gif:
            with Image.open(io.BytesIO(img_bytes)) as input_gif:
                frames = []
                durations = []
                for frame in ImageSequence.Iterator(input_gif):
                    frame = frame.convert("RGBA")
                    durations.append(frame.info.get("duration", 100))
                    frames.append(apply_mask(frame))

            if len(frames) > 1:
                frames[0].save(
                    output_buffer, format="GIF", save_all=True,
                    append_images=frames[1:], duration=durations, loop=0, disposal=2,
                )
            else:
                frames[0].save(output_buffer, format="GIF", duration=durations[0], loop=0)
            filename = "this_u.gif"
        else:
            with Image.open(io.BytesIO(img_bytes)) as input_image:
                final_image = apply_mask(input_image)
            final_image.save(output_buffer, format="PNG")
            filename = "this_u.png"

        output_buffer.seek(0)
        return discord.File(output_buffer, filename=filename)


async def setup(bot: commands.Bot):
    await bot.add_cog(GifEditorCog(bot))