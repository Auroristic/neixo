"""
cogs/social.py  —  social interactions, marriage system, and interactive mini-games
"""

import asyncio
import html
import logging
import os
import random
import re
from datetime import datetime, timezone

import aiohttp
import aiosqlite
import discord
from discord.ext import commands

from utils import DATA_DIR, get_embed_color, help_meta

log = logging.getLogger(__name__)

DB_PATH = os.path.join(DATA_DIR, "social.db")

COG_META = {
    "category": "fun",
    "label": "Fun",
    "desc": "Social, marriage, mini-games, and community interactions.",
}

# ── 8ball responses (minimalist aesthetic) ─────────────────────────
EIGHTBALL_RESPONSES = [
    "yes",
    "most likely",
    "definitely",
    "without a doubt",
    "signs point to yes",
    "ask again later",
    "cannot predict now",
    "don't count on it",
    "my sources say no",
    "very doubtful",
    "no",
    "absolutely not",
]

# ── curated Would You Rather questions ────────────────────────────
WYR_QUESTIONS = [
    ("have the ability to fly", "have the ability to be invisible"),
    ("always be 10 minutes late", "always be 20 minutes early"),
    ("live in a futuristic cyberpunk city", "live in a peaceful secluded forest cottage"),
    ("know the history of every object you touch", "be able to talk to animals"),
    ("never have to sleep again", "never have to eat again without feeling hungry"),
    ("explore deep space", "explore the deepest trenches of the ocean"),
    ("be able to rewind time by 10 seconds", "be able to freeze time for 5 seconds"),
    ("always know when someone is lying", "always get away with any lie"),
    ("lose all your memories from yesterday", "never be able to make new long-term memories"),
    ("have infinite wealth but no internet", "have average wealth with high-speed internet forever"),
    ("be able to speak every human language", "be able to master every musical instrument"),
    ("live one life for 1,000 years", "live 10 different lives for 100 years each"),
]


# ── Marriage Proposal View ─────────────────────────────────────────
class MarryProposalView(discord.ui.View):
    def __init__(self, author: discord.Member, target: discord.Member, cog: "Social"):
        super().__init__(timeout=60)
        self.author = author
        self.target = target
        self.cog = cog
        self.value = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("-# this proposal isn't for you", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="accept", style=discord.ButtonStyle.secondary, emoji="💍")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        self.value = True
        await self.cog.create_marriage(self.author.id, self.target.id, interaction.guild_id or 0)
        await interaction.response.edit_message(
            content=f"-# {self.author.mention} and {self.target.mention} are now married 💍",
            view=self,
        )
        self.stop()

    @discord.ui.button(label="decline", style=discord.ButtonStyle.secondary, emoji="💔")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        self.value = False
        await interaction.response.edit_message(
            content=f"-# {self.target.mention} declined {self.author.mention}'s proposal",
            view=self,
        )
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        self.stop()


