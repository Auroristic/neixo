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

# user_id -> {"all": bool, "channels": set[int]}
_lock_scopes: dict[int, dict] = {}

# (user_id, channel_id) -> Webhook  (lazy cache; rebuilt on demand)
_webhooks: dict[tuple[int, int], discord.Webhook] = {}


def _is_locked(user_id: int, channel_id: int) -> bool:
    s = _lock_scopes.get(user_id)
    if not s:
        return False
    return s.get("all", False) or channel_id in s.get("channels", set())


def _save_state() -> None:
    """Persist current lock scopes + cached webhook URLs."""
    data = {}
    for uid, scope in _lock_scopes.items():
        whs = {
            str(cid): wh.url
            for (u, cid), wh in _webhooks.items()
            if u == uid
        }
        data[str(uid)] = {
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
    for uid_str, info in raw.items():
        try:
            uid = int(uid_str)
        except (TypeError, ValueError):
            continue
        _lock_scopes[uid] = {
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
    @commands.command(name="uwulock")
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

        scope = _lock_scopes.setdefault(
            member.id, {"all": False, "channels": set()}
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
            _lock_scopes.pop(member.id, None)

        await self._gc_webhooks_for(member.id)
        _save_state()

        try:
            await ctx.message.add_reaction(reaction)
        except Exception:
            pass

    @help_meta(
        usage="`.uwulist`",
        desc="Shows who is currently uwulocked.",
        examples=[".uwulist"],
        params=[],
        note="Staff only.",
        section="Moderation",
        staff=True,
    )
    @commands.command(name="uwulist")
    async def uwulist(self, ctx):
        if not ctx.guild:
            return
        if not is_owner_or_creator(ctx) and not ctx.author.guild_permissions.manage_messages:
            return await ctx.send("no perms")

        if not _lock_scopes:
            embed = discord.Embed(
                description="no one is uwulocked rn",
                color=get_embed_color(ctx.guild.id),
            )
            return await ctx.send(embed=embed)

        lines = []
        for uid, scope in _lock_scopes.items():
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
        if not _is_locked(message.author.id, message.channel.id):
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

            uwufied = _uwufy(message.content) if message.content else ""

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

async def setup(bot: commands.Bot):
    await bot.add_cog(FunCog(bot))
