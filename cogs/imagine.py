from __future__ import annotations

import base64
import io
import json
import logging
import os

import aiohttp
import discord
from discord.ext import commands

from utils import check_imagine_cooldown, help_meta, imagine_cooldown_msg

log = logging.getLogger(__name__)


COG_META = {
    "category": "fun",
    "label": "Fun",
    "desc": "Fun staff tools.",
}


class ImagineCog(commands.Cog, name="Imagine"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None

    async def cog_load(self):
        self.session = aiohttp.ClientSession()

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    @help_meta(
        usage="`.imagine <prompt>`",
        desc="Generate an image using AI.",
        examples=[".imagine a cat in space", ".imagine cyberpunk city rain"],
        params=[
            {"name": "prompt", "type": "str", "required": True, "desc": "Description of the image to generate."},
        ],
        note="Uses NVIDIA FLUX.2 model. Per-user cooldown applies.",
    )
    @commands.command(name="imagine", aliases=["draw", "gen"])
    async def imagine_cmd(self, ctx: commands.Context, *, prompt: str):
        """Generate an image using AI."""
        cd = check_imagine_cooldown(ctx.author.id)
        if cd:
            if cd != "silent":
                await ctx.send(imagine_cooldown_msg(int(cd)))
            return
        await ctx.typing()

        url = "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.2-klein-4b"
        payload = {
            "prompt": prompt,
            "samples": 1,
            "seed": 0,
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
                async with self.session.post(
                    url, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=120)
                ) as resp:
                    raw = await resp.read()
                    if resp.status != 200:
                        log.warning(f"{k_name} returned status {resp.status}, trying next key")
                        continue

                    data = json.loads(raw)
                    artifacts = data.get("artifacts", [])
                    if artifacts:
                        b64_img = artifacts[0].get("base64", "")
                        if b64_img:
                            img_bytes = base64.b64decode(b64_img)
                            file = discord.File(io.BytesIO(img_bytes), filename="output.png")
                            await ctx.send(file=file)
                            return

                    img_url = data.get("data", [{}])[0].get("url") or data.get("image", "")
                    if img_url:
                        e = discord.Embed()
                        e.set_image(url=img_url)
                        await ctx.send(embed=e)
                        return

                    await ctx.send("-# image generation returned no data")
                    return
            except Exception as e:
                log.warning(f"{k_name} request failed: {e}")
                continue

        await ctx.send("-# image generation failed")


async def setup(bot: commands.Bot):
    await bot.add_cog(ImagineCog(bot))
