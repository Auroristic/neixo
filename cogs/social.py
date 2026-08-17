import asyncio
import html
import io
import logging
import os
import random
import re
from datetime import datetime, timezone

import aiohttp
import aiosqlite
import discord
from discord.ext import commands
from PIL import Image, ImageDraw

from utils import CONFIG_FILE, DATA_DIR, get_embed_color, help_meta, is_owner_or_creator, load_json

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


# ── Marriage Card Renderers & Time Helpers ─────────────────────────
def _format_married_duration(married_dt: datetime) -> tuple[str, str, int]:
    now = datetime.now(timezone.utc)
    delta = now - married_dt
    total_seconds = max(0, int(delta.total_seconds()))
    days = delta.days
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    if days > 0:
        long_str = f"{days} Day{'s' if days != 1 else ''}, {hours} Hour{'s' if hours != 1 else ''}"
        short_str = f"{days}d {hours}h {minutes}m"
    elif hours > 0:
        long_str = f"{hours} Hour{'s' if hours != 1 else ''}, {minutes} Minute{'s' if minutes != 1 else ''}"
        short_str = f"{hours}h {minutes}m {seconds}s"
    elif minutes > 0:
        long_str = f"{minutes} Minute{'s' if minutes != 1 else ''}, {seconds} Second{'s' if seconds != 1 else ''}"
        short_str = f"{minutes}m {seconds}s"
    else:
        long_str = f"{seconds} Second{'s' if seconds != 1 else ''}"
        short_str = f"{seconds}s"

    return long_str, short_str, days