# ── Interactive Rock-Paper-Scissors View ──────────────────────────
class RPSView(discord.ui.View):
    CHOICES = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
    BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}

    def __init__(self, author: discord.Member, opponent: discord.Member | None = None):
        super().__init__(timeout=45)
        self.author = author
        self.opponent = opponent
        self.p1_choice: str | None = None
        self.p2_choice: str | None = None
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        allowed = [self.author.id]
        if self.opponent:
            allowed.append(self.opponent.id)
        if interaction.user.id not in allowed:
            await interaction.response.send_message("-# not your game", ephemeral=True)
            return False
        return True

    async def _handle_choice(self, interaction: discord.Interaction, choice: str):
        if self.opponent is None:
            # Solo vs Bot
            bot_choice = random.choice(list(self.CHOICES.keys()))
            for item in self.children:
                item.disabled = True

            if choice == bot_choice:
                res = f"tie — both chose {self.CHOICES[choice]}"
            elif self.BEATS[choice] == bot_choice:
                res = f"you won — {self.CHOICES[choice]} beats {self.CHOICES[bot_choice]}"
            else:
                res = f"you lost — {self.CHOICES[bot_choice]} beats {self.CHOICES[choice]}"

            await interaction.response.edit_message(content=f"-# {res}", view=self)
            self.stop()
            return

        # 2-player mode
        if interaction.user.id == self.author.id:
            if self.p1_choice is not None:
                return await interaction.response.send_message("-# you already picked", ephemeral=True)
            self.p1_choice = choice
            await interaction.response.send_message(f"-# you selected {self.CHOICES[choice]}", ephemeral=True)
        else:
            if self.p2_choice is not None:
                return await interaction.response.send_message("-# you already picked", ephemeral=True)
            self.p2_choice = choice
            await interaction.response.send_message(f"-# you selected {self.CHOICES[choice]}", ephemeral=True)

        if self.p1_choice and self.p2_choice:
            for item in self.children:
                item.disabled = True

            c1, c2 = self.p1_choice, self.p2_choice
            e1, e2 = self.CHOICES[c1], self.CHOICES[c2]

            if c1 == c2:
                winner_text = f"tie — both picked {e1}"
            elif self.BEATS[c1] == c2:
                winner_text = f"{self.author.mention} won ({e1} beats {e2})"
            else:
                winner_text = f"{self.opponent.mention} won ({e2} beats {e1})"

            if self.message:
                try:
                    await self.message.edit(content=f"-# {winner_text}", view=self)
                except discord.HTTPException:
                    pass
            self.stop()

    @discord.ui.button(label="rock", style=discord.ButtonStyle.secondary, emoji="🪨")
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_choice(interaction, "rock")

    @discord.ui.button(label="paper", style=discord.ButtonStyle.secondary, emoji="📄")
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_choice(interaction, "paper")

    @discord.ui.button(label="scissors", style=discord.ButtonStyle.secondary, emoji="✂️")
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_choice(interaction, "scissors")


