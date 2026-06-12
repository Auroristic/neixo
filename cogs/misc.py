import discord
from discord.ext import commands
import aiohttp
import asyncio
import random
import tempfile
import os
import time
import urllib.parse
import wavelink
from datetime import datetime, timezone

from utils import (
    load_json, save_json, get_embed_color, get_config, invalidate_config,
    log_audit, is_owner_or_creator, help_meta,
    check_gif_cooldown, gif_cooldown_msg,
    DATA_DIR, CONFIG_FILE, DM_WHITELIST_FILE, SEOULITIES_SERVER_ID
)

# ── cogs/misc.py ──────────────────────────────────────────────
COG_META = {
    "category": "general",
    "label": "General",
    "desc": "Core utility and reaction commands.",
}

async def _tcp_ping(host: str, port: int, timeout: float = 3.0):
    """TCP-connect ping. Returns connect time in ms, or None on failure/timeout."""
    try:
        t0 = time.perf_counter()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        elapsed = (time.perf_counter() - t0) * 1000
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return elapsed
    except Exception:
        return None


# fake "places" for .ping — pure flavor, no real network meaning
_PING_TARGETS = [
    "north korea", "draco", "the void", "no one", "god himself",
    "the moon", "mars", "absolutely nobody", "discord HQ", "the abyss",
    "narnia", "atlantis", "shawty", "ur mom", "the great wall",
    "chappy", "muixo", "lavalink", "the seoulities server", "bro",
    "wonderland", "the matrix", "skynet", "area 51", "antarctica",
]


# ── The Cog ───────────────────────────────────────────────────

