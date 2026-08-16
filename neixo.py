import asyncio
import logging
import os
import random
import re

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

_required_env = ['DISCORD_TOKEN']
_missing = [k for k in _required_env if not os.getenv(k)]
if _missing:
    raise SystemExit(
        f'Missing required env var(s): {", ".join(_missing)}. '
        'Create a .env file (see .env.example or README.md).'
    )

import wavelink

from utils import (
    load_json, get_config, get_ignore_list, get_dm_whitelist,
    get_aliases,
    CREATOR_ID, CONFIG_FILE, DM_WHITELIST_FILE
)


# ── Constants ───────────────────────────────────────────────────

# Compiled once instead of being rebuilt on every message.
_REACTION_TRIGGERS = (
    'dead', 'dying', 'bro', 'lmao', 'lol', 'bruh', 'wtf',
    'crying', 'sad', 'sob', 'fuck', 'damn',
)


class Neixo(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.all()
        discord.utils.setup_logging(level=logging.INFO)

        async def get_prefix(bot, message):
            return [".", f"{bot.user.mention} "] if bot.user else ["."]

        super().__init__(command_prefix=get_prefix, intents=intents, help_command=None, case_insensitive=True)

        # Command-channel rule enforcement (guild only).
        # Built from each cog's COG_META: maps "qualified_name" -> category_id.
        self._cmd_to_category: dict[str, str] = {}
        # For resolving admin rule targets that might be either category or command meta keys.
        self._known_rule_targets: set[str] = set()

        self.persistent_views_added = False
        self.start_time = discord.utils.utcnow()

    def _rewrite_alias(self, message: discord.Message) -> None:
        """
        If the message starts with a known prefix ("." or the bot mention)
        and the first token is bound to an alias, rewrite it to the real
        command name in-place.
        """
        content = message.content
        mention = f"{self.user.mention} " if self.user else None
        if content.startswith("."):
            head, sep, rest = content[1:].partition(" ")
        elif mention and content.startswith(mention):
            head, sep, rest = content[len(mention):].partition(" ")
        else:
            return
        target = get_aliases().get(head.lower())
        if target and target != head.lower():
            message.content = f".{target}{sep}{rest}"

    async def on_command_error(self, ctx, error) -> None:
        # unwrap CommandInvokeError to get the real cause
        original = getattr(error, "original", error)

        # silent: typed prefix didn't match a real command, or DM-only contexts
        if isinstance(error, commands.CommandNotFound):
            return

        # user-facing, friendly replies
        try:
            if isinstance(error, commands.MissingRequiredArgument):
                return await ctx.send(f"-# missing `{error.param.name}` — see `.help {ctx.command}`")
            if isinstance(error, commands.BadArgument) or isinstance(
                error,
                (commands.MemberNotFound, commands.UserNotFound,
                 commands.ChannelNotFound, commands.RoleNotFound),
            ):
                return await ctx.send(f"-# couldn't find that — `{error}`")
            if isinstance(error, commands.CommandOnCooldown):
                return await ctx.send(f"-# slow down, try again in {int(error.retry_after)}s")
            if isinstance(error, commands.MissingPermissions):
                return await ctx.send("-# you don't have permission to do that")
            if isinstance(error, commands.BotMissingPermissions):
                return await ctx.send(f"-# i'm missing perms: {', '.join(error.missing_permissions)}")
            if isinstance(error, commands.NoPrivateMessage):
                return await ctx.send("-# this command only works in a server")
            if isinstance(error, commands.CheckFailure):
                return  # check funcs already messaged the user

            # anything else: log full trace, give the user a generic message
            logging.exception("unhandled command error in %r", ctx.command, exc_info=original)
            await ctx.send("-# something broke on my end, i logged it.")
        except discord.HTTPException:
            pass

    async def _build_cmd_category_index(self) -> None:
        """
        Build:
          - self._cmd_to_category: {"qualified_name": "category_id"}
          - self._known_rule_targets: {"category_id", "command meta keys", ...}
        """
        from cogs.help import _collect
        from utils import get_help_meta

        cmd_to_cat: dict[str, str] = {}
        known_targets: set[str] = set()

        try:
            # Collect everything (owners see all)
            categories, _ = _collect(self, is_owner=True, is_wl=True, has_admin=True)

            for cat_id, cat in categories.items():
                known_targets.add(cat_id)
                for sec_label, cmds in cat["sections"].items():
                    for cmd_name, d in cmds:
                        known_targets.add(cmd_name)
                        cmd_to_cat[cmd_name] = cat_id

            self._cmd_to_category = cmd_to_cat
            self._known_rule_targets = known_targets

            # Developer warning check
            import sys
            undocumented = []
            for cog in self.cogs.values():
                meta = getattr(cog.__class__, "COG_META", None)
                if not meta:
                    module = sys.modules.get(cog.__class__.__module__)
                    meta = getattr(module, "COG_META", None)
                if not meta or not isinstance(meta, dict):
                    continue

                def _check_cmd(cmd):
                    if get_help_meta(cmd) is None:
                        undocumented.append(cmd.qualified_name)
                    if isinstance(cmd, commands.Group):
                        for subcmd in cmd.commands:
                            _check_cmd(subcmd)

                for cmd in cog.get_commands():
                    _check_cmd(cmd)

            if undocumented:
                logging.warning(
                    f"[Developer Warning] The following commands are missing @help_meta: {', '.join(sorted(undocumented))}"
                )
        except Exception:
            logging.exception("failed to build command/category index for cmd-channel rules")


    async def _channel_rule_check(self, ctx: commands.Context) -> bool:
        # Guild only (no DMs).
        if ctx.guild is None or not ctx.command:
            return True

        # Ensure index is ready (fallback if setup_hook didn't run yet).
        if not self._known_rule_targets or not self._cmd_to_category:
            await self._build_cmd_category_index()

        qualified = (ctx.command.qualified_name or ctx.command.name or "").lower().strip()
        if not qualified:
            return True

        # Target keys:
        # 1) command-specific meta key
        # 2) category meta key
        from utils import get_cmd_channel_rule

        async def _apply_rule(rule: dict, denied_msg: str) -> bool | None:
            if not rule:
                return None
            mode = (rule.get("mode") or "").lower()
            channels = set(str(c) for c in (rule.get("channels") or []))
            if mode == "allow":
                allowed = str(ctx.channel.id) in channels
            elif mode == "deny":
                allowed = str(ctx.channel.id) not in channels
            else:
                logging.warning("invalid command-channel rule mode %r for %r", mode, ctx.command.qualified_name)
                return False
            if not allowed:
                try:
                    await ctx.send(denied_msg)
                except discord.HTTPException:
                    pass
                return False
            return True

        # command-specific override (check full qualified name, then parent groups)
        parts = qualified.split()
        candidates = [qualified] + [" ".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)]

        for target in candidates:
            cmd_allowed = await _apply_rule(
                get_cmd_channel_rule(ctx.guild.id, target),
                f"-# `.{ctx.command.qualified_name}` is disabled in this channel.",
            )
            if cmd_allowed is not None:
                return cmd_allowed

        cat_id = self._cmd_to_category.get(qualified)
        if not cat_id and len(parts) > 1:
            cat_id = self._cmd_to_category.get(parts[0])
        if cat_id:
            cat_allowed = await _apply_rule(
                get_cmd_channel_rule(ctx.guild.id, cat_id),
                "-# this command is restricted to other channels.",
            )
            if cat_allowed is not None:
                return cat_allowed

        return True

    async def _disabled_command_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None or not ctx.command:
            return True
        from utils import is_command_disabled

        qualified = (ctx.command.qualified_name or ctx.command.name or "").lower().strip()
        if not qualified:
            return True
        if is_command_disabled(ctx.guild.id, qualified):
            try:
                await ctx.send(f"-# `.{qualified}` is disabled.")
            except discord.HTTPException:
                pass
            return False
        return True

    async def setup_hook(self) -> None:
        # ── Lavalink ──────────────────────────────────────────
        lavalink_uri  = os.getenv("LAVALINK_URI",  "http://localhost:2333")
        lavalink_pass = os.getenv("LAVALINK_PASS", "youshallnotpass")
        nodes = [
            wavelink.Node(uri=lavalink_uri, password=lavalink_pass, resume_timeout=90),
        ]
        try:
            await wavelink.Pool.connect(nodes=nodes, client=self, cache_capacity=100)
            logging.info("Wavelink connected")
        except Exception as e:
            logging.error(f"Wavelink connection failed: {e}")

        # ── Index command/category rules for channel enforcement ──
        try:
            await self._build_cmd_category_index()
        except Exception:
            logging.exception("failed to build command/category index for cmd-channel rules")

        # Register global channel rule check.
        # Added here because some discord.py versions require checks after init.
        try:
            self.add_check(self._channel_rule_check)  # type: ignore[arg-type]
        except Exception:
            logging.exception("failed to register channel command rule check")

        # Register global disabled-command check.
        try:
            self.add_check(self._disabled_command_check)  # type: ignore[arg-type]
        except Exception:
            logging.exception("failed to register disabled-command check")

        # ── Register persistent views ─────────────────────────
        if not self.persistent_views_added:
            try:
                from cogs.confessions import ConfessionButtons
                self.add_view(ConfessionButtons(self, 0))
                self.persistent_views_added = True
                logging.info("Persistent views registered")
            except Exception:
                logging.exception("failed to register persistent confession views")

        # ── Sync slash commands (rate-limited by Discord, handle 429 gracefully) ──
        try:
            synced = await self.tree.sync()
            logging.info(f"Synced {len(synced)} slash commands globally")
        except discord.HTTPException as e:
            if e.status == 429:
                logging.warning("Slash command sync rate-limited, will sync on next ready")
            else:
                logging.error(f"Failed to sync commands: {e}")

    async def on_ready(self) -> None:
        logging.info(f"Logged in: {self.user} | {self.user.id}")
        try:
            from cogs.profile import load_saved_presence
            status, activity = await load_saved_presence()
        except Exception as e:
            logging.warning(f"failed to load saved presence: {e}")
            status = discord.Status.dnd
            activity = discord.Activity(
                type=discord.ActivityType.listening,
                name="discord.gg/seoulities",
            )
        await self.change_presence(status=status, activity=activity)

    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        logging.info(f"Wavelink node connected: {payload.node!r} | Resumed: {payload.resumed}")

    async def on_message(self, message: discord.Message) -> None:
        # ── Ignore bots ───────────────────────────────────────
        if message.author.bot:
            return

        # ── Ignore list (block ALL commands and AI for ignored users) ─
        if message.author.id in get_ignore_list():
            return

        # ── Custom alias rewrite ──────────────────────────────
        # One-shot only (no chained alias→alias expansion).
        self._rewrite_alias(message)

        # Always process commands first
        await self.process_commands(message)

        # ── DM AI handling ────────────────────────────────────
        if not message.guild:
            # Cached — was hitting disk on every DM before.
            dm_whitelist = get_dm_whitelist()
            if message.author.id == CREATOR_ID or message.author.id in dm_whitelist:
                # don't double-fire: real commands are handled by process_commands
                if (await self.get_context(message)).valid:
                    return
                cog = self.get_cog("AI")
                if cog:
                    await cog.handle_dm_ai_response(message)
            return

        # ── Guild AI handling ─────────────────────────────────

        # If this is a real command we already handled it via process_commands,
        # so skip the AI flow entirely.
        ctx = await self.get_context(message)
        if ctx.valid:
            return

        config = get_config()
        guild_config = config.get(str(message.guild.id), {})
        ai_channels = guild_config.get('ai_channels', [])

        cog = self.get_cog("AI")
        if not cog:
            return

        is_ai_channel = str(message.channel.id) in ai_channels
        is_mention = bool(re.search(rf"<@!?{self.user.id}>", message.content))
        resolved_reference = message.reference.resolved if message.reference else None
        if resolved_reference is None and message.reference and message.reference.message_id:
            try:
                resolved_reference = await message.channel.fetch_message(message.reference.message_id)
            except Exception:
                resolved_reference = None

        is_reply_to_bot = bool(
            message.reference
            and getattr(resolved_reference, 'author', None) == self.user
        )
        is_reply_to_ai = is_reply_to_bot and getattr(resolved_reference, 'id', None) in cog._ai_chat_ids

        # ── Decide whether to trigger AI ─────────────────────
        trigger_ai = False

        if is_ai_channel and (is_mention or is_reply_to_ai):
            # In AI channels, respond to everything (commands already filtered above),
            # but skip replies to non-AI bot messages (e.g. command output embeds).
            trigger_ai = True

        if trigger_ai:
            # Store context for AI memory
            await cog.store_message_context(message)

            # 15% chance of dropping a 😭 reaction on relatable messages.
            if random.random() < 0.15:
                content_lower = message.content.lower()
                if any(word in content_lower for word in _REACTION_TRIGGERS):
                    try:
                        await message.add_reaction('😭')
                    except Exception:
                        pass

            await cog.handle_ai_response(message)
        elif is_ai_channel and not is_reply_to_bot:
            # In AI channels, store context for passive messages too
            # so the AI has conversation history when it IS addressed later.
            # Skip bot replies (command output) to keep history clean.
            await cog.store_message_context(message)

    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        # Re-run a command when the user edits a typo into a real one
        # (e.g. ".plya song" → ".play song"). We only fire when the *old*
        # content didn't resolve to a valid command and the *new* one does,
        # to avoid double-running anything that already worked.
        if after.author.bot:
            return
        if after.author.id in get_ignore_list():
            return
        if before.content == after.content:
            return
        # only re-run within ~60s of the original send
        try:
            age = (discord.utils.utcnow() - before.created_at).total_seconds()
        except Exception:
            age = 0
        if age > 60:
            return
        self._rewrite_alias(after)
        old_ctx = await self.get_context(before)
        new_ctx = await self.get_context(after)
        if not new_ctx.valid:
            return
        if old_ctx.valid:
            return
        await self.invoke(new_ctx)


