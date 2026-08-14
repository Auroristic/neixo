import logging
import random
import re as _re

import aiohttp as _aiohttp
import discord
from discord.ext import commands

from utils import (
    CONFIG_FILE,
    DATA_DIR,
    get_embed_color,
    help_meta,
    is_owner_or_creator,
    load_json,
    save_json,
)

log = logging.getLogger(__name__)

# ── cogs/fun.py ─────────────────────────────────────────────────
COG_META = {
    "category": "fun",
    "label": "Fun",
    "desc": "Fun staff tools.",
    "staff": True,
}



# ── uwulock state (persisted) ───────────────────────────────────
UWULOCKED_FILE = f"{DATA_DIR}/uwulocked.json"

# (guild_id, user_id) -> {"all": bool, "channels": set[int]}
_lock_scopes: dict[tuple[int, int], dict] = {}

# (user_id, channel_id) -> Webhook  (lazy cache; rebuilt on demand)
_webhooks: dict[tuple[int, int], discord.Webhook] = {}


def _is_locked(guild_id: int, user_id: int, channel_id: int) -> bool:
    s = _lock_scopes.get((guild_id, user_id))
    if not s:
        return False
    return s.get("all", False) or channel_id in s.get("channels", set())


def _save_state() -> None:
    """Persist current lock scopes + cached webhook URLs."""
    data = {}
    for (gid, uid), scope in _lock_scopes.items():
        whs = {
            str(cid): wh.url
            for (u, cid), wh in _webhooks.items()
            if u == uid
        }
        data[f"{gid}_{uid}"] = {
            "all": bool(scope.get("all", False)),
            "channels": [str(c) for c in scope.get("channels", set())],
            "webhooks": whs,
        }
    save_json(UWULOCKED_FILE, data)


def _load_state(bot: commands.Bot) -> None:
    """Rehydrate locks + webhooks from disk on cog load."""
    _lock_scopes.clear()
    _webhooks.clear()
    raw = load_json(UWULOCKED_FILE) or {}
    for key_str, info in raw.items():
        if "_" in key_str:
            parts = key_str.split("_", 1)
            try:
                gid = int(parts[0])
                uid = int(parts[1])
            except ValueError:
                continue
        else:
            try:
                gid = 0
                uid = int(key_str)
            except (TypeError, ValueError):
                continue
        _lock_scopes[(gid, uid)] = {
            "all": bool(info.get("all", False)),
            "channels": {
                int(c) for c in info.get("channels", []) if str(c).isdigit()
            },
        }
        for cid_str, url in info.get("webhooks", {}).items():
            try:
                _webhooks[(uid, int(cid_str))] = discord.Webhook.from_url(
                    url, client=bot
                )
            except Exception as e:
                log.warning(f"failed to rehydrate webhook {uid}/{cid_str}: {e}")

# ── uwu transform (everything precompiled once → fast) ──────────
_WORD_MAP = {
    "the": "teh", "no": "nwo", "not": "nwot", "you": "chu", "your": "ur",
    "please": "pwease", "sorry": "sowwy", "stop": "stwop", "cute": "kawaii",
    "love": "wuv", "like": "wike", "really": "weawwy", "what": "nani",
    "why": "nyaa", "hello": "hewwo", "hi": "hai", "hey": "heyy",
    "bye": "bai bai", "yes": "yesh", "okay": "okie", "ok": "okie",
    "help": "hewp", "think": "fink", "this": "dis", "that": "dat",
    "cry": "cwy", "feel": "feew", "good": "gud", "bad": "baddo",
    "sad": "saddo", "friend": "fwend", "friends": "fwends", "small": "smol",
    "because": "cuz", "dog": "doggo", "cat": "catto", "kiss": "kissu",
    "morning": "mownin", "night": "nini",
}
_WORD_RE    = _re.compile(r"\b(" + "|".join(_re.escape(k) for k in _WORD_MAP) + r")\b", _re.I)
_NYA_RE     = _re.compile(r"([Nn])([aeiouAEIOU])")
_LR_RE      = _re.compile(r"[rlRL]")
_STUTTER_RE = _re.compile(r"\b([a-zA-Z])([a-zA-Z]{2,})\b")
_BANG_RE    = _re.compile(r"!+")
_Q_RE       = _re.compile(r"\?+")
_PROTECT_RE = _re.compile(r"<a?:\w+:\d+>|<@[!&]?\d+>|<#\d+>|https?://\S+")
_TOKEN_RE   = _re.compile(r"\x00(\d+)\x00")