class MiscCog(commands.Cog, name="Misc"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None

    async def cog_load(self):
        self.session = aiohttp.ClientSession()

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    # ── ping ────────────────────────────────────────────────
    @help_meta(
        usage="`.ping`",
        desc="Shows the bot's current WebSocket and round-trip latency.",
        examples=[".ping"],
        params=[],
        note="Displays both heartbeat latency and message edit RTT. The ping target is purely flavour text.",
    )
    @commands.command(name="ping")
    async def ping_prefix(self, ctx):
        # `ws_ms` = websocket heartbeat to Discord (bot.latency).
        # `rtt_ms` = round-trip of actually sending a message to Discord
        # and getting an ack back (perf_counter delta around ctx.send).
        # The bold target name is purely flavor — a random absurd "place"
        # the bot pretends to ping.
        ws_ms = round(self.bot.latency * 1000)
        target = random.choice(_PING_TARGETS)

        t0 = time.perf_counter()
        msg = await ctx.send(f"took `{ws_ms}ms` to ping **{target}**")
        rtt_ms = (time.perf_counter() - t0) * 1000
        try:
            # Ensure no accidental mentions trigger in the edit
            await msg.edit(
                content=f"took `{ws_ms}ms` to ping **{target}** (edit: `{rtt_ms:.1f}ms`)",
                allowed_mentions=discord.AllowedMentions.none()
            )
        except discord.HTTPException:
            pass

    # ── link ────────────────────────────────────────────────
    @help_meta(
        usage="`.link`",
        desc="Sends the Seoulities website link with an info embed.",
        examples=[".link"],
        params=[],
        note="The image is loaded from local assets.",
    )
    @commands.command(name="link")
    async def link(self, ctx):
        embed = discord.Embed(
            title="seoulities | anime & manhwa",
            description="server locked, anime n manhwa site w anilist n mal sync.",
            url="https://seoulities.com/",
            color=get_embed_color(ctx.guild.id if ctx.guild else 0),
        )
        # Local image (permanent — Discord CDN URLs expire). If the file
        # isn't on disk yet, fall back to a no-image embed instead of crashing.
        img_path = "assets/seoulities.png"
        file = None
        if os.path.isfile(img_path):
            embed.set_image(url="attachment://seoulities.png")
            file = discord.File(img_path, filename="seoulities.png")

        # Use specific user ID for footer info
        target_user = self.bot.get_user(1091368104405237890) or await self.bot.fetch_user(1091368104405237890)
        embed.set_footer(
            text=f"{target_user.name} · seoulities.com · discord.gg/seoulities",
            icon_url=target_user.display_avatar.url,
        )

        if file:
            await ctx.send(embed=embed, file=file)
        else:
            await ctx.send(embed=embed)

    # ── status ───────────────────────────────────────────────
    @help_meta(
        usage="`.status`",
        desc="Shows uptime, latency, memory usage, and Lavalink status.",
        examples=[".status"],
        params=[],
        note="Memory is read from /proc/self/status (Linux only). Lavalink RAM uses the /v4/stats endpoint.",
    )
    @commands.command(name="status")
    async def status(self, ctx):
        bot = self.bot

        # uptime
        start = getattr(bot, "start_time", None)
        if start:
            delta = discord.utils.utcnow() - start
            total = int(delta.total_seconds())
            d, rem = divmod(total, 86400)
            h, rem = divmod(rem, 3600)
            m, s = divmod(rem, 60)
            if d:
                uptime_str = f"{d}d {h}h {m}m"
            elif h:
                uptime_str = f"{h}h {m}m"
            elif m:
                uptime_str = f"{m}m {s}s"
            else:
                uptime_str = f"{s}s"
        else:
            uptime_str = "unknown"

        # memory (linux /proc; works under PM2 on ubuntu)
        rss_mb = 0.0
        try:
            with open("/proc/self/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss_mb = int(line.split()[1]) / 1024
                        break
        except (OSError, ValueError):
            pass

        # latency
        ws_ms = round(bot.latency * 1000)

        # lavalink: state · ping · RAM (when connected)
        try:
            nodes = list(wavelink.Pool.nodes.values())
        except Exception:
            nodes = []
        if nodes:
            node = nodes[0]
            ll_state = getattr(node.status, "name", str(node.status)).lower()
            ll_uri = os.getenv("LAVALINK_URI", "http://localhost:2333")
            ll_pass = os.getenv("LAVALINK_PASS", "youshallnotpass")
            ll_ping = None
            ll_ram = None
            if ll_state == "connected":
                # TCP ping to the node (cheap, ≤2s timeout)
                try:
                    parsed = urllib.parse.urlparse(ll_uri)
                    host = parsed.hostname or "localhost"
                    port = parsed.port or 2333
                    ll_ping = await _tcp_ping(host, port, timeout=2.0)
                except Exception:
                    pass
                # RAM via /v4/stats (the wavelink node already speaks this)
                try:
                    async with self.session.get(
                        f"{ll_uri.rstrip('/')}/v4/stats",
                        headers={"Authorization": ll_pass},
                        timeout=aiohttp.ClientTimeout(total=2),
                    ) as r:
                        if r.status == 200:
                            data = await r.json()
                            used = (data.get("memory") or {}).get("used", 0)
                            ll_ram = used / (1024 * 1024)
                except Exception:
                    pass
            ll_str = ll_state
            if ll_ping is not None:
                ll_str += f" · `{ll_ping:.1f}ms`"
            if ll_ram is not None:
                ll_str += f" · `{ll_ram:.0f}MB`"
        else:
            ll_str = "no node"

        # active voice connections
        vcs = len(bot.voice_clients)

        # seoulities guild context
        guild = bot.get_guild(SEOULITIES_SERVER_ID)
        color = get_embed_color(guild.id) if guild else 0xFF0000

        embed = discord.Embed(
            title="status",
            description=f"-# active in **{guild.name}**" if guild else "-# active",
            color=color,
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        embed.add_field(name="uptime",    value=uptime_str,         inline=True)
        embed.add_field(name="latency",   value=f"{ws_ms}ms",       inline=True)
        embed.add_field(name="memory",    value=f"{rss_mb:.1f} MB", inline=True)
        embed.add_field(name="lavalink",  value=ll_str,             inline=True)
        embed.add_field(name="voice",     value=str(vcs),           inline=True)
        embed.add_field(name="members",   value=str(guild.member_count) if guild else "—", inline=True)

        embed.set_footer(
            text=f"{bot.user.name} • dev: fw_u",
            icon_url=bot.user.display_avatar.url,
        )

        await ctx.send(embed=embed)

    # ── random ───────────────────────────────────────────────
    @help_meta(
        usage="`.random`",
        desc="Picks a random non-bot member in the server and mentions them.",
        examples=[".random"],
        params=[],
        note="Has a GIF cooldown per user.",
    )
    @commands.command(name='random')
    async def random_member(self, ctx):
        cooldown = check_gif_cooldown(ctx.author.id)
        if cooldown:
            if cooldown != "silent":
                return await ctx.send(gif_cooldown_msg(int(cooldown)))
            return
        members = [m for m in ctx.guild.members if not m.bot]
        if not members:
            await ctx.send("No members found.")
            return
        member = random.choice(members)
        await ctx.send(f"{member.mention}")

    # ── dm ─────────────────────────────────────────────────
    @help_meta(
        usage="`.dm @user <message>`",
        desc="Sends a DM to a user as the bot.",
        examples=[".dm @fw_u hello!"],
        params=[
            {"name": "user", "type": "discord.User", "required": True, "desc": "The user to DM."},
            {"name": "message", "type": "str", "required": True, "desc": "The message content to send."},
        ],
        note="Staff only. Requires whitelist.",
        staff=True,
    )
    @commands.command(name='dm')
    async def dm_user(self, ctx, user: discord.User, *, message: str):
        if ctx.guild:
            guild_id = str(ctx.guild.id)
        else:
            guild_id = str(SEOULITIES_SERVER_ID)
        
        # Single config load for all checks (was loading multiple times before)
        config = get_config()
        guild_config = config.get(guild_id, {})
        whitelist = guild_config.get('whitelist', [])
        
        if str(ctx.author.id) not in whitelist:
            await ctx.send("no perms?")
            return
        
        try:
            await user.send(message)
            await ctx.message.add_reaction("<:7079verifiedblacksimplified:1255031445806780467>")
        except discord.Forbidden:
            await ctx.send("null")
        except Exception as e:
            await ctx.send(f"dude : {str(e)}")

    # ── dmcheck ──────────────────────────────────────────────
    @help_meta(
        usage="`.dmcheck @user`",
        desc="Exports DM history with a user as a .txt file.",
        examples=[".dmcheck @fw_u"],
        params=[
            {"name": "user", "type": "discord.User", "required": True, "desc": "The user whose DM history to export."},
        ],
        note="Staff only. Fetches up to 100 most recent messages.",
        staff=True,
    )
    @commands.command(name='dmcheck')
    async def dm_check(self, ctx, user: discord.User):
        if ctx.guild:
            guild_id = str(ctx.guild.id)
        else:
            guild_id = str(SEOULITIES_SERVER_ID)
        
        # Single config load for all checks (was loading multiple times before)
        config = get_config()
        guild_config = config.get(guild_id, {})
        whitelist = guild_config.get('whitelist', [])
        
        if str(ctx.author.id) not in whitelist:
            await ctx.send("no perms?")
            return
        
        try:
            dm_channel = user.dm_channel
            if not dm_channel:
                dm_channel = await user.create_dm()
            
            messages = [msg async for msg in dm_channel.history(limit=100, oldest_first=True)]
            
            if not messages:
                return await ctx.send("no messages found")
            
            content = f"DM History with {user.name} — last {len(messages)} messages\n"
            content += f"fetched at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
            
            for msg in messages:
                timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
                text = msg.content or ""
                if msg.attachments:
                    text += " " + " ".join(f"[attachment: {a.filename}]" for a in msg.attachments)
                if msg.embeds:
                    text += " [embed]"
                content += f"[{timestamp}] {msg.author.name}: {text}\n"
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
                f.write(content)
                tmppath = f.name

            try:
                await ctx.send(file=discord.File(tmppath, filename=f"dm_{user.name}.txt"))
            finally:
                os.remove(tmppath)
            
        except Exception as e:
            await ctx.send(f"error: {str(e)}")

    # ── Echo modal ────────────────────────────────────────────

class EchoModal(discord.ui.Modal, title="Echo Message"):
    message_text = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        placeholder="Type your message here...",
        required=True,
        max_length=2000
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.channel.send(self.message_text.value)
            await interaction.response.send_message(
                "✅ Message sent!",
                ephemeral=True
            )
            log_audit("echo_command", interaction.guild_id, interaction.user.id, 
                     f"Channel: {interaction.channel.id}, Message: {self.message_text.value[:50]}")
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Failed to send message: {str(e)}",
                ephemeral=True
            )

class EchoButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Open Echo Modal", style=discord.ButtonStyle.primary, custom_id="echo_modal_trigger")
    async def echo_modal_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = EchoModal()
        await interaction.response.send_modal(modal)

class EchoCog(commands.Cog):
    COG_META = {
        "category": "misc",
        "label": "Echo",
        "desc": "Staff utility to echo messages through the bot.",
        "staff": True,
    }

    def __init__(self, bot):
        self.bot = bot

    @help_meta(
        usage="`.echo <message>`",
        desc="Opens a modal to send a message as the bot.",
        examples=[".echo"],
        params=[],
        note="Admin only. Opens an interactive modal for message input.",
        admin=True,
    )
    @commands.command(name="echo")
    async def echo_prefix(self, ctx):
        if not ctx.guild:
            return
        if not is_owner_or_creator(ctx) and not ctx.author.guild_permissions.administrator:
            await ctx.send("admin only")
            return

        view = EchoButton()
        await ctx.send("Click below to open the echo modal:", view=view)

async def setup(bot: commands.Bot):
    await bot.add_cog(MiscCog(bot))
    await bot.add_cog(EchoCog(bot))