# ── Bot instance ────────────────────────────────────────────────

bot = Neixo()


# ── Dev / debug commands ────────────────────────────────────────

def _is_creator():
    async def predicate(ctx):
        return ctx.author.id == CREATOR_ID
    return commands.check(predicate)


@bot.command(name="debughelp")
@_is_creator()
async def debug_help(ctx):
    from cogs.help import _collect
    cats, cmd_index = _collect(bot, True, True, True)
    lines = []
    if not cats:
        lines.append("cats is EMPTY — no COG_META found in any cog")
    for cat_id, cat in cats.items():
        for sec, cmds in cat["sections"].items():
            lines.append(f"{cat_id} > {sec}: {len(cmds)} cmds")
    await ctx.send("\n".join(lines) if lines else "nothing found")


@bot.command(name="reload", aliases=["rl"])
@_is_creator()
async def reload_cog(ctx, *, cog: str):
    """Reload a cog without restarting. Usage: .reload music"""
    # support both "music" and "cogs.music"
    ext = cog if cog.startswith("cogs.") else f"cogs.{cog}"
    try:
        await bot.reload_extension(ext)
        await ctx.send(f"-# ✓ reloaded `{ext}`")
    except commands.ExtensionNotLoaded:
        # wasn't loaded yet — try loading it fresh
        try:
            await bot.load_extension(ext)
            await ctx.send(f"-# ✓ loaded `{ext}` (wasn't loaded before)")
        except Exception as e:
            await ctx.send(f"-# ✗ failed to load `{ext}`: `{e}`")
    except Exception as e:
        await ctx.send(f"-# ✗ failed to reload `{ext}`: `{e}`")


