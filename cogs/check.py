import asyncio
import time
from typing import Optional

import aiohttp
import discord
from discord.ext import commands

from utils import help_meta

COG_META = {
    "category": "utility",
    "label": "Utility",
    "desc": "API status checks and utilities.",
}


class Check(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None

    async def cog_load(self):
        self.session = aiohttp.ClientSession()

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    @help_meta(
        usage="`.anilist`",
        desc="Checks AniList API status and latency.",
        examples=[".anilist"],
        params=[],
        note="No authentication required. Uses the AniList GraphQL API.",
    )
    @commands.command()
    async def anilist(self, ctx: commands.Context):
        """Check AniList API status."""
        url = "https://graphql.anilist.co"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # Minimal introspection query — always works, no auth needed, no custom UA.
        payload = {
            "query": "{ __typename }"
        }

        embed = discord.Embed(color=0x02A9FF)
        embed.set_author(
            name="AniList API Status",
            icon_url="https://anilist.co/img/icons/android-chrome-512x512.png",
        )

        start = time.perf_counter()
        try:
            async with self.session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                latency = round((time.perf_counter() - start) * 1000)
                rl_remaining = resp.headers.get("X-RateLimit-Remaining")
                rl_limit = resp.headers.get("X-RateLimit-Limit")

                if resp.status == 200:
                    try:
                        data = await resp.json()
                    except Exception:
                        data = None

                    has_data = isinstance(data, dict) and data.get("data")
                    has_errors = isinstance(data, dict) and bool(data.get("errors"))

                    if has_data and not has_errors:
                        embed.add_field(name="Status", value="🟢 Online", inline=True)
                        embed.add_field(name="Latency", value=f"{latency}ms", inline=True)
                    elif has_errors:
                        first_err = data["errors"][0].get("message", "unknown error")
                        embed.add_field(name="Status", value="🟡 Degraded", inline=True)
                        embed.add_field(name="Latency", value=f"{latency}ms", inline=True)
                        embed.add_field(
                            name="GraphQL Error", value=first_err[:200], inline=False
                        )
                    else:
                        embed.add_field(name="Status", value="🟡 Degraded", inline=True)
                        embed.add_field(name="Latency", value=f"{latency}ms", inline=True)

                elif resp.status == 429:
                    retry_after = resp.headers.get("Retry-After", "n/a")
                    embed.add_field(name="Status", value="🟡 Rate Limited", inline=True)
                    embed.add_field(name="Latency", value=f"{latency}ms", inline=True)
                    embed.add_field(
                        name="Retry After", value=f"{retry_after}s", inline=True
                    )

                elif resp.status in (500, 502, 503, 504):
                    embed.add_field(name="Status", value="🔴 Offline", inline=True)
                    embed.add_field(name="Code", value=f"HTTP {resp.status}", inline=True)
                    embed.add_field(name="Latency", value=f"{latency}ms", inline=True)

                elif resp.status in (400, 422):
                    # Server is up but rejected the query — count as degraded
                    embed.add_field(name="Status", value="🟡 Degraded", inline=True)
                    embed.add_field(name="Code", value=f"HTTP {resp.status}", inline=True)
                    embed.add_field(name="Latency", value=f"{latency}ms", inline=True)

                else:
                    embed.add_field(name="Status", value="🟡 Unknown", inline=True)
                    embed.add_field(name="Code", value=f"HTTP {resp.status}", inline=True)
                    embed.add_field(name="Latency", value=f"{latency}ms", inline=True)

                if rl_remaining and rl_limit:
                    embed.set_footer(text=f"Rate limit: {rl_remaining}/{rl_limit}")

        except asyncio.TimeoutError:
            embed.add_field(name="Status", value="🔴 Offline", inline=True)
            embed.add_field(name="Error", value="Request timed out (>10s)", inline=True)

        except aiohttp.ClientConnectorError as e:
            embed.add_field(name="Status", value="🔴 Offline", inline=True)
            embed.add_field(
                name="Error", value=f"Connection failed: {str(e)[:150]}", inline=True
            )

        except Exception as e:
            embed.add_field(name="Status", value="🔴 Error", inline=True)
            embed.add_field(name="Error", value=str(e)[:200], inline=True)

        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Check(bot))
