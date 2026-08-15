"""
cogs/utility_tools.py  —  utility tools: live interactive polls, safe calc, timezones, urban dictionary, weather
"""

import ast
import asyncio
import logging
import math
import operator as op
import os
import urllib.parse
from datetime import datetime, timezone

import aiohttp
import aiosqlite
import discord
from discord.ext import commands

from utils import DATA_DIR, get_embed_color, help_meta

log = logging.getLogger(__name__)

TZ_DB_PATH = os.path.join(DATA_DIR, "timezones.db")

COG_META = {
    "category": "utility",
    "label": "Utility",
    "desc": "Utility tools, live polls, calculations, weather, and timezones.",
}


# ── Safe AST Math Evaluator ─────────────────────────────────────────
_SAFE_OPERATORS = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.FloorDiv: op.floordiv,
    ast.Mod: op.mod,
    ast.Pow: op.pow,
    ast.USub: op.neg,
    ast.UAdd: op.pos,
}

_SAFE_FUNCTIONS = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "abs": abs,
    "round": round,
    "floor": math.floor,
    "ceil": math.ceil,
    "log": math.log,
    "log10": math.log10,
    "pi": math.pi,
    "e": math.e,
}


def _eval_ast(node):
    if isinstance(node, ast.Expression):
        return _eval_ast(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("unsupported literal")
    if isinstance(node, ast.BinOp):
        left = _eval_ast(node.left)
        right = _eval_ast(node.right)
        op_type = type(node.op)
        if op_type not in _SAFE_OPERATORS:
            raise ValueError("unsupported operator")
        if op_type is ast.Pow and (right > 100 or left > 1000000):
            raise ValueError("exponent too large")
        return _SAFE_OPERATORS[op_type](left, right)
    if isinstance(node, ast.UnaryOp):
        operand = _eval_ast(node.operand)
        op_type = type(node.op)
        if op_type not in _SAFE_OPERATORS:
            raise ValueError("unsupported operator")
        return _SAFE_OPERATORS[op_type](operand)
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in _SAFE_FUNCTIONS:
            args = [_eval_ast(a) for a in node.args]
            func = _SAFE_FUNCTIONS[node.func.id]
            if callable(func):
                return func(*args)
            return func
        raise ValueError("unsupported function")
    if isinstance(node, ast.Name) and node.id in _SAFE_FUNCTIONS:
        return _SAFE_FUNCTIONS[node.id]
    raise ValueError("invalid expression")


def safe_eval(expr: str) -> float | int:
    clean = expr.replace("^", "**").replace("×", "*").replace("÷", "/")
    tree = ast.parse(clean, mode="eval")
    return _eval_ast(tree)


# ── Interactive Poll View ──────────────────────────────────────────
_POLL_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]


class PollView(discord.ui.View):
    def __init__(self, question: str, options: list[str], author_id: int):
        super().__init__(timeout=86400)
        self.question = question
        self.options = options
        self.author_id = author_id
        # user_id -> option_index
        self.votes: dict[int, int] = {}
        self.closed = False

        for i, opt in enumerate(options):
            btn = discord.ui.Button(
                label=opt[:80],
                emoji=_POLL_EMOJIS[i] if i < len(_POLL_EMOJIS) else None,
                style=discord.ButtonStyle.secondary,
                custom_id=f"poll_{i}",
            )
            btn.callback = self._make_callback(i)
            self.add_item(btn)

    def _make_callback(self, idx: int):
        async def callback(interaction: discord.Interaction):
            if self.closed:
                return await interaction.response.send_message("-# this poll is closed", ephemeral=True)
            uid = interaction.user.id
            if self.votes.get(uid) == idx:
                self.votes.pop(uid, None)
                await interaction.response.send_message("-# vote removed", ephemeral=True)
            else:
                self.votes[uid] = idx
                await interaction.response.send_message(f"-# voted for **{self.options[idx]}**", ephemeral=True)

            try:
                await interaction.message.edit(embed=self.build_embed(interaction.guild_id or 0), view=self)
            except discord.HTTPException:
                pass

        return callback

    def build_embed(self, guild_id: int) -> discord.Embed:
        total = len(self.votes)
        counts = [list(self.votes.values()).count(i) for i in range(len(self.options))]

        lines = []
        for i, opt in enumerate(self.options):
            cnt = counts[i]
            pct = round((cnt / total) * 100) if total > 0 else 0
            filled = round(pct / 10)
            bar = "█" * filled + "░" * (10 - filled)
            emoji = _POLL_EMOJIS[i] if i < len(_POLL_EMOJIS) else "•"
            lines.append(f"{emoji} **{opt}**\n`{bar}` `{pct}%` ({cnt})")

        embed = discord.Embed(
            title=f"📊 {self.question}",
            description="\n\n".join(lines),
            color=get_embed_color(guild_id),
        )
        status = "closed" if self.closed else f"{total} total votes · click a button to vote"
        embed.set_footer(text=status)
        return embed