@bot.command(name="load")
@_is_creator()
async def load_cog_cmd(ctx, *, cog: str):
    """Load a cog that isn't loaded yet. Usage: .load music"""
    ext = cog if cog.startswith("cogs.") else f"cogs.{cog}"
    try:
        await bot.load_extension(ext)
        await ctx.send(f"-# ✓ loaded `{ext}`")
    except Exception as e:
        await ctx.send(f"-# ✗ failed: `{e}`")


@bot.command(name="unload")
@_is_creator()
async def unload_cog_cmd(ctx, *, cog: str):
    """Unload a cog. Usage: .unload music"""
    ext = cog if cog.startswith("cogs.") else f"cogs.{cog}"
    try:
        await bot.unload_extension(ext)
        await ctx.send(f"-# ✓ unloaded `{ext}`")
    except Exception as e:
        await ctx.send(f"-# ✗ failed: `{e}`")


@bot.command(name="reloadall", aliases=["rla"])
@_is_creator()
async def reload_all(ctx):
    """Reload every currently loaded cog. Usage: .reloadall"""
    results = []
    # snapshot the list first since we're modifying during iteration
    exts = list(bot.extensions.keys())
    for ext in exts:
        try:
            await bot.reload_extension(ext)
            results.append(f"✓ `{ext}`")
        except Exception as e:
            results.append(f"✗ `{ext}` — {e}")
    await ctx.send("-# " + "\n-# ".join(results))