# ── Interactive Trivia View ────────────────────────────────────────
class TriviaView(discord.ui.View):
    def __init__(self, author: discord.Member, correct_answer: str, all_options: list[str]):
        super().__init__(timeout=25)
        self.author = author
        self.correct_answer = correct_answer
        self.answered = False

        for opt in all_options:
            btn = discord.ui.Button(
                label=opt[:80],
                style=discord.ButtonStyle.secondary,
                custom_id=f"trivia_{opt}",
            )
            btn.callback = self._make_callback(opt)
            self.add_item(btn)

    def _make_callback(self, chosen: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.author.id:
                return await interaction.response.send_message("-# not your trivia question", ephemeral=True)
            if self.answered:
                return
            self.answered = True
            for item in self.children:
                item.disabled = True
                if getattr(item, "label", "") == self.correct_answer:
                    item.style = discord.ButtonStyle.success
                elif getattr(item, "label", "") == chosen:
                    item.style = discord.ButtonStyle.danger

            if chosen == self.correct_answer:
                msg = f"-# correct — **{self.correct_answer}**"
            else:
                msg = f"-# wrong — the correct answer was **{self.correct_answer}**"

            await interaction.response.edit_message(content=msg, view=self)
            self.stop()

        return callback


# ── Would You Rather View ──────────────────────────────────────────
class WYRView(discord.ui.View):
    def __init__(self, opt1: str, opt2: str):
        super().__init__(timeout=120)
        self.opt1 = opt1
        self.opt2 = opt2
        self.votes1: set[int] = set()
        self.votes2: set[int] = set()

    def _render_content(self) -> str:
        t1 = len(self.votes1)
        t2 = len(self.votes2)
        total = t1 + t2
        p1 = round((t1 / total) * 100) if total > 0 else 50
        p2 = 100 - p1 if total > 0 else 50
        return (
            f"**would you rather...**\n\n"
            f"🅰️ **{self.opt1}** — `{p1}%` ({t1} votes)\n"
            f"🅱️ **{self.opt2}** — `{p2}%` ({t2} votes)"
        )

    @discord.ui.button(label="option a", style=discord.ButtonStyle.secondary, emoji="🅰️")
    async def btn_a(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        self.votes2.discard(uid)
        self.votes1.add(uid)
        await interaction.response.edit_message(content=self._render_content(), view=self)

    @discord.ui.button(label="option b", style=discord.ButtonStyle.secondary, emoji="🅱️")
    async def btn_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        self.votes1.discard(uid)
        self.votes2.add(uid)
        await interaction.response.edit_message(content=self._render_content(), view=self)


# ── The Social Cog ─────────────────────────────────────────────────
class Social(commands.Cog, name="Social"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: aiosqlite.Connection | None = None

    async def cog_load(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.db = await aiosqlite.connect(DB_PATH)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA synchronous=NORMAL")
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS marriages (
                user1_id INTEGER NOT NULL,
                user2_id INTEGER NOT NULL,
                married_at TEXT NOT NULL,
                guild_id INTEGER NOT NULL,
                PRIMARY KEY (user1_id, user2_id)
            )
        """)
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_marriages_u1 ON marriages(user1_id)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_marriages_u2 ON marriages(user2_id)")
        await self.db.commit()

    async def cog_unload(self):
        if self.db:
            await self.db.close()
            self.db = None

    async def get_marriage(self, user_id: int) -> tuple[int, str, int] | None:
        """Returns (partner_id, married_at_iso, guild_id) or None."""
        if not self.db:
            return None
        async with self.db.execute(
            "SELECT user2_id, married_at, guild_id FROM marriages WHERE user1_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return row[0], row[1], row[2]

        async with self.db.execute(
            "SELECT user1_id, married_at, guild_id FROM marriages WHERE user2_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return row[0], row[1], row[2]
        return None

    async def create_marriage(self, u1: int, u2: int, guild_id: int):
        if not self.db:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "INSERT OR REPLACE INTO marriages (user1_id, user2_id, married_at, guild_id) VALUES (?, ?, ?, ?)",
            (u1, u2, now_iso, guild_id),
        )
        await self.db.commit()

    async def delete_marriage(self, user_id: int):
        if not self.db:
            return
        await self.db.execute(
            "DELETE FROM marriages WHERE user1_id = ? OR user2_id = ?",
            (user_id, user_id),
        )
        await self.db.commit()

    # ── Marriage Commands ───────────────────────────────────────────
    @commands.command(name="marry", aliases=["propose"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    @help_meta(
        usage="`.marry <@user>`",
        desc="Proposes to another server member with an interactive wedding proposal.",
        section="Fun",
        perm_tier="public",
        examples=[".marry @someone"],
        params=[
            {"name": "user", "type": "user", "required": True, "desc": "Member to propose to."},
        ],
        note="Target user must click accept within 60 seconds.",
    )
    async def marry(self, ctx: commands.Context, user: discord.Member = None):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if user is None:
            return await ctx.send("-# usage: `.marry <@user>`")
        if user.id == ctx.author.id:
            return await ctx.send("-# you can't marry yourself")
        if user.bot:
            return await ctx.send("-# you can't marry a bot")

        m1 = await self.get_marriage(ctx.author.id)
        if m1:
            return await ctx.send(f"-# you're already married to <@{m1[0]}>. use `.divorce` first")

        m2 = await self.get_marriage(user.id)
        if m2:
            return await ctx.send(f"-# {user.display_name} is already married to <@{m2[0]}>")

        view = MarryProposalView(ctx.author, user, self)
        await ctx.send(
            f"{user.mention}, **{ctx.author.display_name}** has proposed to you 💍",
            view=view,
        )

    @commands.command(name="divorce")
    @commands.cooldown(1, 10, commands.BucketType.user)
    @help_meta(
        usage="`.divorce`",
        desc="Divorces your current married partner.",
        section="Fun",
        perm_tier="public",
        examples=[".divorce"],
        params=[],
        note="Ends active marriage immediately.",
    )
    async def divorce(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        m = await self.get_marriage(ctx.author.id)
        if not m:
            return await ctx.send("-# you aren't married to anyone")

        partner_id = m[0]
        await self.delete_marriage(ctx.author.id)
        await ctx.send(f"-# you and <@{partner_id}> are now divorced 💔")

    @commands.command(name="marriage", aliases=["marrystatus"])
    @commands.cooldown(2, 5, commands.BucketType.user)
    @help_meta(
        usage="`.marriage [@user]`",
        desc="Displays marriage status, partner, date, and anniversary duration.",
        section="Fun",
        perm_tier="public",
        examples=[".marriage", ".marriage @someone"],
        params=[
            {"name": "user", "type": "user", "required": False, "desc": "Member to check. Defaults to you."},
        ],
    )
    async def marriage(self, ctx: commands.Context, user: discord.Member = None):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        target = user or ctx.author
        m = await self.get_marriage(target.id)
        if not m:
            return await ctx.send(f"-# {target.display_name} is not married")

        partner_id, iso_str, _ = m
        try:
            married_dt = datetime.fromisoformat(iso_str)
            days = (datetime.now(timezone.utc) - married_dt).days
            duration = f"{days} days" if days != 1 else "1 day"
        except Exception:
            duration = "some time"

        partner = ctx.guild.get_member(partner_id) or self.bot.get_user(partner_id)
        partner_name = partner.display_name if partner else f"User {partner_id}"

        embed = discord.Embed(
            description=f"💍 **{target.display_name}** is married to **{partner_name}**\n-# together for {duration}",
            color=get_embed_color(ctx.guild.id),
        )
        await ctx.send(embed=embed)

    @commands.command(name="marriages", aliases=["marrylist"])
    @commands.cooldown(1, 10, commands.BucketType.channel)
    @help_meta(
        usage="`.marriages`",
        desc="Lists all active marriages in this server.",
        section="Fun",
        perm_tier="public",
        examples=[".marriages"],
        params=[],
    )
    async def marrylist(self, ctx: commands.Context):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if not self.db:
            return await ctx.send("-# no marriage records found")

        async with self.db.execute(
            "SELECT user1_id, user2_id, married_at FROM marriages WHERE guild_id = ?",
            (ctx.guild.id,),
        ) as cur:
            rows = await cur.fetchall()

        if not rows:
            return await ctx.send("-# no marriages in this server yet")

        lines = []
        for u1, u2, iso_str in rows[:20]:
            try:
                days = (datetime.now(timezone.utc) - datetime.fromisoformat(iso_str)).days
                d_str = f"{days}d"
            except Exception:
                d_str = "?"
            lines.append(f"💍 <@{u1}> & <@{u2}> — `{d_str}`")

        embed = discord.Embed(
            title="server marriages",
            description="\n".join(lines),
            color=get_embed_color(ctx.guild.id),
        )
        await ctx.send(embed=embed)

    # ── 8ball ───────────────────────────────────────────────────────
    @commands.command(name="8ball", aliases=["eightball"])
    @commands.cooldown(2, 5, commands.BucketType.user)
    @help_meta(
        usage="`.8ball <question>`",
        desc="Consult the magic 8-ball for an answer.",
        section="Fun",
        perm_tier="public",
        examples=[".8ball will today be good", ".8ball should i sleep"],
        params=[{"name": "question", "type": "str", "required": True, "desc": "Question to ask the 8ball."}],
    )
    async def eightball(self, ctx: commands.Context, *, question: str = None):
        if not question:
            return await ctx.send("-# ask a question")
        ans = random.choice(EIGHTBALL_RESPONSES)
        await ctx.send(f"-# {ans}")

    # ── Choose ──────────────────────────────────────────────────────
    @commands.command(name="choose", aliases=["pick"])
    @commands.cooldown(2, 5, commands.BucketType.user)
    @help_meta(
        usage="`.choose <option1>, <option2>, ...`",
        desc="Randomly chooses between comma-separated options.",
        section="Fun",
        perm_tier="public",
        examples=[".choose coffee, tea, boba", ".choose play valorant, watch anime"],
        params=[{"name": "options", "type": "str", "required": True, "desc": "Comma-separated list of choices."}],
    )
    async def choose(self, ctx: commands.Context, *, choices_str: str = None):
        if not choices_str:
            return await ctx.send("-# usage: `.choose <opt1>, <opt2>, ...`")
        if "," in choices_str:
            opts = [c.strip() for c in choices_str.split(",") if c.strip()]
        else:
            opts = [c.strip() for c in choices_str.split(" or ") if c.strip()]
        if len(opts) < 2:
            return await ctx.send("-# give me at least 2 choices separated by commas")
        pick = random.choice(opts)
        await ctx.send(f"-# i choose **{pick}**")

    # ── Rock Paper Scissors ─────────────────────────────────────────
    @commands.command(name="rps")
    @commands.cooldown(1, 5, commands.BucketType.user)
    @help_meta(
        usage="`.rps [@user]`",
        desc="Play an interactive game of Rock-Paper-Scissors against the bot or another member.",
        section="Fun",
        perm_tier="public",
        examples=[".rps", ".rps @someone"],
        params=[{"name": "user", "type": "user", "required": False, "desc": "Opponent to play against. Omit to play vs bot."}],
    )
    async def rps(self, ctx: commands.Context, opponent: discord.Member = None):
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if opponent and opponent.id == ctx.author.id:
            return await ctx.send("-# you can't play against yourself")
        if opponent and opponent.bot:
            return await ctx.send("-# play against me by omitting the mention: `.rps`")

        view = RPSView(ctx.author, opponent)
        if opponent:
            msg = await ctx.send(
                f"{ctx.author.mention} challenged {opponent.mention} to rock-paper-scissors — make your picks",
                view=view,
            )
        else:
            msg = await ctx.send("choose your move:", view=view)
        view.message = msg

    # ── Trivia ──────────────────────────────────────────────────────
    @commands.command(name="trivia")
    @commands.cooldown(1, 10, commands.BucketType.user)
    @help_meta(
        usage="`.trivia`",
        desc="Generates a multiple choice trivia challenge.",
        section="Fun",
        perm_tier="public",
        examples=[".trivia"],
        params=[],
    )
    async def trivia(self, ctx: commands.Context):
        url = "https://opentdb.com/api.php?amount=1&type=multiple"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status != 200:
                        return await ctx.send("-# trivia api unavailable")
                    data = await r.json()
                    results = data.get("results", [])
                    if not results:
                        return await ctx.send("-# could not load question")
                    q_data = results[0]
        except Exception:
            return await ctx.send("-# trivia api unavailable")

        question = html.unescape(q_data.get("question", ""))
        correct = html.unescape(q_data.get("correct_answer", ""))
        incorrects = [html.unescape(a) for a in q_data.get("incorrect_answers", [])]
        category = html.unescape(q_data.get("category", "General"))
        difficulty = q_data.get("difficulty", "medium")

        options = [correct] + incorrects
        random.shuffle(options)

        view = TriviaView(ctx.author, correct, options)
        embed = discord.Embed(
            title=f"trivia ({difficulty})",
            description=f"**{question}**",
            color=get_embed_color(ctx.guild.id if ctx.guild else 0),
        )
        embed.set_footer(text=f"category: {category}")
        await ctx.send(embed=embed, view=view)

    # ── Would You Rather ────────────────────────────────────────────
    @commands.command(name="wyr", aliases=["wouldyourather"])
    @commands.cooldown(1, 8, commands.BucketType.channel)
    @help_meta(
        usage="`.wyr`",
        desc="Posts a Would You Rather dilemma with live community voting buttons.",
        section="Fun",
        perm_tier="public",
        examples=[".wyr"],
        params=[],
    )
    async def wyr(self, ctx: commands.Context):
        opt1, opt2 = random.choice(WYR_QUESTIONS)
        view = WYRView(opt1, opt2)
        await ctx.send(content=view._render_content(), view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Social(bot))