# ── The Utility Cog ────────────────────────────────────────────────
class UtilityTools(commands.Cog, name="UtilityTools"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: aiosqlite.Connection | None = None

    async def cog_load(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.db = await aiosqlite.connect(TZ_DB_PATH)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA synchronous=NORMAL")
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS timezones (
                user_id INTEGER PRIMARY KEY,
                tz_name TEXT NOT NULL,
                offset_hours REAL NOT NULL
            )
        """)
        await self.db.commit()

    async def cog_unload(self):
        if self.db:
            await self.db.close()
            self.db = None

    # ── Poll ────────────────────────────────────────────────────────
    @commands.command(name="poll")
    @commands.cooldown(1, 10, commands.BucketType.channel)
    @help_meta(
        usage='`.poll <"Question"> <"Option 1"> <"Option 2"> ...`',
        desc="Creates an interactive button poll with live percentage breakdown.",
        section="Utility",
        perm_tier="public",
        examples=[
            '.poll "Favorite game?" "Valorant" "Minecraft" "Genshin"',
            '.poll "Pizza or Burgers?" "Pizza" "Burgers"',
        ],
        params=[
            {"name": "question", "type": "str", "required": True, "desc": "The poll question (in quotes)."},
            {"name": "options", "type": "str", "required": True, "desc": "2 to 5 options (each in quotes)."},
        ],
        note="Supports up to 5 options. Live votes update dynamically on click.",
    )
    async def poll(self, ctx: commands.Context, *, raw_args: str = None):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if not raw_args:
            return await ctx.send('-# usage: `.poll "Question" "Option 1" "Option 2"`')

        # Split quoted arguments
        import shlex
        try:
            tokens = shlex.split(raw_args)
        except ValueError:
            tokens = [p.strip().strip('"').strip("'") for p in raw_args.split('"') if p.strip()]

        if len(tokens) < 3:
            return await ctx.send('-# provide a question and at least 2 options: `.poll "Question" "Option 1" "Option 2"`')

        question = tokens[0]
        options = tokens[1:6]

        view = PollView(question, options, ctx.author.id)
        embed = view.build_embed(ctx.guild.id)
        await ctx.send(embed=embed, view=view)

    # ── Safe Calc ───────────────────────────────────────────────────
    @commands.command(name="calc", aliases=["math", "calculate"])
    @commands.cooldown(3, 5, commands.BucketType.user)
    @help_meta(
        usage="`.calc <expression>`",
        desc="Evaluates mathematical expressions safely using an AST evaluator.",
        section="Utility",
        perm_tier="public",
        examples=[".calc 2 + 2", ".calc sqrt(144) * 3", ".calc (50 * 1.15) / 2"],
        params=[{"name": "expression", "type": "str", "required": True, "desc": "Math expression to evaluate."}],
        note="Supports +, -, *, /, %, ^, sqrt, sin, cos, tan, abs, round, log.",
    )
    async def calc(self, ctx: commands.Context, *, expr: str = None):
        if not expr:
            return await ctx.send("-# usage: `.calc <expression>` — e.g. `.calc 15 * 8 + sqrt(144)`")
        try:
            res = safe_eval(expr)
            # Format nicely
            if isinstance(res, float) and res.is_integer():
                res = int(res)
            elif isinstance(res, float):
                res = round(res, 6)
            await ctx.send(f"-# result: `{res:,}`" if isinstance(res, (int, float)) and abs(res) >= 1000 else f"-# result: `{res}`")
        except Exception as e:
            await ctx.send(f"-# math error: {str(e).lower()}")

    # ── Timezone ────────────────────────────────────────────────────
    @commands.command(name="timezone", aliases=["tz", "settimezone"])
    @commands.cooldown(2, 5, commands.BucketType.user)
    @help_meta(
        usage="`.timezone [timezone_name|offset]`  ·  `.timezone @user`",
        desc="Sets your timezone or checks another member's current local time.",
        section="Utility",
        perm_tier="public",
        examples=[".timezone EST", ".timezone UTC+5:30", ".timezone America/New_York", ".timezone @someone"],
        params=[{"name": "timezone", "type": "str", "required": False, "desc": "Timezone abbreviation, offset (+5, -4), or @user."}],
    )
    async def timezone_cmd(self, ctx: commands.Context, *, target_or_tz: str = None):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")

        # Check if inspecting another user
        if ctx.message.mentions:
            target = ctx.message.mentions[0]
            if not self.db:
                return await ctx.send("-# database unavailable")
            async with self.db.execute("SELECT tz_name, offset_hours FROM timezones WHERE user_id = ?", (target.id,)) as cur:
                row = await cur.fetchone()
            if not row:
                return await ctx.send(f"-# {target.display_name} hasn't set their timezone yet")
            tz_name, offset = row
            now_utc = datetime.now(timezone.utc)
            from datetime import timedelta
            local_time = now_utc + timedelta(hours=offset)
            time_str = local_time.strftime("%I:%M %p (%a, %b %d)")
            return await ctx.send(f"-# it's currently **{time_str}** for {target.display_name} (`{tz_name}`)")

        if not target_or_tz:
            # Check own timezone
            if not self.db:
                return await ctx.send("-# database unavailable")
            async with self.db.execute("SELECT tz_name, offset_hours FROM timezones WHERE user_id = ?", (ctx.author.id,)) as cur:
                row = await cur.fetchone()
            if not row:
                return await ctx.send("-# you haven't set a timezone yet. usage: `.timezone EST` or `.timezone UTC+5:30`")
            tz_name, offset = row
            now_utc = datetime.now(timezone.utc)
            from datetime import timedelta
            local_time = now_utc + timedelta(hours=offset)
            time_str = local_time.strftime("%I:%M %p (%a, %b %d)")
            return await ctx.send(f"-# your local time: **{time_str}** (`{tz_name}`)")

        # Setting timezone
        raw = target_or_tz.strip().upper()
        offset = 0.0
        tz_label = target_or_tz.strip()

        _KNOWN_TZ = {
            "UTC": 0.0, "GMT": 0.0,
            "EST": -5.0, "EDT": -4.0,
            "CST": -6.0, "CDT": -5.0,
            "MST": -7.0, "MDT": -6.0,
            "PST": -8.0, "PDT": -7.0,
            "AKST": -9.0, "HST": -10.0,
            "BST": 1.0, "CET": 1.0, "CEST": 2.0,
            "EET": 2.0, "EEST": 3.0,
            "MSK": 3.0, "IST": 5.5,
            "ICT": 7.0, "SGT": 8.0, "CST_CHINA": 8.0,
            "JST": 9.0, "KST": 9.0, "AEST": 10.0, "AEDT": 11.0,
        }

        if raw in _KNOWN_TZ:
            offset = _KNOWN_TZ[raw]
        elif "UTC" in raw or "GMT" in raw or raw.startswith(("+", "-")):
            clean_off = raw.replace("UTC", "").replace("GMT", "").strip()
            try:
                if ":" in clean_off:
                    h, m = clean_off.split(":", 1)
                    sign = -1 if h.startswith("-") else 1
                    offset = float(h) + (sign * float(m) / 60.0)
                else:
                    offset = float(clean_off)
            except ValueError:
                return await ctx.send("-# couldn't parse timezone. try `.timezone EST` or `.timezone UTC+5:30`")
        else:
            return await ctx.send("-# couldn't recognize timezone. try `.timezone EST`, `PST`, `GMT`, `IST`, or `UTC+2`")

        if not self.db:
            return await ctx.send("-# database unavailable")

        await self.db.execute(
            "INSERT OR REPLACE INTO timezones (user_id, tz_name, offset_hours) VALUES (?, ?, ?)",
            (ctx.author.id, tz_label, offset),
        )
        await self.db.commit()
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")

    # ── Urban Dictionary ────────────────────────────────────────────
    @commands.command(name="urban", aliases=["ud", "define"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    @help_meta(
        usage="`.urban <query>`",
        desc="Looks up slang definitions and terms on Urban Dictionary.",
        section="Utility",
        perm_tier="public",
        examples=[".urban rizz", ".urban cap", ".urban ghosting"],
        params=[{"name": "query", "type": "str", "required": True, "desc": "The slang or term to search for."}],
    )
    async def urban(self, ctx: commands.Context, *, query: str = None):
        if not query:
            return await ctx.send("-# usage: `.urban <query>`")

        url = f"https://api.urbandictionary.com/v0/define?term={urllib.parse.quote(query)}"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status != 200:
                        return await ctx.send("-# urban dictionary unavailable")
                    data = await r.json()
                    definitions = data.get("list", [])
                    if not definitions:
                        return await ctx.send(f"-# no definitions found for **{query}**")
                    top = definitions[0]
        except Exception:
            return await ctx.send("-# urban dictionary unavailable")

        # Strip brackets from urban dictionary text
        def_text = top.get("definition", "").replace("[", "").replace("]", "")
        example = top.get("example", "").replace("[", "").replace("]", "")
        thumbs_up = top.get("thumbs_up", 0)
        thumbs_down = top.get("thumbs_down", 0)

        embed = discord.Embed(
            title=f"📖 {top.get('word', query)}",
            url=top.get("permalink", ""),
            description=def_text[:1200] if def_text else "*no definition*",
            color=get_embed_color(ctx.guild.id if ctx.guild else 0),
        )
        if example:
            embed.add_field(name="example", value=f"*{example[:500]}*", inline=False)
        embed.set_footer(text=f"👍 {thumbs_up:,} · 👎 {thumbs_down:,} · by {top.get('author', 'anonymous')}")
        await ctx.send(embed=embed)

    # ── Weather ─────────────────────────────────────────────────────
    @commands.command(name="weather", aliases=["temp", "forecast"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    @help_meta(
        usage="`.weather <city>`",
        desc="Fetches current weather conditions and temperature for any location.",
        section="Utility",
        perm_tier="public",
        examples=[".weather London", ".weather Tokyo", ".weather New York"],
        params=[{"name": "city", "type": "str", "required": True, "desc": "City or country name."}],
    )
    async def weather(self, ctx: commands.Context, *, city: str = None):
        if not city:
            return await ctx.send("-# usage: `.weather <city>`")

        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status != 200:
                        return await ctx.send("-# weather service unavailable")
                    data = await r.json()
                    current = data.get("current_condition", [{}])[0]
                    area = data.get("nearest_area", [{}])[0]
        except Exception:
            return await ctx.send("-# weather service unavailable")

        temp_c = current.get("temp_C", "?")
        temp_f = current.get("temp_F", "?")
        feels_c = current.get("FeelsLikeC", "?")
        feels_f = current.get("FeelsLikeF", "?")
        desc = (current.get("weatherDesc", [{}])[0].get("value") or "clear").lower()
        humidity = current.get("humidity", "?")
        wind_kmph = current.get("windspeedKmph", "?")
        city_name = area.get("areaName", [{}])[0].get("value") or city
        country = area.get("country", [{}])[0].get("value") or ""

        embed = discord.Embed(
            title=f"🌤️ {city_name}, {country}",
            description=f"**{desc}**\n\n🌡️ **Temperature:** {temp_c}°C / {temp_f}°F\n🤔 **Feels like:** {feels_c}°C / {feels_f}°F\n💧 **Humidity:** {humidity}%\n💨 **Wind:** {wind_kmph} km/h",
            color=get_embed_color(ctx.guild.id if ctx.guild else 0),
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(UtilityTools(bot))