@bot.command(name="cogs")
@_is_creator()
async def list_cogs(ctx):
    """List all currently loaded cogs."""
    loaded = list(bot.extensions.keys())
    if not loaded:
        return await ctx.send("-# no cogs loaded.")
    await ctx.send("-# loaded cogs:\n" + "\n".join(f"-# • `{e}`" for e in loaded))


@bot.command(name="syncslash")
@_is_creator()
async def sync_slash_cmd(ctx, guild_id: int = None):
    """Sync application slash commands globally or to a specific guild for instant testing."""
    if guild_id:
        guild = discord.Object(id=guild_id)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        await ctx.send(f"-# synced {len(synced)} slash commands to guild `{guild_id}`")
    else:
        synced = await bot.tree.sync()
        await ctx.send(f"-# synced {len(synced)} slash commands globally")


# ── Slash Commands ──────────────────────────────────────────────

@bot.tree.command(name="confess", description="Submit an anonymous confession")
async def confess_slash(interaction: discord.Interaction):
    from cogs.confessions import ConfessionModal
    modal = ConfessionModal(bot)
    await interaction.response.send_modal(modal)


@bot.tree.command(name="ping", description="Check the bot's latency")
async def ping_slash(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    embed = discord.Embed(description=f"{latency}ms", color=0xFF0000)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="avatar", description="View full resolution avatar")
@app_commands.describe(user="User to view avatar for (defaults to you)")
async def avatar_slash(interaction: discord.Interaction, user: discord.User = None):
    target = user or interaction.user
    from cogs.userinfo import AvatarView
    member = interaction.guild.get_member(target.id) if interaction.guild else None
    view = AvatarView(interaction.user.id, member or target, interaction.guild_id or 0)
    embed = view.build_embed()
    await interaction.response.send_message(embed=embed, view=view if len(view.children) > 0 else None)