_BANGS  = ["!!", " >w<!!", "!!~", " >////<!", " uwu!!"]
_QS     = [" owo?", " uwu?", " nya~?", " >w<?"]
_FLAVOR = [
    "uwu", "owo", ">w<", "^w^", ":3", "x3", "nya~", ">////<", "(◕ω◕✿)",
    "*blushes*", "*hides behind paws*", "*twirls hair*", "teehee~",
    "*fidgets nervously*", "*soft squeak*", "*ears perk up*", "*wags tail*",
    "*tail wagging intensifies*", "rawr~", "*purrs*", "*nuzzles u*", "awoo~",
    "*boops ur snoot*", "mrrp~", "*paws at u*", "*pounces*", "*flicks tail*",
    "ROAAR >:3", "*goes feral*", "im so feral rn", "HELP i cant-",
    "*screams into pillow*", "*vibrates*", "im NOT okay teehee~",
    "*sparkles*", "*bounces excitedly*", "brain go brrr", "*chomp*",
]

def _word_sub(m):
    rep = _WORD_MAP[m.group(0).lower()]
    return rep[:1].upper() + rep[1:] if m.group(0)[:1].isupper() else rep

def _nya_sub(m):
    return ("Ny" if m.group(1) == "N" else "ny") + m.group(2)

def _stutter_sub(m):
    if random.random() < 0.18:
        return f"{m.group(1)}-{m.group(1)}{m.group(2)}"
    return m.group(0)

def _uwufy(text: str) -> str:
    if not text:
        return text
    # stash mentions / emojis / channels / links so they survive untouched
    stash = []
    def _hold(m):
        stash.append(m.group(0))
        return f"\x00{len(stash) - 1}\x00"
    text = _PROTECT_RE.sub(_hold, text)

    text = _WORD_RE.sub(_word_sub, text)
    text = _NYA_RE.sub(_nya_sub, text)
    text = _LR_RE.sub(lambda m: "W" if m.group(0).isupper() else "w", text)
    text = _STUTTER_RE.sub(_stutter_sub, text)
    text = _BANG_RE.sub(lambda m: random.choice(_BANGS), text)
    text = _Q_RE.sub(lambda m: random.choice(_QS), text)

    # sprinkle 1–3 cutesy / furry / cringe bits, scaled to message length
    k = min(len(_FLAVOR), 1 + (len(text.split()) > 3) + (random.random() < 0.4))
    text = (text.rstrip() + " " + " ".join(random.sample(_FLAVOR, k))).strip()

    return _TOKEN_RE.sub(lambda m: stash[int(m.group(1))], text)

async def _get_or_make_webhook(
    channel: discord.TextChannel, member: discord.Member,
    session: _aiohttp.ClientSession | None = None,
) -> discord.Webhook:
    """Return a webhook for (member, channel), creating one if needed.
    Each (user, channel) pair gets its own webhook so per-channel locks coexist."""
    key = (member.id, channel.id)
    wh = _webhooks.get(key)
    if wh:
        try:
            await wh.fetch()
            return wh
        except discord.NotFound:
            _webhooks.pop(key, None)
        except Exception as e:
            log.warning(f"webhook fetch error: {e}")
            _webhooks.pop(key, None)

    avatar_bytes = None
    try:
        own_session = session is None
        if own_session:
            session = _aiohttp.ClientSession()
        try:
            async with session.get(str(member.display_avatar.url)) as r:
                if r.status == 200:
                    avatar_bytes = await r.read()
        finally:
            if own_session:
                await session.close()
    except Exception as e:
        log.warning(f"avatar download error: {e}")

    new_wh = await channel.create_webhook(
        name=member.display_name, avatar=avatar_bytes
    )
    _webhooks[key] = new_wh
    _save_state()  # persist the new webhook url
    return new_wh


