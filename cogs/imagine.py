from __future__ import annotations

import base64
import io
import json
import logging
import os
import random
import time
from urllib.parse import quote

import aiohttp
import discord
from discord.ext import commands
from PIL import Image

from utils import check_imagine_cooldown, get_embed_color, help_meta, imagine_cooldown_msg

log = logging.getLogger(__name__)

COG_META = {
    "category": "ai",
    "label": "AI",
    "desc": "AI text and high-definition image generation.",
}


class ImagineCog(commands.Cog, name="Imagine"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def cog_unload(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _try_nvidia_flux(self, session: aiohttp.ClientSession, prompt: str) -> bytes | None:
        url = "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.2-klein-4b"
        payload = {
            "prompt": prompt,
            "samples": 1,
            "seed": random.randint(0, 999999),
            "steps": 4,
        }

        for k_name in ("NVIDIA_API_KEY_1", "NVIDIA_API_KEY_2", "NVIDIA_API_KEY_3"):
            key = os.getenv(k_name)
            if not key:
                continue

            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }

            try:
                async with session.post(
                    url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=45)
                ) as resp:
                    if resp.status != 200:
                        continue
                    raw = await resp.read()
                    data = json.loads(raw)
                    artifacts = data.get("artifacts", [])
                    if artifacts:
                        b64_img = artifacts[0].get("base64", "")
                        if b64_img:
                            return base64.b64decode(b64_img)
                    img_url = data.get("data", [{}])[0].get("url") or data.get("image", "")
                    if img_url:
                        async with session.get(img_url, timeout=aiohttp.ClientTimeout(total=20)) as img_resp:
                            if img_resp.status == 200:
                                return await img_resp.read()
            except Exception as e:
                log.debug(f"{k_name} flux request failed: {e}")
        return None

    async def _try_nvidia_sdxl(self, session: aiohttp.ClientSession, prompt: str) -> bytes | None:
        url = "https://ai.api.nvidia.com/v1/genai/stabilityai/stable-diffusion-xl"
        payload = {
            "text_prompts": [{"text": prompt, "weight": 1}],
            "cfg_scale": 7,
            "sampler": "K_DPM_2_ANCESTRAL",
            "seed": random.randint(0, 999999),
            "steps": 25,
        }

        for k_name in ("NVIDIA_API_KEY_1", "NVIDIA_API_KEY_2", "NVIDIA_API_KEY_3"):
            key = os.getenv(k_name)
            if not key:
                continue

            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

            try:
                async with session.post(
                    url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=45)
                ) as resp:
                    if resp.status != 200:
                        continue
                    raw = await resp.read()
                    data = json.loads(raw)
                    artifacts = data.get("artifacts", [])
                    if artifacts:
                        b64_img = artifacts[0].get("base64", "")
                        if b64_img:
                            return base64.b64decode(b64_img)
            except Exception as e:
                log.debug(f"{k_name} sdxl request failed: {e}")
        return None

    async def _try_pollinations_fallback(self, session: aiohttp.ClientSession, prompt: str) -> bytes | None:
        """High-reliability zero-rate-limit fallback image generation."""
        encoded = quote(prompt[:300])
        seed = random.randint(1, 999999)
        url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true&seed={seed}&model=flux"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    if len(data) > 1024:  # Valid image payload
                        return data
        except Exception as e:
            log.warning(f"Pollinations fallback failed: {e}")
        return None

    @commands.command(name="imagine", aliases=["draw", "gen"])
    @help_meta(
        usage="`.imagine <prompt>`",
        desc="Generate high-definition artwork using the NVIDIA FLUX / SDXL image generation models.",
        section="Image Gen",
        perm_tier="public",
        examples=[
            ".imagine cyberpunk city in the rain at midnight",
            ".imagine aesthetic anime bedroom with warm sunset lighting",
            ".draw futuristic mechanical butterfly glowing neon blue",
        ],
        params=[
            {"name": "prompt", "type": "str", "required": True, "desc": "Detailed text description of the image to generate."},
        ],
        note="Uses NVIDIA FLUX.2 Klein model with multi-key failover and high-res fallbacks.",
    )
    async def imagine_cmd(self, ctx: commands.Context, *, prompt: str = None):
        """Generate an image using AI."""
        if not prompt:
            return await ctx.send("-# usage: `.imagine <prompt>`")

        cd = check_imagine_cooldown(ctx.author.id)
        if cd:
            if cd != "silent":
                await ctx.send(imagine_cooldown_msg(int(cd)))
            return

        start_time = time.perf_counter()
        async with ctx.typing():
            session = await self._get_session()

            # 1. Try NVIDIA Flux
            img_bytes = await self._try_nvidia_flux(session, prompt)

            # 2. Try NVIDIA SDXL
            if not img_bytes:
                img_bytes = await self._try_nvidia_sdxl(session, prompt)

            # 3. Try Pollinations Fallback
            if not img_bytes:
                img_bytes = await self._try_pollinations_fallback(session, prompt)

            if not img_bytes:
                return await ctx.send("-# image generation failed (all model providers unavailable)")

            # Validate & optimize image with PIL
            try:
                with Image.open(io.BytesIO(img_bytes)) as pil_img:
                    # Ensure within Discord file limits (< 8MB)
                    buf = io.BytesIO()
                    if pil_img.format == "PNG" and len(img_bytes) > 6 * 1024 * 1024:
                        pil_img.convert("RGB").save(buf, format="JPEG", quality=90, optimize=True)
                    else:
                        pil_img.save(buf, format=pil_img.format or "PNG")
                    buf.seek(0)
                    img_bytes = buf.getvalue()
            except Exception as e:
                log.warning(f"Image PIL validation error: {e}")

            elapsed = round(time.perf_counter() - start_time, 1)

            file = discord.File(io.BytesIO(img_bytes), filename="imagine.png")
            embed = discord.Embed(
                description=f"✦ **Prompt:** {prompt[:300]}",
                color=get_embed_color(ctx.guild.id if ctx.guild else 0),
            )
            embed.set_image(url="attachment://imagine.png")
            embed.set_footer(text=f"Generated in {elapsed}s · xo ai")

            await ctx.send(embed=embed, file=file)


async def setup(bot: commands.Bot):
    await bot.add_cog(ImagineCog(bot))