@bot.tree.command(name="banner", description="View user profile banner")
@app_commands.describe(user="User to view banner for (defaults to you)")
async def banner_slash(interaction: discord.Interaction, user: discord.User = None):
    target = user or interaction.user
    try:
        full_user = await bot.fetch_user(target.id)
    except Exception:
        full_user = target

    from utils import get_embed_color
    color = get_embed_color(interaction.guild_id or 0)

    if full_user.banner:
        banner_url = full_user.banner.url
        png_url = full_user.banner.replace(format="png", size=4096).url
        webp_url = full_user.banner.replace(format="webp", size=4096).url
        links = f"[png]({png_url}) · [webp]({webp_url})"
        if full_user.banner.is_animated():
            gif_url = full_user.banner.replace(format="gif", size=4096).url
            links += f" · [gif]({gif_url})"

        embed = discord.Embed(
            description=f"-# {full_user.name}'s banner · {links}",
            color=color,
        )
        embed.set_image(url=banner_url)
        await interaction.response.send_message(embed=embed)
    elif getattr(full_user, "accent_color", None):
        hex_c = f"#{full_user.accent_color.value:06x}"
        embed = discord.Embed(
            description=f"-# {full_user.name} has no banner (accent: `{hex_c}`)",
            color=full_user.accent_color.value,
        )
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message(f"-# {full_user.name} has no banner", ephemeral=True)