class FunCog(commands.Cog, name="Fun"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: _aiohttp.ClientSession | None = None

    async def cog_load(self):
        self.session = _aiohttp.ClientSession()
        _load_state(self.bot)

    async def cog_unload(self):
        for wh in _webhooks.values():
            try:
                await wh.delete()
            except Exception:
                pass
        _lock_scopes.clear()
        _webhooks.clear()
        if self.session:
            await self.session.close()

    async def _gc_webhooks_for(self, user_id: int) -> None:
        """Delete cached webhooks the user no longer needs after a scope change."""
        scope = _lock_scopes.get(user_id)
        keep_all = bool(scope and scope.get("all"))
        keep_channels = scope.get("channels", set()) if scope else set()

        keys = [k for k in list(_webhooks) if k[0] == user_id]
        for k in keys:
            if keep_all or k[1] in keep_channels:
                continue
            wh = _webhooks.pop(k, None)
            if wh:
                try:
                    await wh.delete()
                except Exception as e:
                    log.warning(f"webhook gc delete: {e}")

    @commands.command(name="uwulock")
    @help_meta(
        usage="`.uwulock @user [#channel]`",
        desc="Toggles uwulock on a user — all their messages become uwufied.",
        examples=[".uwulock @user", ".uwulock @user #general"],
        params=[
            {"name": "member", "type": "discord.Member", "required": False, "desc": "The member to uwulock."},
            {"name": "channel", "type": "discord.TextChannel", "required": False, "desc": "Specific channel to restrict it to (optional — omit for all channels)."},
        ],
        note="Staff only. Uses webhooks to rewrite messages. If no channel is given, toggles all-channel lock.",
        section="Moderation",
        staff=True,
    )
    async def uwulock(
        self,
        ctx,
        member: discord.Member = None,
        channel: discord.TextChannel = None,
    ):
        if not ctx.guild:
            return
        config = load_json(CONFIG_FILE)
        guild_config = config.get(str(ctx.guild.id), {})
        whitelist = guild_config.get("whitelist", [])

        if not is_owner_or_creator(ctx) and str(ctx.author.id) not in {str(uid) for uid in whitelist}:
            return await ctx.send("no perms")

        if not member:
            return await ctx.send("who? `.uwulock @user [#channel]`")

        if member.bot:
            return await ctx.send("can't uwulock bots silly!")

        key = (ctx.guild.id, member.id)
        scope = _lock_scopes.setdefault(
            key, {"all": False, "channels": set()}
        )

        if channel is None:
            # toggle the all-channels lock
            if scope["all"]:
                scope["all"] = False
                reaction = "<:redlotus:1263556248310386800>"
            else:
                scope["all"] = True
                reaction = "<:pinklotus:1263556545686405170>"
        else:
            # toggle a specific-channel lock
            if channel.id in scope["channels"]:
                scope["channels"].discard(channel.id)
                reaction = "<:redlotus:1263556248310386800>"
            else:
                scope["channels"].add(channel.id)
                reaction = "<:pinklotus:1263556545686405170>"

        # drop the user entirely if nothing is locked anymore
        if not scope["all"] and not scope["channels"]:
            _lock_scopes.pop(key, None)

        await self._gc_webhooks_for(member.id)
        _save_state()

        try:
            await ctx.message.add_reaction(reaction)
        except Exception:
            pass

    @commands.command(name="uwulist")
    @help_meta(
        usage="`.uwulist`",
        desc="Shows who is currently uwulocked.",
        examples=[".uwulist"],
        params=[],
        note="Staff only.",
        section="Moderation",
        staff=True,
    )
    async def uwulist(self, ctx):
        if not ctx.guild:
            return
        if not is_owner_or_creator(ctx) and not ctx.author.guild_permissions.manage_messages:
            return await ctx.send("no perms")

        guild_locks = {
            uid: scope for (gid, uid), scope in _lock_scopes.items() if gid == ctx.guild.id
        }

        if not guild_locks:
            embed = discord.Embed(
                description="no one is uwulocked rn",
                color=get_embed_color(ctx.guild.id),
            )
            return await ctx.send(embed=embed)

        lines = []
        for uid, scope in guild_locks.items():
            if scope.get("all"):
                scope_str = "all channels"
            else:
                chans = scope.get("channels", set())
                scope_str = ", ".join(f"<#{c}>" for c in chans) or "(none)"
            lines.append(f"• <@{uid}> — {scope_str}")
        embed = discord.Embed(
            title="uwulocked members",
            description="\n".join(lines),
            color=get_embed_color(ctx.guild.id),
        )
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return
        if not _is_locked(message.guild.id, message.author.id, message.channel.id):
            return
        if message.content.startswith("."):
            return

        has_links = any(
            x in message.content.lower()
            for x in ("http://", "https://", "discord.gg/", "tenor", "giphy")
        )
        has_gifs = any(
            att.filename.lower().endswith((".gif", ".webp"))
            for att in message.attachments
        )
        has_stickers = bool(message.stickers)

        if has_links or has_gifs or has_stickers:
            try:
                await message.delete()
            except Exception as e:
                log.warning(f"uwulock delete blocked content: {e}")
            return

        try:
            try:
                wh = await _get_or_make_webhook(message.channel, message.author, self.session)
            except Exception as e:
                log.warning(f"uwulock webhook issue: {e}")
                return

            uwufied = _uwufy(message.content)[:2000] if message.content else ""

            files = []
            for att in message.attachments:
                try:
                    files.append(await att.to_file())
                except Exception as e:
                    log.warning(f"uwulock attachment download: {e}")

            if not uwufied and not files:
                return

            # send the webhook copy first, then delete the original —
            # deleting first loses the user's message if the send fails
            await wh.send(
                uwufied or "...",
                username=message.author.display_name,
                avatar_url=str(message.author.display_avatar.url),
                files=files if files else discord.utils.MISSING,
            )
            await message.delete()
        except Exception as e:
            log.warning(f"uwulock error: {e}")
            import traceback
            traceback.print_exc()

    @commands.command(name="ship")
    @help_meta(
        usage="`.ship <@user1> [@user2]`",
        desc="calculates compatibility between two members and renders a ship card",
        section="Fun",
        examples=[".ship @someone", ".ship @user1 @user2"],
        params=[
            {
                "name": "user1",
                "type": "discord.User",
                "required": True,
                "desc": "First user to ship.",
            },
            {
                "name": "user2",
                "type": "discord.User",
                "required": False,
                "desc": "Second user to ship. Defaults to you.",
            },
        ],
        note="generates a dark aesthetic compatibility card with percentage meter.",
    )
    async def ship_cmd(self, ctx: commands.Context, user1: discord.User = None, user2: discord.User = None):
        if user1 is None:
            return await ctx.send("-# usage: `.ship @user1 [@user2]`")

        target1 = ctx.author if user2 is None else user1
        target2 = user1 if user2 is None else user2

        if target1.id == target2.id:
            return await ctx.send("-# shipping yourself with yourself? self love is real ig")

        # Deterministic percentage based on user IDs
        min_id, max_id = min(target1.id, target2.id), max(target1.id, target2.id)
        # Hash combining IDs with day of the month for subtle daily drift or pure static
        seed_val = (min_id * 31 + max_id) % 101
        pct = int(seed_val)

        if pct <= 15:
            comment = "zero compatibility... don't even try"
        elif pct <= 35:
            comment = "dry conversation speedrun"
        elif pct <= 55:
            comment = "awkward friendship vibes"
        elif pct <= 75:
            comment = "there's definitely a spark here"
        elif pct <= 90:
            comment = "dangerously compatible"
        else:
            comment = "soulmates fr no cap"

        av1_bytes = None
        av2_bytes = None
        try:
            async with _aiohttp.ClientSession() as s:
                tasks = [
                    s.get(target1.display_avatar.url, timeout=_aiohttp.ClientTimeout(total=8)),
                    s.get(target2.display_avatar.url, timeout=_aiohttp.ClientTimeout(total=8)),
                ]
                resps = await asyncio.gather(*tasks, return_exceptions=True)
                if not isinstance(resps[0], Exception) and resps[0].status == 200:
                    av1_bytes = await resps[0].read()
                if not isinstance(resps[1], Exception) and resps[1].status == 200:
                    av2_bytes = await resps[1].read()
        except Exception:
            pass

        import io
        import asyncio
        buf = await asyncio.to_thread(
            _render_ship_card,
            av1_bytes,
            av2_bytes,
            target1.display_name,
            target2.display_name,
            pct,
            comment,
        )

        await ctx.send(file=discord.File(fp=buf, filename=f"ship_{target1.name}_{target2.name}.png"))


def _render_ship_card(
    av1_bytes: bytes | None,
    av2_bytes: bytes | None,
    name1: str,
    name2: str,
    percentage: int,
    comment: str,
) -> io.BytesIO:
    import io
    from PIL import Image, ImageDraw, ImageFilter
    from cogs.serverstats import _load_font, _circle_avatar

    W, H = 840, 360
    bg = Image.new("RGB", (W, H), (14, 16, 22))

    # Background gradient
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(H):
        alpha = int(70 * (y / H))
        gd.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
    bg = Image.alpha_composite(bg.convert("RGBA"), grad)

    # Glass container
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    pad_x, pad_y = 30, 25
    cd.rounded_rectangle(
        [pad_x, pad_y, W - pad_x, H - pad_y],
        radius=28,
        fill=(255, 255, 255, 10),
        outline=(255, 255, 255, 35),
        width=1,
    )
    bg = Image.alpha_composite(bg, card)
    draw = ImageDraw.Draw(bg)

    f_title = _load_font(36, bold=True)
    f_sub = _load_font(20, bold=False)
    f_name = _load_font(22, bold=True)

    av_size = 120
    # Left avatar
    av1_x, av1_y = pad_x + 50, pad_y + 40
    if av1_bytes:
        try:
            av1 = _circle_avatar(av1_bytes, av_size)
            bg.paste(av1, (av1_x, av1_y), av1)
            draw.ellipse([av1_x, av1_y, av1_x + av_size, av1_y + av_size], outline=(255, 255, 255, 60), width=2)
        except Exception:
            pass

    # Right avatar
    av2_x, av2_y = W - pad_x - 50 - av_size, pad_y + 40
    if av2_bytes:
        try:
            av2 = _circle_avatar(av2_bytes, av_size)
            bg.paste(av2, (av2_x, av2_y), av2)
            draw.ellipse([av2_x, av2_y, av2_x + av_size, av2_y + av_size], outline=(255, 255, 255, 60), width=2)
        except Exception:
            pass

    # Names under avatars
    w1 = f_name.measure(name1[:15])
    f_name.draw(draw, (av1_x + (av_size - w1) // 2, av1_y + av_size + 15), name1[:15], fill=(255, 255, 255, 220))

    w2 = f_name.measure(name2[:15])
    f_name.draw(draw, (av2_x + (av_size - w2) // 2, av2_y + av_size + 15), name2[:15], fill=(255, 255, 255, 220))

    # Center percentage
    pct_text = f"{percentage}%"
    pct_w = f_title.measure(pct_text)
    f_title.draw(draw, ((W - pct_w) // 2, pad_y + 55), pct_text, fill=(255, 120, 160, 255))

    # Progress Bar in Center Bottom
    bar_w, bar_h = 320, 14
    bar_x = (W - bar_w) // 2
    bar_y = pad_y + 115
    draw.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], radius=7, fill=(255, 255, 255, 25))
    fill_w = int(bar_w * (percentage / 100))
    if fill_w > 0:
        draw.rounded_rectangle([bar_x, bar_y, bar_x + fill_w, bar_y + bar_h], radius=7, fill=(255, 105, 155, 230))

    # Comment text at bottom
    com_w = f_sub.measure(comment)
    f_sub.draw(draw, ((W - com_w) // 2, H - pad_y - 45), comment, fill=(255, 255, 255, 160))

    buf = io.BytesIO()
    bg.convert("RGB").save(buf, format="PNG", quality=95)
    buf.seek(0)
    return buf


async def setup(bot: commands.Bot):
    await bot.add_cog(FunCog(bot))