def _render_marriage_card(
    av1_bytes: bytes | None,
    av2_bytes: bytes | None,
    name1: str,
    name2: str,
    tag1: str,
    tag2: str,
    duration_str: str,
    date_str: str,
    sent_proposals: int = 0,
    recv_proposals: int = 0,
) -> io.BytesIO:
    from cogs.serverstats import _load_font, _circle_avatar, _make_glass_backdrop

    W, H = 860, 360
    source_bytes = av1_bytes or av2_bytes
    bg = _make_glass_backdrop(source_bytes, W, H, dark_tint=0.60, blur_radius=22)

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    pad_x, pad_y = 28, 22
    cd.rounded_rectangle(
        [pad_x, pad_y, W - pad_x, H - pad_y],
        radius=26,
        fill=(0, 0, 0, 115),
        outline=(255, 255, 255, 45),
        width=1,
    )
    cd.line([(pad_x + 25, pad_y + 1), (W - pad_x - 25, pad_y + 1)], fill=(255, 255, 255, 90), width=1)
    bg = Image.alpha_composite(bg, card)
    draw = ImageDraw.Draw(bg)

    f_title = _load_font(13, bold=True)
    f_huge = _load_font(28, bold=True)
    f_sub = _load_font(16, bold=False)
    f_name = _load_font(20, bold=True)
    f_tag = _load_font(14, bold=False)
    f_badge = _load_font(14, bold=True)

    # Top Header Badge
    header_text = "M A R R I A G E   C E R T I F I C A T E"
    hw = f_title.getlength(header_text)
    pill_w1 = hw + 32
    pill_h1 = 28
    pill_x1 = (W - pill_w1) // 2
    pill_y1 = pad_y + 12
    draw.rounded_rectangle(
        [pill_x1, pill_y1, pill_x1 + pill_w1, pill_y1 + pill_h1],
        radius=14,
        fill=(240, 245, 255, 245),
        outline=(255, 255, 255, 100),
        width=1,
    )
    f_title.draw(draw, ((W - hw) // 2, pill_y1 + 7), header_text, fill=(12, 14, 18, 255))

    av_size = 115
    # Left avatar
    av1_x, av1_y = pad_x + 45, pad_y + 48
    if av1_bytes:
        try:
            av1_img = _circle_avatar(av1_bytes, av_size)
            bg.paste(av1_img, (av1_x, av1_y), av1_img)
            draw.ellipse([av1_x, av1_y, av1_x + av_size, av1_y + av_size], outline=(255, 255, 255, 80), width=2)
            draw.ellipse([av1_x - 4, av1_y - 4, av1_x + av_size + 4, av1_y + av_size + 4], outline=(255, 255, 255, 25), width=1)
        except Exception:
            pass

    # Right avatar
    av2_x, av2_y = W - pad_x - 45 - av_size, pad_y + 48
    if av2_bytes:
        try:
            av2_img = _circle_avatar(av2_bytes, av_size)
            bg.paste(av2_img, (av2_x, av2_y), av2_img)
            draw.ellipse([av2_x, av2_y, av2_x + av_size, av2_y + av_size], outline=(255, 255, 255, 80), width=2)
            draw.ellipse([av2_x - 4, av2_y - 4, av2_x + av_size + 4, av2_y + av_size + 4], outline=(255, 255, 255, 25), width=1)
        except Exception:
            pass

    # User 1 name & tag
    w1 = f_name.getlength(name1[:14])
    f_name.draw(draw, (av1_x + (av_size - w1) // 2, av1_y + av_size + 14), name1[:14], fill=(255, 255, 255, 240))
    t1_w = f_tag.getlength(tag1[:16])
    f_tag.draw(draw, (av1_x + (av_size - t1_w) // 2, av1_y + av_size + 38), tag1[:16], fill=(170, 175, 190, 200))

    # User 2 name & tag
    w2 = f_name.getlength(name2[:14])
    f_name.draw(draw, (av2_x + (av_size - w2) // 2, av2_y + av_size + 14), name2[:14], fill=(255, 255, 255, 240))
    t2_w = f_tag.getlength(tag2[:16])
    f_tag.draw(draw, (av2_x + (av_size - t2_w) // 2, av2_y + av_size + 38), tag2[:16], fill=(170, 175, 190, 200))

    # Center connector line with rings
    cx = W // 2
    cy = pad_y + 85

    draw.line([(av1_x + av_size + 20, cy), (cx - 50, cy)], fill=(255, 255, 255, 40), width=1)
    draw.line([(cx + 50, cy), (av2_x - 20, cy)], fill=(255, 255, 255, 40), width=1)

    ring_sym = "💍"
    f_ring = _load_font(28, bold=False)
    rw = f_ring.getlength(ring_sym)
    f_ring.draw(draw, (cx - rw // 2, cy - 18), ring_sym, fill=(255, 255, 255, 250))

    # Duration text
    dw = f_huge.getlength(duration_str)
    f_huge.draw(draw, ((W - dw) // 2, cy + 26), duration_str, fill=(255, 255, 255, 255))

    # Married since date
    date_text = f"Married on {date_str}" if not date_str.startswith("Married") else date_str
    date_w = f_sub.getlength(date_text)
    f_sub.draw(draw, ((W - date_w) // 2, cy + 64), date_text, fill=(185, 190, 205, 210))

    # Bottom proposal stats pill
    stats_text = f"Proposals: {sent_proposals} sent  •  {recv_proposals} received"
    sw = f_badge.getlength(stats_text)
    pill_w = sw + 32
    pill_h = 32
    pill_x = (W - pill_w) // 2
    pill_y = H - pad_y - 48
    draw.rounded_rectangle(
        [pill_x, pill_y, pill_x + pill_w, pill_y + pill_h],
        radius=16,
        fill=(240, 245, 255, 245),
        outline=(255, 255, 255, 100),
        width=1,
    )
    f_badge.draw(draw, (pill_x + 16, pill_y + 8), stats_text, fill=(12, 14, 18, 255))

    out = io.BytesIO()
    bg.convert("RGB").save(out, format="PNG")
    out.seek(0)
    return out


def _render_single_card(
    av_bytes: bytes | None,
    name: str,
    tag: str,
    sent_proposals: int = 0,
    recv_proposals: int = 0,
) -> io.BytesIO:
    from cogs.serverstats import _load_font, _circle_avatar, _make_glass_backdrop

    W, H = 680, 270
    bg = _make_glass_backdrop(av_bytes, W, H, dark_tint=0.60, blur_radius=22)

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    pad_x, pad_y = 26, 20
    cd.rounded_rectangle(
        [pad_x, pad_y, W - pad_x, H - pad_y],
        radius=24,
        fill=(0, 0, 0, 115),
        outline=(255, 255, 255, 45),
        width=1,
    )
    cd.line([(pad_x + 20, pad_y + 1), (W - pad_x - 20, pad_y + 1)], fill=(255, 255, 255, 90), width=1)
    bg = Image.alpha_composite(bg, card)
    draw = ImageDraw.Draw(bg)

    f_title = _load_font(12, bold=True)
    f_huge = _load_font(24, bold=True)
    f_sub = _load_font(15, bold=False)
    f_name = _load_font(20, bold=True)
    f_tag = _load_font(14, bold=False)
    f_badge = _load_font(13, bold=True)

    header_text = "M A R R I A G E   P R O F I L E"
    hw = f_title.getlength(header_text)
    pill_w1 = hw + 28
    pill_h1 = 26
    pill_x1 = (W - pill_w1) // 2
    pill_y1 = pad_y + 12
    draw.rounded_rectangle(
        [pill_x1, pill_y1, pill_x1 + pill_w1, pill_y1 + pill_h1],
        radius=13,
        fill=(240, 245, 255, 245),
        outline=(255, 255, 255, 100),
        width=1,
    )
    f_title.draw(draw, ((W - hw) // 2, pill_y1 + 6), header_text, fill=(12, 14, 18, 255))

    av_size = 100
    av_x, av_y = pad_x + 40, pad_y + 48
    if av_bytes:
        try:
            av_img = _circle_avatar(av_bytes, av_size)
            bg.paste(av_img, (av_x, av_y), av_img)
            draw.ellipse([av_x, av_y, av_x + av_size, av_y + av_size], outline=(255, 255, 255, 75), width=2)
            draw.ellipse([av_x - 3, av_y - 3, av_x + av_size + 3, av_y + av_size + 3], outline=(255, 255, 255, 20), width=1)
        except Exception:
            pass

    info_x = av_x + av_size + 35
    w_n = f_name.getlength(name[:16])
    f_name.draw(draw, (info_x, pad_y + 54), name[:16], fill=(255, 255, 255, 245))
    f_tag.draw(draw, (info_x + w_n + 10, pad_y + 59), tag[:16], fill=(160, 165, 180, 190))

    status_text = "Single  •  Not Married"
    f_huge.draw(draw, (info_x, pad_y + 84), status_text, fill=(245, 245, 255, 250))

    f_sub.draw(draw, (info_x, pad_y + 116), "Use `.marry @someone` to propose!", fill=(175, 180, 195, 200))

    # Stats pill
    stats_text = f"Proposals: {sent_proposals} sent  •  {recv_proposals} received"
    sw = f_badge.getlength(stats_text)
    pill_w = sw + 28
    pill_h = 30
    pill_y = H - pad_y - 42
    draw.rounded_rectangle(
        [info_x, pill_y, info_x + pill_w, pill_y + pill_h],
        radius=15,
        fill=(240, 245, 255, 245),
        outline=(255, 255, 255, 100),
        width=1,
    )
    f_badge.draw(draw, (info_x + 14, pill_y + 7), stats_text, fill=(12, 14, 18, 255))

    out = io.BytesIO()
    bg.convert("RGB").save(out, format="PNG")
    out.seek(0)
    return out


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
        await self.cog.record_proposal_result(self.author.id, self.target.id, accepted=True)
        
        await interaction.response.defer()
        try:
            av1_bytes = await self.cog._fetch_avatar(self.author)
            av2_bytes = await self.cog._fetch_avatar(self.target)
            now_dt = datetime.now(timezone.utc)
            date_str = now_dt.strftime("%b %d, %Y")
            
            stats = await self.cog.get_proposal_stats(self.author.id)
            sent, recv, _, _ = stats
            
            card_buf = await asyncio.to_thread(
                _render_marriage_card,
                av1_bytes,
                av2_bytes,
                self.author.display_name,
                self.target.display_name,
                f"@{self.author.name}",
                f"@{self.target.name}",
                "Just Married 💍",
                date_str,
                sent,
                recv,
            )
            file = discord.File(card_buf, filename="wedding.png")
            await interaction.message.edit(content=None, attachments=[file], view=self)
        except Exception as e:
            log.warning(f"error rendering wedding card: {e}")
            await interaction.message.edit(
                content=f"-# {self.author.mention} and {self.target.mention} are now married 💍",
                view=self,
            )
        self.stop()

    @discord.ui.button(label="decline", style=discord.ButtonStyle.secondary, emoji="💔")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        self.value = False
        await self.cog.record_proposal_result(self.author.id, self.target.id, accepted=False)
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
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS proposal_stats (
                user_id INTEGER PRIMARY KEY,
                sent_count INTEGER NOT NULL DEFAULT 0,
                received_count INTEGER NOT NULL DEFAULT 0,
                accepted_count INTEGER NOT NULL DEFAULT 0,
                declined_count INTEGER NOT NULL DEFAULT 0
            )
        """)
        await self.db.commit()

    async def cog_unload(self):
        if self.db:
            await self.db.close()
            self.db = None

    async def _fetch_avatar(self, user: discord.abc.User | discord.Member | None) -> bytes | None:
        if user is None:
            return None
        try:
            url = str(user.display_avatar.with_format("png").with_size(256).url)
            timeout = aiohttp.ClientTimeout(total=6)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as r:
                    if r.status == 200:
                        return await r.read()
        except Exception as e:
            log.warning(f"failed to fetch avatar for {getattr(user, 'id', None)}: {e}")
        return None

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

    async def get_proposal_stats(self, user_id: int) -> tuple[int, int, int, int]:
        """Returns (sent_count, received_count, accepted_count, declined_count)."""
        if not self.db:
            return 0, 0, 0, 0
        async with self.db.execute(
            "SELECT sent_count, received_count, accepted_count, declined_count FROM proposal_stats WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return row[0], row[1], row[2], row[3]
        return 0, 0, 0, 0

    async def record_proposal(self, sender_id: int, target_id: int):
        if not self.db:
            return
        await self.db.execute("""
            INSERT INTO proposal_stats (user_id, sent_count, received_count, accepted_count, declined_count)
            VALUES (?, 1, 0, 0, 0)
            ON CONFLICT(user_id) DO UPDATE SET sent_count = sent_count + 1
        """, (sender_id,))
        await self.db.execute("""
            INSERT INTO proposal_stats (user_id, sent_count, received_count, accepted_count, declined_count)
            VALUES (?, 0, 1, 0, 0)
            ON CONFLICT(user_id) DO UPDATE SET received_count = received_count + 1
        """, (target_id,))
        await self.db.commit()

    async def record_proposal_result(self, sender_id: int, target_id: int, accepted: bool):
        if not self.db:
            return
        col = "accepted_count" if accepted else "declined_count"
        for uid in (sender_id, target_id):
            await self.db.execute(f"""
                INSERT INTO proposal_stats (user_id, sent_count, received_count, accepted_count, declined_count)
                VALUES (?, 0, 0, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET {col} = {col} + 1
            """, (uid, 1 if accepted else 0, 1 if not accepted else 0))
        await self.db.commit()

    async def set_proposal_stats(self, user_id: int, sent: int, received: int):
        if not self.db:
            return
        await self.db.execute("""
            INSERT INTO proposal_stats (user_id, sent_count, received_count, accepted_count, declined_count)
            VALUES (?, ?, ?, 0, 0)
            ON CONFLICT(user_id) DO UPDATE SET sent_count = ?, received_count = ?
        """, (user_id, sent, received, sent, received))
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

        await self.record_proposal(ctx.author.id, user.id)

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
        desc="Displays marriage status, partner, anniversary duration, proposal statistics, and custom glass card.",
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
        stats = await self.get_proposal_stats(target.id)
        sent, recv, accepted, declined = stats

        if not m:
            av_bytes = await self._fetch_avatar(target)
            card_buf = await asyncio.to_thread(
                _render_single_card,
                av_bytes,
                target.display_name,
                f"@{target.name}",
                sent,
                recv,
            )
            file = discord.File(card_buf, filename="marriage.png")
            return await ctx.send(file=file)

        partner_id, iso_str, _ = m
        try:
            married_dt = datetime.fromisoformat(iso_str)
            long_dur, short_dur, days = _format_married_duration(married_dt)
            married_ts = int(married_dt.timestamp())
            date_str = married_dt.strftime("%b %d, %Y")
        except Exception:
            long_dur = "Some Time"
            short_dur = "some time"
            married_ts = int(datetime.now(timezone.utc).timestamp())
            date_str = "Recently"

        partner = ctx.guild.get_member(partner_id) or self.bot.get_user(partner_id)
        if partner is None:
            try:
                partner = await self.bot.fetch_user(partner_id)
            except Exception:
                pass

        partner_name = partner.display_name if partner else f"User {partner_id}"
        partner_tag = f"@{partner.name}" if partner else f"@{partner_id}"

        av1_bytes = await self._fetch_avatar(target)
        av2_bytes = await self._fetch_avatar(partner) if partner else None

        card_buf = await asyncio.to_thread(
            _render_marriage_card,
            av1_bytes,
            av2_bytes,
            target.display_name,
            partner_name,
            f"@{target.name}",
            partner_tag,
            long_dur,
            date_str,
            sent,
            recv,
        )

        file = discord.File(card_buf, filename="marriage.png")
        await ctx.send(file=file)

    @commands.command(name="marrysetstats", aliases=["setproposals"])
    @help_meta(
        usage="`.marrysetstats <@user> <sent> <received>`",
        desc="Sets the proposal statistics for a user.",
        section="Fun",
        perm_tier="whitelist",
        examples=[".marrysetstats @someone 5 12"],
        params=[
            {"name": "user", "type": "user", "required": True, "desc": "Member to update."},
            {"name": "sent", "type": "int", "required": True, "desc": "Proposals sent count."},
            {"name": "received", "type": "int", "required": True, "desc": "Proposals received count."},
        ],
        note="Server Owner / Whitelisted only.",
    )
    async def marrysetstats(self, ctx: commands.Context, user: discord.Member = None, sent: int = 0, received: int = 0):
        if not is_owner_or_creator(ctx):
            config = load_json(CONFIG_FILE)
            guild_config = config.get(str(ctx.guild.id), {}) if ctx.guild else {}
            whitelist = guild_config.get("whitelist", [])
            if str(ctx.author.id) not in {str(uid) for uid in whitelist}:
                return await ctx.send("no perms")

        if user is None:
            return await ctx.send("usage: `.marrysetstats <@user> <sent> <received>`")

        await self.set_proposal_stats(user.id, max(0, sent), max(0, received))
        try:
            await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        except Exception:
            await ctx.send(f"-# updated proposal stats for {user.display_name}: `{sent}` sent • `{received}` received")

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