@bot.tree.command(name="serverinfo", description="View server stats and info card")
async def serverinfo_slash(interaction: discord.Interaction):
    if not interaction.guild:
        return await interaction.response.send_message("-# this command only works in servers", ephemeral=True)
    await interaction.response.defer()
    cog = bot.get_cog("ServerStats")
    if cog and hasattr(cog, "serverinfo"):
        temp = await interaction.channel.send(".serverinfo")
        try:
            temp.author = interaction.user
            ctx = await bot.get_context(temp)
            await bot.invoke(ctx)
        finally:
            try:
                await temp.delete()
            except Exception:
                pass
            await interaction.followup.send("-# done", ephemeral=True)
    else:
        await interaction.followup.send("-# serverstats not loaded", ephemeral=True)



@bot.tree.command(name="echo", description="Send a message as the bot")
async def echo_slash(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            "this command only works in servers.",
            ephemeral=True
        )
        return

    config = load_json(CONFIG_FILE)
    guild_config = config.get(str(interaction.guild_id), {})
    whitelist = guild_config.get('whitelist', [])

    is_owner_or_creator = (
        interaction.user.id == interaction.guild.owner_id
        or interaction.user.id == CREATOR_ID
    )
    if not is_owner_or_creator and str(interaction.user.id) not in {str(uid) for uid in whitelist}:
        await interaction.response.send_message(
            "no perms",
            ephemeral=True
        )
        return

    from cogs.misc import EchoModal
    modal = EchoModal()
    await interaction.response.send_modal(modal)


# ── Music slash commands ────────────────────────────────────────

async def _music_err(interaction: discord.Interaction, msg: str):
    try:
        await interaction.response.send_message(f"-# {msg}", ephemeral=True)
    except discord.errors.InteractionResponded:
        await interaction.followup.send(f"-# {msg}", ephemeral=True)


@bot.tree.command(name="play", description="Play a song or playlist from YouTube, Spotify, or SoundCloud")
@app_commands.describe(query="Song name, artist, or URL")
async def play_slash(interaction: discord.Interaction, query: str):
    cog = bot.get_cog("Music")
    if not cog:
        return await _music_err(interaction, "music cog not loaded.")
    if not interaction.guild:
        return await _music_err(interaction, "this command only works in a server.")
    user_vc = interaction.user.voice.channel if interaction.user.voice else None
    if not user_vc:
        return await _music_err(interaction, "join a voice channel first.")
    bot_vc = interaction.guild.voice_client
    if bot_vc and bot_vc.channel != user_vc:
        return await _music_err(interaction, f"you need to be in {bot_vc.channel.mention} to use this.")
    await interaction.response.defer()
    temp = await interaction.channel.send(f".play {query.replace(chr(10), ' ')}")
    try:
        ctx = await bot.get_context(temp)
        ctx.author = interaction.user
        await bot.invoke(ctx)
    finally:
        try:
            await temp.delete()
        except discord.HTTPException:
            pass


@bot.tree.command(name="skip", description="Skip the current track")
async def skip_slash(interaction: discord.Interaction):
    cog = bot.get_cog("Music")
    if not cog:
        return await _music_err(interaction, "music cog not loaded.")
    if not interaction.guild:
        return await _music_err(interaction, "this command only works in a server.")
    player = interaction.guild.voice_client
    if not player or not player.playing:
        return await _music_err(interaction, "nothing is playing rn.")
    user_vc = interaction.user.voice.channel if interaction.user.voice else None
    if not user_vc or player.channel != user_vc:
        return await _music_err(interaction, f"you need to be in {player.channel.mention} to use this.")
    await interaction.response.defer()
    await player.skip(force=True)
    await interaction.followup.send(embed=discord.Embed(description="-# skipped.", color=0x121516))


@bot.tree.command(name="pause", description="Pause playback")
async def pause_slash(interaction: discord.Interaction):
    cog = bot.get_cog("Music")
    if not cog:
        return await _music_err(interaction, "music cog not loaded.")
    if not interaction.guild:
        return await _music_err(interaction, "this command only works in a server.")
    player = interaction.guild.voice_client
    if not player or not player.playing:
        return await _music_err(interaction, "nothing is playing rn.")
    user_vc = interaction.user.voice.channel if interaction.user.voice else None
    if not user_vc or player.channel != user_vc:
        return await _music_err(interaction, f"you need to be in {player.channel.mention} to use this.")
    await player.pause(True)
    await interaction.response.send_message(embed=discord.Embed(description="-# paused.", color=0x121516))
    await cog._update_vc_status(player.channel.id, "paused | Neixo")


@bot.tree.command(name="resume", description="Resume paused playback")
async def resume_slash(interaction: discord.Interaction):
    cog = bot.get_cog("Music")
    if not cog:
        return await _music_err(interaction, "music cog not loaded.")
    if not interaction.guild:
        return await _music_err(interaction, "this command only works in a server.")
    player = interaction.guild.voice_client
    if not player:
        return await _music_err(interaction, "not connected to voice.")
    if not player.paused:
        return await _music_err(interaction, "not paused.")
    user_vc = interaction.user.voice.channel if interaction.user.voice else None
    if not user_vc or player.channel != user_vc:
        return await _music_err(interaction, f"you need to be in {player.channel.mention} to use this.")
    await player.pause(False)
    await interaction.response.send_message(embed=discord.Embed(description="-# resumed.", color=0x121516))
    if player.current:
        await cog._update_vc_status(player.channel.id, f"{player.current.title} | Neixo")


@bot.tree.command(name="queue", description="Show the music queue")
async def queue_slash(interaction: discord.Interaction):
    cog = bot.get_cog("Music")
    if not cog:
        return await _music_err(interaction, "music cog not loaded.")
    if not interaction.guild:
        return await _music_err(interaction, "this command only works in a server.")
    player = interaction.guild.voice_client
    if not player:
        return await _music_err(interaction, "not connected to voice.")
    await interaction.response.defer()
    temp = await interaction.channel.send(".queue")
    try:
        ctx = await bot.get_context(temp)
        ctx.author = interaction.user
        await bot.invoke(ctx)
    finally:
        try:
            await temp.delete()
        except discord.HTTPException:
            pass


@bot.tree.command(name="nowplaying", description="Show the currently playing track")
async def nowplaying_slash(interaction: discord.Interaction):
    cog = bot.get_cog("Music")
    if not cog:
        return await _music_err(interaction, "music cog not loaded.")
    if not interaction.guild:
        return await _music_err(interaction, "this command only works in a server.")
    player = interaction.guild.voice_client
    if not player or not player.playing:
        return await _music_err(interaction, "nothing is playing rn.")
    await interaction.response.defer()
    temp = await interaction.channel.send(".nowplaying")
    try:
        ctx = await bot.get_context(temp)
        ctx.author = interaction.user
        await bot.invoke(ctx)
    finally:
        try:
            await temp.delete()
        except discord.HTTPException:
            pass


@bot.tree.command(name="help", description="Browse all commands or get help with a specific one")
@app_commands.describe(command="Optional: get details for a specific command (e.g. play)")
async def help_slash(interaction: discord.Interaction, command: str = None):
    await interaction.response.defer(ephemeral=True)
    temp = await interaction.channel.send(f".help {command}" if command else ".help")
    try:
        temp.author = interaction.user
        ctx = await bot.get_context(temp)
        await bot.invoke(ctx)
    finally:
        try:
            await temp.delete()
        except discord.HTTPException:
            pass
        # complete the deferred interaction — otherwise the client keeps
        # "Neixo is thinking..." forever after every /help
        try:
            await interaction.followup.send(content="done.", ephemeral=True)
        except discord.HTTPException:
            pass


# ── Cog loader ──────────────────────────────────────────────────

async def load_cogs() -> None:
    dirs = ["./cogs", "./cogs/events"]
    # Files that are helpers/views (not actual cogs with setup functions)
    skip_files = {"music_helpers.py", "music_views.py", "theme_helpers.py", "theme_views.py"}
    for d in dirs:
        if not os.path.exists(d):
            continue
        for f in os.listdir(d):
            if f.endswith(".py") and f != "__init__.py" and f not in skip_files:
                ext = d.replace("./", "").replace("/", ".") + f".{f[:-3]}"
                try:
                    await bot.load_extension(ext)
                    logging.info(f"Loaded {ext}")
                except Exception as e:
                    logging.error(f"Failed to load {ext}: {e}")


async def main() -> None:
    async with bot:
        await load_cogs()
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            raise ValueError("DISCORD_TOKEN not set in .env")
        await bot.start(token)


if __name__ == '__main__':
    asyncio.run(main())
