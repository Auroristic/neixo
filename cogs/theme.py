"""
cogs/theme.py  —  NeixO Theme System
Server theme management: role names/icons, channel prefixes, unicode font styles.

Commands (prefix: .):
  .theme                  — show current theme summary
  .theme help             — full command reference

  Role mapping (one-time setup per server):
  .theme setup            — interactive dropdown wizard to map server roles → slots
  .theme setrole <slot> @Role  — quickly remap one slot
  .theme roles            — list all mapped slots

  Role renaming:
  .theme role <slot> <new name>         — rename a mapped role
  .theme roleicon <slot> [emoji|url|attachment]  — set role icon (attach image or pass URL)
  .theme resetrole <slot>               — revert one role to its snapshotted name

  Channel prefix:
  .theme prefix scan <#category>        — auto-detect existing prefix in a category
  .theme prefix add <emoji> <#category> [<#cat2> ...]  — add/replace prefix
  .theme prefix remove <#category> [<#cat2> ...]       — remove prefix from channels
  .theme prefix list                    — show all stored prefixes

  Channel font:
  .theme font list                      — show all available unicode fonts with examples
  .theme font set <font> [all|<#cat> [<#cat2> ...]]    — apply font to channels
  .theme font reset [all|<#cat>]        — strip font from channels

  Full theme apply / save / reset:
  .theme save <name>      — save current server state as a named preset
  .theme apply <name>     — preview + confirm applying a saved preset
  .theme presets          — list all saved presets
  .theme delete <name>    — delete a saved preset
  .theme reset            — undo last .theme apply (restores snapshot)
  .theme snapshot         — show what the current snapshot contains
"""

import asyncio
import base64
import logging
import time

import discord
from discord.ext import commands
from discord.ui import Button, View

import theme_manager as tm
from cogs.theme_helpers import (
    _close_http_session,
    _edit_progress,
    _embed,
    _err_embed,
    _get_http_session,
    _is_theme_admin,
    _ok_embed,
    _resolve_icon_bytes,
    _resolve_role_slot,
)
from cogs.theme_views import ConfirmView, PreviewView, RolePickerView, RoleSlotModal
from neixoconfig import Neixocolor
from utils import help_meta

log = logging.getLogger(__name__)

COG_META = {
    "category": "theme",
    "label": "Theme",
    "desc": "Server theme management — roles, channel prefixes, unicode fonts.",
    "owner": False
}


# ── The Cog ───────────────────────────────────────────────────────
class ThemeCog(commands.Cog, name="Theme"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # in-memory cache: guild_id -> {n: prefix}
        self._last_scan = {}
        # load persisted last_scan entries for guilds the bot is in
        for g in getattr(bot, "guilds", []):
            ls = tm.get_last_scan(g.id)
            if ls:
                self._last_scan[g.id] = ls
        # per-target last edit timestamp for a simple rate-limiter
        # keys are strings like 'ch:<id>' or 'guild:<id>' to avoid collisions
        self._last_edit: dict[str, float] = {}

    async def cog_load(self):
        await _get_http_session()

    async def cog_unload(self):
        await _close_http_session()

    async def _rate_limit_for_channel(self, ch: discord.abc.GuildChannel):
        """Per-channel rate limiting: allow ~2 edits per 10s per channel.
        Only sleeps when the min interval hasn't elapsed — no unnecessary delay."""
        self._evict_edit_cache()
        key = f"ch:{ch.id}"
        last = self._last_edit.get(key, 0)
        now = time.monotonic()
        elapsed = now - last
        min_interval = 5.0
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        self._last_edit[key] = time.monotonic()

    async def _bulk_rename_channels(
        self,
        ctx: commands.Context,
        channels: list[discord.abc.GuildChannel],
        *,
        new_name_for: callable,
        reason: str,
        prog: discord.Message,
        failures: list,
        progress_total: int,
        rate_limit_kind: str = "channel",
    ) -> int:
        """
        Apply name edits to multiple channels with:
        - per-item timeout
        - progress updates
        - failure collection
        - per-channel (or guild) rate limiting
        new_name_for(ch) -> str (desired name)
        """
        done = 0
        for ch in channels:
            try:
                new_name = new_name_for(ch)
            except Exception as exc:
                self._collect_failure(failures, "channel", getattr(ch, "id", None), getattr(ch, "name", ""), exc)
                done += 1
                await _edit_progress(prog, done, progress_total, getattr(ch, "name", str(getattr(ch, "id", ""))))
                continue

            if new_name != getattr(ch, "name", None):
                try:
                    await asyncio.wait_for(
                        ch.edit(name=new_name, reason=reason),
                        timeout=10.0,
                    )
                except (discord.HTTPException, asyncio.TimeoutError) as exc:
                    self._collect_failure(failures, "channel", ch.id, ch.name, exc)
                if rate_limit_kind == "guild":
                    await self._rate_limit_for_guild(ctx.guild)
                else:
                    await self._rate_limit_for_channel(ch)

            done += 1
            await _edit_progress(prog, done, progress_total, ch.name)
        return done

    async def _rate_limit_for_guild(self, guild: discord.Guild):
        """Per-guild rate limiting for role/guild edits to avoid bursts.
        Only sleeps when the min interval hasn't elapsed — no unnecessary delay."""
        self._evict_edit_cache()
        key = f"guild:{guild.id}"
        last = self._last_edit.get(key, 0)
        now = time.monotonic()
        elapsed = now - last
        min_interval = 5.0
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        self._last_edit[key] = time.monotonic()

    def _evict_edit_cache(self):
        now = time.monotonic()
        stale_before = now - 300
        to_del = [k for k, v in self._last_edit.items() if v < stale_before]
        for k in to_del:
            del self._last_edit[k]

    def _collect_failure(self, failures: list, kind: str, id_: int | str, name: str, exc: Exception):
        failures.append((kind, id_, name, str(exc)))
        log.warning(f"{kind} edit failed {id_} {name}: {exc}")

    async def _report_failures(self, ctx: commands.Context, failures: list, title: str = "Edit failures"):
        if not failures:
            return
        # show up to 10 failures inline
        lines = []
        for kind, id_, name, err in failures[:10]:
            lines.append(f"• **{kind}** `{name}` ({id_}): {err}")
        if len(failures) > 10:
            lines.append(f"... and {len(failures) - 10} more failures")
        await ctx.send(embed=_embed(ctx, title, "\n".join(lines)))

    # ── Root command ──────────────────────────────────────────

    @commands.group(name="theme", invoke_without_command=True)
    @_is_theme_admin()
    @help_meta(
        usage="`.theme`",
        desc="Shows the current theme summary — roles, prefixes, font, and snapshot status.",
        section="Overview",
        examples=[".theme"],
        params=[],
        note="This is the entry point. Use `.theme help` to see all subcommands.",
    )
    async def theme(self, ctx: commands.Context):
        """Show current theme summary for this server."""
        gid = ctx.guild.id
        role_map = tm.get_role_map(gid)
        theme    = tm.get_guild_theme(gid)
        snap     = tm.get_snapshot(gid)

        e = _embed(ctx, "🎨 server theme")

        # Role slots
        if role_map:
            lines = []
            for slot, rid in role_map.items():
                role = ctx.guild.get_role(int(rid))
                rname = role.mention if role else f"~~deleted ({rid})~~"
                override = (theme or {}).get("roles", {}).get(slot, {})
                styled = override.get("name") or (role.name if role else "?")
                lines.append(f"**{slot}** → {rname} • displayed as `{styled}`")
            e.add_field(name="Role Slots", value="\n".join(lines), inline=False)
        else:
            e.add_field(name="Role Slots", value="-# none set — run `.theme setup`", inline=False)

        # Channel prefixes
        if theme and theme.get("channel_prefix"):
            plines = []
            for cat_id, prefix in theme["channel_prefix"].items():
                cat = ctx.guild.get_channel(int(cat_id))
                cname = cat.name if cat else f"(deleted {cat_id})"
                plines.append(f"`{prefix}` → **{cname}**")
            e.add_field(name="Channel Prefixes", value="\n".join(plines), inline=False)

        # Font style
        if theme and theme.get("channel_style"):
            cs = theme["channel_style"]
            font_info = tm.UNICODE_FONTS.get(cs.get("font", ""), {})
            scope = cs.get("scope", "")
            scope_str = "all channels" if scope == "all" else f"{len(scope)} categor(ies)"
            e.add_field(
                name="Channel Font",
                value=f"`{cs.get('font')}` ({font_info.get('label','?')}) · {scope_str}",
                inline=False,
            )

        e.set_footer(text=f"{'✅ snapshot saved' if snap else '⚠️ no snapshot — .theme reset unavailable'} · .theme help for all commands")
        await ctx.send(embed=e)

    # ── Help ──────────────────────────────────────────────────

    @theme.command(name="help")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme help`",
        desc="Shows all theme commands organised by section.",
        section="Setup",
        examples=[".theme help"],
        params=[],
        note="Displays a complete list of theme subcommands grouped by category.",
    )
    async def theme_help(self, ctx: commands.Context):
        e = _embed(ctx, "🎨 theme commands")
        e.add_field(name="Setup", value=(
            "`.theme setup` — interactive role mapping wizard\n"
            "`.theme setrole <slot> @Role` — remap one slot\n"
            "`.theme roles` — list all mapped slots\n"
            "`.theme roles clear` — wipe all slot mappings\n"
            "`.theme roles setup` — re-run mapping wizard"
        ), inline=False)
        e.add_field(name="Roles", value=(
            "`.theme role <slot> <new name>` — rename role\n"
            "`.theme roleicon <slot> [emoji|url]` — set icon (attach image too)\n"
            "`.theme role revert <slot>` — revert to snapshot name"
        ), inline=False)
        e.add_field(name="Prefix Groups", value=(
            "`.theme group setup` / `.tg setup` — scan server + create named groups interactively\n"
            "`.theme group list` / `.tg list` — show all groups with prefixes + categories\n"
            "`.theme group create <name> <prefix>` / `.tg create` — manually create a group\n"
            "`.theme group delete <name>` / `.tg delete` — delete a group record\n"
            "`.theme group set <name> <new prefix>` / `.tg set` — change prefix + rename channels\n"
            "`.theme group add <name> #cat` / `.tg add` — add category to group\n"
            "`.theme group remove <name> #cat` / `.tg remove` — remove category from group\n"
            "`.theme group apply <name> #ch` / `.tg apply` — stamp prefix on one channel"
        ), inline=False)
        e.add_field(name="Channel Prefix (manual)", value=(
            "`.theme prefix scan <#cat>` — detect prefix in a category\n"
            "`.theme prefix scan all` — scan entire server\n"
            "`.theme prefix add <emoji> <#cat> [...]` — add/replace\n"
            "`.theme prefix remove <#cat> [...]` — strip prefix\n"
            "`.theme prefix remove all` — strip all prefixes server-wide\n"
            "`.theme prefix server <emoji>` — apply to every channel\n"
            "`.theme prefix replace <n> <new>` — replace a scanned prefix\n"
            "`.theme prefix undo` — undo last prefix operation\n"
            "`.theme prefix list` — show stored prefixes\n"
            "`.theme channel strip <text>` — strip exact text from all channel names"
        ), inline=False)
        e.add_field(name="Channel Font", value=(
            "`.theme font list` — all fonts with examples\n"
            "`.theme font set <font> [all|<#cat> ...]` — apply\n"
            "`.theme font reset [all|<#cat>]` — strip"
        ), inline=False)
        e.add_field(name="Presets & Reset", value=(
            "`.theme save <name>` — save current state as preset\n"
            "`.theme apply <name>` — preview & apply preset\n"
            "`.theme presets` — list presets\n"
            "`.theme delete <name>` — delete preset\n"
            "`.theme reset` — undo last apply (uses snapshot)\n"
            "`.theme snapshot` — view snapshot contents"
        ), inline=False)
        await ctx.send(embed=e)

    # ══════════════════════════════════════════════════════════
    # ROLE MAPPING
    # ══════════════════════════════════════════════════════════

    @theme.command(name="setup")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme setup`",
        desc="Interactive wizard — define custom role slots and map them to server roles via dropdowns.",
        section="Setup",
        examples=[".theme setup"],
        params=[],
        note="This is the recommended starting point for new servers. Guides you through the entire configuration.",
    )
    async def theme_setup(self, ctx: commands.Context):
        gid = ctx.guild.id
        slots: list[str] = []

        # ── Step 1: collect slot names via modal buttons ───────
        class SlotCollectView(View):
            def __init__(self_v):
                super().__init__(timeout=120)

            @discord.ui.button(label="Add Slot", style=discord.ButtonStyle.gray)
            async def add_slot(self_v, interaction: discord.Interaction, button: Button):
                if interaction.user.id != ctx.author.id:
                    return
                async def _cb(inter: discord.Interaction, name: str):
                    if name and name not in slots:
                        slots.append(name)
                    await inter.response.edit_message(
                        content=(
                            f"-# slots so far: {', '.join(f'`{s}`' for s in slots) or 'none'}\n"
                            f"-# click **Add Slot** to add more, **Done** when finished."
                        ),
                        view=self_v,
                    )
                await interaction.response.send_modal(RoleSlotModal(_cb))

            @discord.ui.button(emoji="<:7079verifiedblacksimplified:1255031445806780467>", style=discord.ButtonStyle.gray)
            async def done(self_v, interaction: discord.Interaction, button: Button):
                if interaction.user.id != ctx.author.id:
                    return await interaction.response.defer()
                self_v.stop()
                await interaction.response.edit_message(
                    content=f"-# slots defined: {', '.join(f'`{s}`' for s in slots)}\n-# moving to role assignment...",
                    view=None,
                )

        collect_view = SlotCollectView()
        msg = await ctx.send(
            "-# **Step 1/2** — define your role slots (e.g. Owner, Head of Security, Mod)\n"
            "-# click **Add Slot** for each one, then **Done** when finished.",
            view=collect_view,
        )
        await collect_view.wait()
        await asyncio.sleep(0.2)

        if not slots:
            try:
                await msg.edit(content="-# no slots defined, setup cancelled.", view=None)
            except discord.HTTPException:
                pass
            return

        # ── Step 2: for each slot, send ONE message and await it ──
        role_map = tm.get_role_map(gid)
        mapped = 0

        for i, slot in enumerate(slots):
            event = asyncio.Event()
            picked: dict = {"role_id": None}

            async def _on_pick(interaction: discord.Interaction, _slot=slot, role_id=None):
                nonlocal mapped
                picked["role_id"] = role_id
                if role_id:
                    role_map[_slot] = role_id
                    mapped += 1
                    r = ctx.guild.get_role(role_id)
                    await interaction.response.edit_message(
                        content=f"-# ✅ **{_slot}** → {r.mention if r else role_id}",
                        view=None,
                    )
                else:
                    await interaction.response.edit_message(
                        content=f"-# ⏭ skipped **{_slot}**",
                        view=None,
                    )
                event.set()

            picker_view = RolePickerView(ctx.guild, slot, ctx.author.id, _on_pick)

            await ctx.send(
                f"-# **slot {i+1}/{len(slots)}** — which role is **{slot}**?",
                view=picker_view,
            )

            try:
                await asyncio.wait_for(event.wait(), timeout=60)
            except asyncio.TimeoutError:
                await ctx.send(f"-# timed out waiting for **{slot}**, skipping remaining slots.")
                break

        tm.save_role_map(gid, role_map)
        await ctx.message.add_reaction("✓")
        await ctx.send(embed=_ok_embed(f"setup complete — `{mapped}` slot(s) mapped. use `.theme setrole <slot> @Role` to adjust anytime."))

    @theme.command(name="setrole", aliases=["map", "bind", "m"])
    @_is_theme_admin()
    @help_meta(
        usage="`.theme setrole <slot|#index> [@role]`",
        desc="Remaps a theme slot to a server role. Supports slot number or name.",
        section="Setup",
        perm_tier="admin",
        discord_perms=["manage_roles"],
        examples=[".theme setrole 1 @Owner", ".theme setrole owner @Owner", ".tmap 1 @Owner"],
        params=[
            {"name": "slot", "type": "str", "required": True, "desc": "Slot number (1, 2...) or slot name to map."},
            {"name": "role", "type": "role", "required": False, "desc": "Discord role to map (omit for interactive picker)."},
        ],
        note="Only maps the slot. Use `.theme role` or `.tr` to rename the role itself.",
    )
    async def theme_setrole(self, ctx: commands.Context, slot: str = None, role: discord.Role = None):
        """Remap a slot via dropdown, or pass slot + @Role directly.

        Fast paths:
        - `.theme setrole <slot|#index> @Role`  -> immediate update
        - `.theme setrole <slot|#index>`        -> show the picker for that single slot only
        - no args -> interactive walk through all slots (legacy behavior)
        """
        gid = ctx.guild.id
        role_map = tm.get_role_map(gid)

        # fast path: slot + role provided
        if slot and role:
            resolved_slot, _, _ = _resolve_role_slot(ctx.guild, role_map, slot)
            target_slot = resolved_slot or slot
            tm.add_role_slot(gid, target_slot, role.id)
            await ctx.message.add_reaction("✓")
            return await ctx.send(embed=_ok_embed(f"**{target_slot}** → {role.mention}"))

        if not role_map:
            return await ctx.send("-# no slots defined yet — run `.theme setup` first")

        # fast path: slot only -> show single dropdown for that slot
        if slot and not role:
            resolved_slot, _, _ = _resolve_role_slot(ctx.guild, role_map, slot)
            if not resolved_slot:
                valid_slots = ", ".join(f"`{i}. {s}`" for i, s in enumerate(role_map.keys(), start=1))
                return await ctx.send(f"-# slot not found — see `.theme roles`\n-# valid slots: {valid_slots}")

            target_slot = resolved_slot
            event = asyncio.Event()

            async def _on_pick_single(interaction: discord.Interaction, _slot=target_slot, role_id=None):
                if role_id:
                    tm.add_role_slot(gid, _slot, role_id)
                    r = ctx.guild.get_role(role_id)
                    await interaction.response.edit_message(
                        content=f"-# ✅ **{_slot}** → {r.mention if r else role_id}",
                        view=None,
                    )
                    await ctx.message.add_reaction("✓")
                else:
                    await interaction.response.edit_message(content=f"-# ⏭ kept **{_slot}** unchanged", view=None)
                event.set()

            picker = RolePickerView(ctx.guild, target_slot, ctx.author.id, _on_pick_single)
            await ctx.send(f"-# choose new role for slot **{target_slot}**", view=picker)
            try:
                await asyncio.wait_for(event.wait(), timeout=60)
            except asyncio.TimeoutError:
                return await ctx.send(f"-# timed out waiting for **{target_slot}**")
            return

        # legacy interactive: walk through each slot one by one with a dropdown
        slots = list(role_map.keys())
        updated = 0

        for i, s in enumerate(slots):
            current_role = ctx.guild.get_role(int(role_map[s]))
            current_str = current_role.mention if current_role else "*(deleted)*"

            event = asyncio.Event()

            async def _on_pick(interaction: discord.Interaction, _s=s, role_id=None):
                nonlocal updated
                if role_id:
                    tm.add_role_slot(gid, _s, role_id)
                    updated += 1
                    r = ctx.guild.get_role(role_id)
                    await interaction.response.edit_message(
                        content=f"-# ✅ **{_s}** → {r.mention if r else role_id}",
                        view=None,
                    )
                else:
                    await interaction.response.edit_message(
                        content=f"-# ⏭ kept **{_s}** unchanged",
                        view=None,
                    )
                event.set()

            picker = RolePickerView(ctx.guild, s, ctx.author.id, _on_pick)

            await ctx.send(
                f"-# **slot {i+1}/{len(slots)} — {s}** (currently {current_str})",
                view=picker,
            )

            try:
                await asyncio.wait_for(event.wait(), timeout=60)
            except asyncio.TimeoutError:
                await ctx.send(f"-# timed out on **{s}**, stopping.")
                break

        await ctx.message.add_reaction("✓")
        await ctx.send(embed=_ok_embed(f"done — `{updated}` slot(s) updated"))

    @theme.group(name="roles", aliases=["slots", "list", "ls", "l"], invoke_without_command=True)
    @_is_theme_admin()
    @help_meta(
        usage="`.theme roles`",
        desc="Lists all mapped role slots, their indices, and their current Discord roles.",
        section="Setup",
        examples=[".theme roles", ".troles", ".theme slots"],
        params=[],
        note="Shows the current slot-to-role mappings.",
    )
    async def theme_roles(self, ctx: commands.Context):
        """List all mapped role slots for this server."""
        role_map = tm.get_role_map(ctx.guild.id)
        if not role_map:
            return await ctx.send("-# no slots mapped yet — run `.theme setup`")
        bot_member = ctx.guild.get_member(ctx.bot.user.id)
        lines = []
        for i, (slot, rid) in enumerate(role_map.items(), start=1):
            role = ctx.guild.get_role(int(rid))
            warn = ""
            if role and bot_member and role >= bot_member.top_role:
                warn = " ⚠️ *(move bot above role)*"
            lines.append(f"`{i}.` **{slot}** → {role.mention if role else f'~~deleted ({rid})~~'}{warn}")
        e = _embed(ctx, "✦ Server Role Slots", "\n".join(lines))
        e.set_footer(text="Quick rename: .tr <slot|#> <name>  ·  Quick icon: .tri <slot|#> <icon>")
        await ctx.send(embed=e)

    @theme_roles.command(name="clear")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme roles clear`",
        desc="Wipes all slot mappings. Discord role names are unchanged; remap with `.theme roles setup`.",
        section="Setup",
        examples=[".theme roles clear"],
        params=[],
        note="This only clears the internal mapping, not the actual role names. Run setup again to remap.",
    )
    async def roles_clear(self, ctx: commands.Context):
        """Wipe all slot mappings without restarting the wizard."""
        gid = ctx.guild.id
        role_map = tm.get_role_map(gid)
        if not role_map:
            return await ctx.send("-# no slots to clear — run `.theme roles setup` to start fresh")

        e = _embed(ctx, "clear role slots")
        e.description = (
            f"this will delete all **{len(role_map)}** slot mapping(s).\n"
            f"current slots: {', '.join(f'`{s}`' for s in role_map)}\n\n"
            f"role names in discord are **not** changed — only the bot's slot records are wiped."
        )
        view = ConfirmView(ctx.author.id)
        confirm_msg = await ctx.send(embed=e, view=view)
        await view.wait()

        if not view.confirmed:
            return await confirm_msg.edit(embed=_err_embed("cancelled."), view=None)

        tm.save_role_map(gid, {})
        await confirm_msg.edit(embed=_ok_embed("slot mappings cleared — run `.theme roles setup` to remap."), view=None)

    @theme_roles.command(name="setup")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme roles setup`",
        desc="Runs the role mapping wizard. Shows the current mapping per slot so you can skip or remap.",
        section="Setup",
        examples=[".theme roles setup"],
        params=[],
        note="Interactive — you will be prompted for each slot.",
    )
    async def roles_setup(self, ctx: commands.Context):
        """Run the role mapping wizard. Shows current mappings per slot so you can skip or remap."""
        await ctx.invoke(self.theme_setup)

    # ══════════════════════════════════════════════════════════
    # ROLE EDITING
    # ══════════════════════════════════════════════════════════

    @theme.group(name="role", aliases=["rename", "r"], invoke_without_command=True)
    @_is_theme_admin()
    @help_meta(
        usage="`.theme role <slot|#index> <new name>`",
        desc="Renames the Discord role mapped to a slot. Supports slot index or name. E.g. `.theme role 1 True Dragon` or `.theme role Owner God Emperor`.",
        section="Roles",
        perm_tier="admin",
        discord_perms=["manage_roles"],
        examples=[
            ".theme role 1 True Dragon",
            ".theme role owner God Emperor",
            ".theme role co owner Vice King",
            ".tr 1 True Dragon",
        ],
        params=[
            {"name": "slot", "type": "str", "required": True, "desc": "The slot number (1, 2...) or slot name whose role to rename."},
            {"name": "new_name", "type": "str", "required": True, "desc": "The new display name for the role."},
        ],
        note="This actually changes the role name on Discord. Use `.theme role revert` or `.trevert` to undo.",
    )
    async def theme_role(self, ctx: commands.Context, *, args: str = None):
        """Walk through all slots with skip/rename/done buttons, or fast rename a specific slot."""
        role_map = tm.get_role_map(ctx.guild.id)
        if not role_map:
            return await ctx.send("-# no slots mapped yet — run `.theme setup` first")

        bot_member = ctx.guild.get_member(ctx.bot.user.id)

        # fast path if args provided (e.g. .theme role 1 True Dragon or .theme role co owner Vice King)
        if args and args.strip():
            slot, role, new_name = _resolve_role_slot(ctx.guild, role_map, args)
            if not slot:
                valid_slots = ", ".join(f"`{i}. {s}`" for i, s in enumerate(role_map.keys(), start=1))
                return await ctx.send(f"-# slot not found — see `.theme roles`\n-# valid slots: {valid_slots}")

            if not new_name:
                return await ctx.send(f"-# usage: `.theme role {slot} <new name>`")

            if not role:
                return await ctx.send(f"-# role for slot `{slot}` no longer exists in server")

            if bot_member and role >= bot_member.top_role:
                return await ctx.send(f"-# can't edit **{role.name}** — move my bot role above it first.")

            await self._ensure_snapshot(ctx.guild)
            old = role.name
            await self._rate_limit_for_guild(ctx.guild)
            try:
                await role.edit(name=new_name, reason=f"NeixO theme: renamed by {ctx.author}")
            except discord.HTTPException as exc:
                failures: list = []
                self._collect_failure(failures, "role", slot, new_name, exc)
                await self._report_failures(ctx, failures, "role rename failures")
                return await ctx.send(embed=_err_embed(f"failed to rename **{slot}**: {exc}"))

            gtheme = tm.get_guild_theme(ctx.guild.id) or tm.build_empty_theme()
            gtheme.setdefault("roles", {}).setdefault(slot, {})["name"] = new_name
            tm.save_guild_theme(ctx.guild.id, gtheme)
            await ctx.message.add_reaction("✓")
            return await ctx.send(embed=_ok_embed(f"**{slot}** renamed: `{old}` → `{new_name}`"))

        # interactive wizard: one slot at a time, skip/rename/done
        slots = list(role_map.keys())
        renamed: list[str] = []
        failures: list = []

        await self._ensure_snapshot(ctx.guild)
        gtheme = tm.get_guild_theme(ctx.guild.id) or tm.build_empty_theme()

        for i, s in enumerate(slots):
            role = ctx.guild.get_role(int(role_map[s]))
            rname = role.name if role else "deleted"
            warn = " ⚠️ (bot can't edit — move me above this role)" if (role and bot_member and role >= bot_member.top_role) else ""

            event = asyncio.Event()
            action: dict = {"type": None, "name": None}

            class SlotActionView(View):
                def __init__(self_v):
                    super().__init__(timeout=60)

                async def on_timeout(self_v):
                    action["type"] = "timeout"
                    event.set()

                @discord.ui.button(label="Rename", style=discord.ButtonStyle.gray)
                async def rename_btn(self_v, interaction: discord.Interaction, button: Button):
                    current_role = ctx.guild.get_role(int(role_map[s]))

                    class RenameModal(discord.ui.Modal, title=f"Rename: {s}"):
                        name_input = discord.ui.TextInput(
                            label="New role name",
                            default=current_role.name if current_role else "",
                            max_length=100,
                        )
                        async def on_submit(modal_self, inter: discord.Interaction):
                            action["type"] = "rename"
                            action["name"] = modal_self.name_input.value.strip()
                            await inter.response.edit_message(
                                content=f"-# ✅ **{s}** → `{action['name']}`",
                                view=None,
                            )
                            self_v.stop()
                            event.set()

                    await interaction.response.send_modal(RenameModal())

                @discord.ui.button(label="Skip", style=discord.ButtonStyle.gray)
                async def skip_btn(self_v, interaction: discord.Interaction, button: Button):
                    action["type"] = "skip"
                    await interaction.response.edit_message(
                        content=f"-# ⏭ skipped **{s}**",
                        view=None,
                    )
                    self_v.stop()
                    event.set()

                @discord.ui.button(emoji="<:7079verifiedblacksimplified:1255031445806780467>", style=discord.ButtonStyle.gray)
                async def done_btn(self_v, interaction: discord.Interaction, button: Button):
                    action["type"] = "done"
                    await interaction.response.edit_message(
                        content="-# stopping here.",
                        view=None,
                    )
                    self_v.stop()
                    event.set()

            view = SlotActionView()
            await ctx.send(
                f"-# **slot {i+1}/{len(slots)} — {s}**\n"
                f"-# current name: `{rname}`{warn}",
                view=view,
            )

            await event.wait()

            if action["type"] == "done" or action["type"] == "timeout":
                break

            if action["type"] == "rename" and action["name"]:
                if role and (not bot_member or role < bot_member.top_role):
                    old = role.name
                    try:
                        await self._rate_limit_for_guild(ctx.guild)
                        await role.edit(name=action["name"], reason=f"NeixO theme: renamed by {ctx.author}")
                        gtheme.setdefault("roles", {}).setdefault(s, {})["name"] = action["name"]
                        renamed.append(f"**{s}**: `{old}` → `{action['name']}`")
                    except discord.HTTPException as exc:
                        self._collect_failure(failures, "role", s, action.get("name",""), exc)
                elif role and bot_member and role >= bot_member.top_role:
                    await ctx.send(f"-# ⚠️ skipped **{s}** — move me above that role first")

        tm.save_guild_theme(ctx.guild.id, gtheme)

        if renamed:
            await ctx.message.add_reaction("✓")
            await ctx.send(embed=_ok_embed(f"renamed {len(renamed)} role(s):\n" + "\n".join(f"-# {r}" for r in renamed)))
        else:
            await ctx.send(embed=_err_embed("no roles renamed."))

        await self._report_failures(ctx, failures, "role rename failures")

    @theme.command(name="roleicon", aliases=["icon", "i"])
    @_is_theme_admin()
    @help_meta(
        usage="`.theme roleicon <slot|#index> [emoji|url|attachment]`",
        desc="Sets a role icon via emoji, URL, or image attachment. Supports slot number or name. Requires boost level 2.",
        section="Roles",
        perm_tier="admin",
        discord_perms=["manage_roles"],
        examples=[
            ".theme roleicon 1 👑",
            ".theme roleicon Owner 👑",
            ".theme icon 1 https://i.imgur.com/icon.png",
            ".tri 1 👑",
        ],
        params=[
            {"name": "slot", "type": "str", "required": True, "desc": "Slot number (1, 2...) or slot name."},
            {"name": "source", "type": "str", "required": False, "desc": "Emoji, image URL, or attachment. Omit to clear."},
        ],
        note="Requires server boost level 2 for role icons.",
    )
    async def theme_roleicon(self, ctx: commands.Context, *, args: str = None):
        """Set a role icon: .theme roleicon 1 👑"""
        if not args and not ctx.message.attachments and not ctx.message.reference:
            return await ctx.send("-# usage: `.theme roleicon <slot|#index> [emoji|url|attachment]`")

        role_map = tm.get_role_map(ctx.guild.id)
        if not role_map:
            return await ctx.send("-# no slots mapped yet — run `.theme setup`")

        slot, role, source = _resolve_role_slot(ctx.guild, role_map, args or "")
        if not slot:
            valid_slots = ", ".join(f"`{i}. {s}`" for i, s in enumerate(role_map.keys(), start=1))
            return await ctx.send(f"-# slot not found — see `.theme roles`\n-# valid slots: {valid_slots}")

        if not role:
            return await ctx.send(f"-# the role for slot `{slot}` no longer exists")

        if ctx.guild.premium_tier < 2:
            return await ctx.send("-# role icons require server boost level 2")

        await self._ensure_snapshot(ctx.guild)

        # try image bytes first (attachment / url download)
        icon_bytes: bytes | None = None
        icon_emoji: str | None = None

        try:
            icon_bytes = await _resolve_icon_bytes(ctx, source)
        except Exception as e:
            return await ctx.send(f"-# couldn't download image: {e}")

        if icon_bytes:
            try:
                await self._rate_limit_for_guild(ctx.guild)
                await role.edit(display_icon=icon_bytes, reason=f"NeixO theme icon: {ctx.author}")
                icon_store = "[image]"
            except discord.HTTPException as exc:
                return await ctx.send(embed=_err_embed(f"failed to set role icon: {exc}"))
        elif source:
            icon_emoji = source.strip()
            try:
                await self._rate_limit_for_guild(ctx.guild)
                await role.edit(display_icon=icon_emoji, reason=f"NeixO theme icon: {ctx.author}")
                icon_store = icon_emoji
            except discord.HTTPException as exc:
                return await ctx.send(embed=_err_embed(f"failed to set role icon: {exc}"))
        else:
            return await ctx.send("-# attach an image, reply to one, pass a URL, or pass a unicode emoji")

        gtheme = tm.get_guild_theme(ctx.guild.id) or tm.build_empty_theme()
        gtheme.setdefault("roles", {}).setdefault(slot, {})["icon"] = icon_store
        tm.save_guild_theme(ctx.guild.id, gtheme)

        await ctx.message.add_reaction("✓")
        await ctx.send(embed=_ok_embed(f"icon set for **{slot}** ({role.mention})"))

    @theme_role.command(name="revert", aliases=["reset", "rev"])
    @_is_theme_admin()
    @help_meta(
        usage="`.theme role revert <slot|#index>`",
        desc="Reverts one role back to its name from before the last theme apply.",
        section="Roles",
        perm_tier="admin",
        discord_perms=["manage_roles"],
        examples=[".theme role revert 1", ".theme role revert Owner", ".trevert 1"],
        params=[
            {"name": "slot", "type": "str", "required": True, "desc": "Slot number (1, 2...) or slot name to restore previous name for."},
        ],
        note="Only the most recent name change per role is stored.",
    )
    async def role_revert(self, ctx: commands.Context, *, slot_arg: str = None):
        """Revert one role slot to its snapshotted name."""
        if not slot_arg:
            return await ctx.send("-# usage: `.theme role revert <slot|#index>`")
        snap = tm.get_snapshot(ctx.guild.id)
        if not snap:
            return await ctx.send("-# no snapshot found — nothing to revert to")
        role_map = tm.get_role_map(ctx.guild.id)
        if not role_map:
            return await ctx.send("-# no slots mapped yet — run `.theme setup`")

        slot, role, _ = _resolve_role_slot(ctx.guild, role_map, slot_arg)
        if not slot:
            valid_slots = ", ".join(f"`{i}. {s}`" for i, s in enumerate(role_map.keys(), start=1))
            return await ctx.send(f"-# slot not found — see `.theme roles`\n-# valid slots: {valid_slots}")

        if not role:
            return await ctx.send(f"-# the role for slot `{slot}` no longer exists")
        saved = snap.get("roles", {}).get(str(role.id))
        if not saved:
            return await ctx.send(f"-# no snapshot data for `{slot}`")
        try:
            await self._rate_limit_for_guild(ctx.guild)
            await role.edit(name=saved["name"], reason="NeixO theme: resetrole")
            await ctx.message.add_reaction("✓")
            await ctx.send(embed=_ok_embed(f"**{slot}** reverted to `{saved['name']}`"))
        except discord.HTTPException as exc:
            return await ctx.send(embed=_err_embed(f"failed to revert role: {exc}"))

    # ══════════════════════════════════════════════════════════
    # CHANNEL PREFIX
    # ══════════════════════════════════════════════════════════

    @theme.group(name="prefix", aliases=["p"], invoke_without_command=True)
    @_is_theme_admin()
    @help_meta(
        usage="`.theme prefix [add|scan|remove|list]`",
        desc="Manages channel prefixes across categories.",
        section="Channels",
        perm_tier="admin",
        discord_perms=["manage_channels"],
        examples=[".theme prefix add ✦ #general", ".theme prefix scan all", ".tprefix ✦ #general"],
        params=[],
        note="Root command for all prefix subcommands: scan, add, remove, server, replace, undo, list.",
    )
    async def theme_prefix(self, ctx: commands.Context, *, args: str = None):
        if not args:
            return await ctx.send(
                "-# prefix subcommands: `scan`, `add`, `remove`, `list`\n"
                "-# e.g. `.theme prefix add ✦ #general` or `.tprefix ✦ #general`"
            )
        words = args.split(None, 1)
        emoji = words[0] if words else None
        cats = words[1] if len(words) > 1 else None
        await self.prefix_add(ctx, emoji=emoji, categories=cats)

    @theme_prefix.command(name="scan")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme prefix scan <#cat|all>`",
        desc="Detects existing emoji/symbol prefixes in a category or server-wide.",
        section="Channels",
        examples=[".theme prefix scan #general-category", ".theme prefix scan all"],
        params=[
            {"name": "target", "type": "str", "required": False, "desc": "Category mention to scan, or `all` to scan every channel."},
        ],
        note="The scan detects leading emojis/symbols in channel names and reports them.",
    )
    async def prefix_scan(self, ctx: commands.Context, *, args: str = None):
        """Scan a category or all categories for existing prefixes.
        .theme prefix scan #category
        .theme prefix scan all
        """
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        # handle "all" passed as the category arg (discord won't resolve "all" as a channel)
        is_all = args is not None and args.strip().lower() == "all"

        if is_all:
            # scan every category in the server
            all_found: dict[str, dict] = {}  # prefix → {cat_name: [ch_names]}
            for cat in ctx.guild.categories:
                for ch in cat.channels:
                    p = tm.detect_prefix(ch.name)
                    if p:
                        all_found.setdefault(p, {}).setdefault(cat.name, []).append(ch.name)

            if not all_found:
                self._last_scan.pop(ctx.guild.id, None)
                return await ctx.send("-# no prefixes detected anywhere in the server")

            # number them and save to memory for .theme prefix replace
            numbered = {i + 1: p for i, p in enumerate(all_found.keys())}
            tm.save_last_scan(ctx.guild.id, numbered)
            self._last_scan[ctx.guild.id] = numbered

            lines = []
            for num, p in numbered.items():
                cats = all_found[p]
                total_chs = sum(len(v) for v in cats.values())
                cat_list = ", ".join(f"**{cn}** ({len(chs)})" for cn, chs in cats.items())
                lines.append(f"`{num}.` `{p}` — {total_chs} channel(s) across {cat_list}")

            e = _embed(ctx, "prefix scan: entire server", "\n".join(lines))
            e.set_footer(text=f"{len(all_found)} unique prefix(es) found · use `.theme prefix replace <n> <new>` to swap one")
            return await ctx.send(embed=e)

        # resolve the category from the mention/id in args
        category = None
        if args:
            arg = args.strip()
            if arg.startswith("<#") and arg.endswith(">"):
                cid = int(arg[2:-1])
                category = ctx.guild.get_channel(cid)
            else:
                try:
                    category = ctx.guild.get_channel(int(arg))
                except ValueError:
                    category = discord.utils.get(ctx.guild.categories, name=arg)

        if not category:
            return await ctx.send(
                "-# usage: `.theme prefix scan <#category>` or `.theme prefix scan all`"
            )

        found: dict[str, list[str]] = {}
        for ch in category.channels:
            p = tm.detect_prefix(ch.name)
            if p:
                found.setdefault(p, []).append(ch.name)
        if not found:
            return await ctx.send(
                f"-# no prefixes detected in **{category.name}**\n"
                f"-# note: discord channel names only support unicode symbols/emoji as prefixes, separated by a hyphen (e.g. `🔥-general`)"
            )
        lines = [
            f"`{p}` — {len(chs)} channel(s): {', '.join(f'`{c}`' for c in chs[:3])}{'...' if len(chs) > 3 else ''}"
            for p, chs in found.items()
        ]
        e = _embed(ctx, f"prefix scan: {category.name}", "\n".join(lines))
        e.set_footer(text="format: <emoji>-<name> · use .theme prefix add <emoji> to apply")
        await ctx.send(embed=e)

    @theme_prefix.command(name="add")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme prefix add <emoji> <#cat> [...]`",
        desc="Adds or replaces a prefix emoji on all channels in one or more categories.",
        section="Channels",
        examples=[".theme prefix add ★ #text-channels", ".theme prefix add 🎮 #gaming #general"],
        params=[
            {"name": "emoji", "type": "str", "required": True, "desc": "The emoji or symbol to use as prefix."},
            {"name": "categories", "type": "discord.CategoryChannel", "required": True, "desc": "One or more category channels to apply the prefix to."},
        ],
        note="Existing prefixes on channels will be replaced.",
    )
    async def prefix_add(self, ctx: commands.Context, emoji: str = None, *categories: discord.CategoryChannel):
        """
        Add or replace a prefix emoji on all channels in one or more categories.
        .theme prefix add 🔥 #chat-category #gaming-category
        """
        if not emoji or not categories:
            return await ctx.send("-# usage: `.theme prefix add <emoji> <#cat> [<#cat2> ...]`")

        channels_to_edit = [ch for cat in categories for ch in cat.channels]
        if not channels_to_edit:
            return await ctx.send("-# no channels found in those categories")

        changes = [(ch.name, tm.apply_prefix(ch.name, emoji)) for ch in channels_to_edit]
        changes = [(b, a) for b, a in changes if b != a]
        if not changes:
            return await ctx.send("-# all channels already have that prefix")

        warn = f"-# ⚠️ `{emoji}` looks like a plain word — channels will look like `{emoji}general`\n" if tm.is_plain_word(emoji) else ""
        view = PreviewView(ctx.author.id, f"prefix add {emoji}", changes, warn=warn)
        preview_msg = await ctx.send(embed=view.build_embed(), view=view)
        await view.wait()

        if not view.confirmed:
            return await preview_msg.edit(embed=_err_embed("cancelled."), view=None)

        await self._ensure_snapshot(ctx.guild)
        history_snap = {str(ch.id): ch.name for ch in channels_to_edit}
        gtheme = tm.get_guild_theme(ctx.guild.id) or tm.build_empty_theme()

        # store prefix per category (unchanged behavior)
        for cat in categories:
            gtheme.setdefault("channel_prefix", {})[str(cat.id)] = emoji

        prog = await ctx.send(f"-# applying prefix `{emoji}` to {len(changes)} channels...")
        failures: list = []

        # preserve original "done" semantics: increments per edited channel iteration
        done = await self._bulk_rename_channels(
            ctx,
            channels_to_edit,
            new_name_for=lambda ch: tm.apply_prefix(ch.name, emoji),
            reason=f"NeixO theme prefix: {ctx.author}",
            prog=prog,
            failures=failures,
            progress_total=len(channels_to_edit),
            rate_limit_kind="channel",
        )

        tm.save_guild_theme(ctx.guild.id, gtheme)
        tm.save_prefix_history(ctx.guild.id, {"op": "prefix_add", "channels": history_snap})
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await prog.edit(content=f"-# `{'█' * 10}` done — prefix `{emoji}` applied to {done} channels across {len(categories)} categor(ies)")
        await self._report_failures(ctx, failures, "prefix add failures")

    @theme_prefix.command(name="remove")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme prefix remove <#cat> [...]`",
        desc="Strips prefixes from channels in specific categories, or from every channel.",
        section="Channels",
        examples=[".theme prefix remove #text-channels", ".theme prefix remove all"],
        params=[
            {"name": "categories", "type": "str", "required": False, "desc": "One or more category mentions, or `all` to strip server-wide."},
        ],
        note="Only removes leading emojis/symbols that were detected as prefixes.",
    )
    async def prefix_remove(self, ctx: commands.Context, *args):
        """Strip the stored prefix from all channels in one or more categories.
        Pass 'all' to remove from every channel in the server.
        .theme prefix remove #cat1 #cat2
        .theme prefix remove all
        """
        if not args:
            return await ctx.send(
                "-# usage: `.theme prefix remove <#cat> [<#cat2> ...]` or `.theme prefix remove all`"
            )

        use_all = len(args) == 1 and args[0].lower() == "all"
        if use_all:
            channels_to_edit = [ch for ch in ctx.guild.channels if not isinstance(ch, discord.CategoryChannel)]
        else:
            categories = []
            for arg in args:
                try:
                    if arg.startswith("<#") and arg.endswith(">"):
                        cid = int(arg[2:-1])
                    else:
                        cid = int(arg)
                    cat = ctx.guild.get_channel(cid)
                    if cat and isinstance(cat, discord.CategoryChannel):
                        categories.append(cat)
                except ValueError:
                    pass
            if not categories:
                return await ctx.send("-# couldn't resolve any of those categories")
            channels_to_edit = [ch for cat in categories for ch in cat.channels]

        if not channels_to_edit:
            return await ctx.send("-# no channels found")

        changes = [(ch.name, tm.remove_prefix_from_name(ch.name)) for ch in channels_to_edit]
        changes_filtered = [(b, a) for b, a in changes if b != a]
        if not changes_filtered:
            return await ctx.send("-# no prefixes found to remove")

        scope_label = "all channels" if use_all else f"{len(categories)} categor(ies)"
        view = PreviewView(ctx.author.id, f"prefix remove — {scope_label}", changes_filtered)
        preview_msg = await ctx.send(embed=view.build_embed(), view=view)
        await view.wait()

        if not view.confirmed:
            return await preview_msg.edit(embed=_err_embed("cancelled."), view=None)

        await self._ensure_snapshot(ctx.guild)
        history_snap = {str(ch.id): ch.name for ch in channels_to_edit}
        gtheme = tm.get_guild_theme(ctx.guild.id) or tm.build_empty_theme()
        prog = await ctx.send(f"-# removing prefixes from {len(changes_filtered)} channels...")
        done = 0
        failures: list = []

        for ch in channels_to_edit:
            new_name = tm.remove_prefix_from_name(ch.name)
            if new_name != ch.name:
                try:
                    await asyncio.wait_for(
                        ch.edit(name=new_name, reason=f"NeixO theme prefix remove: {ctx.author}"),
                        timeout=10.0,
                    )
                except (discord.HTTPException, asyncio.TimeoutError) as exc:
                    self._collect_failure(failures, "channel", ch.id, ch.name, exc)
                await self._rate_limit_for_channel(ch)
            done += 1
            await _edit_progress(prog, done, len(channels_to_edit), ch.name)

        if use_all:
            gtheme["channel_prefix"] = {}
        else:
            for cat in categories:
                gtheme.get("channel_prefix", {}).pop(str(cat.id), None)

        tm.save_guild_theme(ctx.guild.id, gtheme)
        tm.save_prefix_history(ctx.guild.id, {"op": "prefix_remove", "channels": history_snap})
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await prog.edit(content=f"-# `{'█' * 10}` done — prefixes removed from {done} channels")
        await self._report_failures(ctx, failures, "prefix remove failures")

    @theme_prefix.command(name="server")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme prefix server <emoji>`",
        desc="Applies a prefix to every channel in the server, or removes all prefixes server-wide.",
        section="Channels",
        examples=[".theme prefix server ★", ".theme prefix server"],
        params=[
            {"name": "emoji", "type": "str", "required": False, "desc": "Emoji to apply server-wide. Omit to remove all prefixes."},
        ],
        note="This overrides any existing prefixes on all channels.",
    )
    async def prefix_server(self, ctx: commands.Context, emoji: str = None):
        """Set a universal prefix on every channel in the server across all categories.
        .theme prefix server 🔥
        .theme prefix server remove  — strips prefix from everything
        """
        if not emoji:
            return await ctx.send("-# usage: `.theme prefix server <emoji>` or `.theme prefix server remove`")

        removing = emoji.strip().lower() == "remove"
        all_channels = [ch for ch in ctx.guild.channels if not isinstance(ch, discord.CategoryChannel)]

        if removing:
            changes = [(ch.name, tm.remove_prefix_from_name(ch.name)) for ch in all_channels]
        else:
            changes = [(ch.name, tm.apply_prefix(ch.name, emoji)) for ch in all_channels]

        changes_filtered = [(b, a) for b, a in changes if b != a]
        if not changes_filtered:
            return await ctx.send("-# no changes needed — channels already look correct")

        warn = ""
        if not removing and tm.is_plain_word(emoji):
            warn = f"-# ⚠️ `{emoji}` looks like a plain word — channels will look like `{emoji}general`\n"

        scope_label = "remove all prefixes" if removing else f"prefix all — {emoji}"
        view = PreviewView(ctx.author.id, scope_label, changes_filtered, warn=warn)
        preview_msg = await ctx.send(embed=view.build_embed(), view=view)
        await view.wait()

        if not view.confirmed:
            return await preview_msg.edit(embed=_err_embed("cancelled."), view=None)

        await self._ensure_snapshot(ctx.guild)
        history_snap = {str(ch.id): ch.name for ch in all_channels}
        gtheme = tm.get_guild_theme(ctx.guild.id) or tm.build_empty_theme()
        prog = await ctx.send(
            f"-# {'removing prefixes from' if removing else f'applying prefix `{emoji}` to'} {len(changes_filtered)} channels..."
        )
        done = 0
        failures: list = []

        for ch in all_channels:
            new_name = tm.remove_prefix_from_name(ch.name) if removing else tm.apply_prefix(ch.name, emoji)
            if new_name != ch.name:
                try:
                    await asyncio.wait_for(
                        ch.edit(name=new_name, reason=f"NeixO theme prefix all: {ctx.author}"),
                        timeout=10.0,
                    )
                except (discord.HTTPException, asyncio.TimeoutError) as exc:
                    self._collect_failure(failures, "channel", ch.id, ch.name, exc)
            done += 1
            await _edit_progress(prog, done, len(all_channels), ch.name)
            if ch:
                await self._rate_limit_for_channel(ch)

        if removing:
            gtheme["channel_prefix"] = {}
        else:
            for cat in ctx.guild.categories:
                gtheme.setdefault("channel_prefix", {})[str(cat.id)] = emoji
            gtheme["universal_prefix"] = emoji

        tm.save_guild_theme(ctx.guild.id, gtheme)
        tm.save_prefix_history(ctx.guild.id, {"op": "prefix_server", "channels": history_snap})

        bar = "█" * 10
        action_str = "prefixes removed from" if removing else f"prefix `{emoji}` applied to"
        result = f"-# `{bar}` done — {action_str} {done} channels"
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await prog.edit(content=result)
        await self._report_failures(ctx, failures, "prefix server failures")

    @theme_prefix.command(name="replace")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme prefix replace <n> <new>`",
        desc="Replaces a detected prefix with a new one across the server.",
        section="Channels",
        examples=[".theme prefix replace 3 ★", ".theme prefix replace 1 🎮"],
        params=[
            {"name": "n", "type": "int", "required": True, "desc": "The index of the prefix to replace (from scan results)."},
            {"name": "new", "type": "str", "required": True, "desc": "The new prefix emoji or symbol."},
        ],
        note="Use `.theme prefix scan` first to see detected prefixes and their indices.",
    )
    async def prefix_replace(self, ctx: commands.Context, number: int = None, *, new_prefix: str = None):
        """Replace a detected prefix with a new one across the whole server.
        Run .theme prefix scan all first to get the numbers.
        .theme prefix replace 1 ⭐
        """
        if number is None or not new_prefix:
            return await ctx.send("-# usage: `.theme prefix replace <number> <new prefix>` — run `.theme prefix scan all` first")

        scan = self._last_scan.get(ctx.guild.id) or tm.get_last_scan(ctx.guild.id)
        if not scan:
            return await ctx.send("-# no scan found — run `.theme prefix scan all` first")

        old_prefix = scan.get(number)
        if not old_prefix:
            valid = ", ".join(f"`{n}`" for n in scan)
            return await ctx.send(f"-# number `{number}` not in last scan — valid options: {valid}")

        new_prefix = new_prefix.strip()

        # find every channel that currently has the old prefix
        all_channels = [ch for ch in ctx.guild.channels if not isinstance(ch, discord.CategoryChannel)]
        changes = []
        for ch in all_channels:
            detected = tm.detect_prefix(ch.name)
            if detected == old_prefix:
                rest = ch.name[len(old_prefix):].lstrip("-").strip()
                new_name = tm.apply_prefix(rest, new_prefix)
                if new_name != ch.name:
                    changes.append((ch.name, new_name))

        if not changes:
            return await ctx.send(f"-# no channels found with prefix `{old_prefix}`")

        warn = f"-# ⚠️ `{new_prefix}` looks like a plain word — channels will look like `{new_prefix}general`\n" if tm.is_plain_word(new_prefix) else ""
        view = PreviewView(ctx.author.id, f"replace `{old_prefix}` → `{new_prefix}`", changes, warn=warn)
        preview_msg = await ctx.send(embed=view.build_embed(), view=view)
        await view.wait()

        if not view.confirmed:
            return await preview_msg.edit(embed=_err_embed("cancelled."), view=None)

        await self._ensure_snapshot(ctx.guild)
        history_snap = {str(ch.id): ch.name for ch in all_channels}
        prog = await ctx.send(f"-# replacing `{old_prefix}` → `{new_prefix}` on {len(changes)} channels...")
        done = 0
        failures: list = []

        for ch in all_channels:
            detected = tm.detect_prefix(ch.name)
            if detected == old_prefix:
                rest = ch.name[len(old_prefix):].lstrip("-").strip()
                new_name = tm.apply_prefix(rest, new_prefix)
                if new_name != ch.name:
                    try:
                        await asyncio.wait_for(
                            ch.edit(name=new_name, reason=f"NeixO theme prefix replace: {ctx.author}"),
                            timeout=10.0,
                        )
                    except (discord.HTTPException, asyncio.TimeoutError) as exc:
                        self._collect_failure(failures, "channel", ch.id, ch.name, exc)
            done += 1
            await _edit_progress(prog, done, len(all_channels), ch.name)
            await self._rate_limit_for_channel(ch)

        # update stored prefixes in gtheme for any categories affected
        gtheme = tm.get_guild_theme(ctx.guild.id) or tm.build_empty_theme()
        for cat_id, stored_prefix in list(gtheme.get("channel_prefix", {}).items()):
            if stored_prefix == old_prefix:
                gtheme["channel_prefix"][cat_id] = new_prefix
        tm.save_guild_theme(ctx.guild.id, gtheme)
        tm.save_prefix_history(ctx.guild.id, {"op": f"prefix_replace:{old_prefix}→{new_prefix}", "channels": history_snap})

        # update the in-memory scan so numbers stay accurate and persist it
        new_scan = {
            n: (new_prefix if p == old_prefix else p)
            for n, p in scan.items()
        }
        self._last_scan[ctx.guild.id] = new_scan
        tm.save_last_scan(ctx.guild.id, new_scan)

        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await prog.edit(content=f"-# `{'█' * 10}` done — replaced `{old_prefix}` → `{new_prefix}` on {len(changes)} channels")
        await self._report_failures(ctx, failures, "prefix replace failures")

    @theme_prefix.command(name="undo")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme prefix undo`",
        desc="Undoes the last prefix operation (add, remove, or all).",
        section="Channels",
        examples=[".theme prefix undo"],
        params=[],
        note="Only the most recent prefix change can be undone.",
    )
    async def prefix_undo(self, ctx: commands.Context):
        """Undo the last prefix operation (add, remove, or all)."""
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        hist = tm.get_prefix_history(ctx.guild.id)
        if not hist:
            return await ctx.send("-# no prefix history found — nothing to undo")

        channels_snap = hist.get("channels", {})
        op = hist.get("op", "unknown")

        # build changes preview
        changes = []
        for ch_id_str, old_name in channels_snap.items():
            ch = ctx.guild.get_channel(int(ch_id_str))
            if ch and ch.name != old_name:
                changes.append((ch.name, old_name))

        if not changes:
            return await ctx.send("-# nothing to undo — channels already match the saved state")

        view = PreviewView(ctx.author.id, f"undo: {op}", changes)
        preview_msg = await ctx.send(embed=view.build_embed(), view=view)
        await view.wait()

        if not view.confirmed:
            return await preview_msg.edit(embed=_err_embed("cancelled."), view=None)

        prog = await ctx.send(f"-# undoing {op} on {len(changes)} channels...")
        done = 0
        failures: list = []

        for ch_id_str, old_name in channels_snap.items():
            ch = ctx.guild.get_channel(int(ch_id_str))
            if ch and ch.name != old_name:
                try:
                    await asyncio.wait_for(
                        ch.edit(name=old_name, reason=f"NeixO theme prefix undo: {ctx.author}"),
                        timeout=10.0,
                    )
                except (discord.HTTPException, asyncio.TimeoutError) as exc:
                    self._collect_failure(failures, "channel", ch_id_str, old_name, exc)
            done += 1
            await _edit_progress(prog, done, len(channels_snap), ch.name if ch else ch_id_str)
            if ch:
                await self._rate_limit_for_channel(ch)

        # rebuild stored channel_prefix by scanning categories for current prefixes
        gtheme = tm.get_guild_theme(ctx.guild.id) or tm.build_empty_theme()
        new_prefixes = {}
        for cat in ctx.guild.categories:
            for ch in cat.channels:
                p = tm.detect_prefix(ch.name)
                if p:
                    new_prefixes[str(cat.id)] = p
                    break
        gtheme["channel_prefix"] = new_prefixes
        tm.save_guild_theme(ctx.guild.id, gtheme)

        tm.clear_prefix_history(ctx.guild.id)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await prog.edit(content=f"-# `{'█' * 10}` done — {op} undone across {done} channels")
        await self._report_failures(ctx, failures, "prefix undo failures")

    @theme_prefix.command(name="list")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme prefix list`",
        desc="Shows all stored channel prefixes for this server.",
        section="Channels",
        examples=[".theme prefix list"],
        params=[],
        note="Displays each channel and its current prefix.",
    )
    async def prefix_list(self, ctx: commands.Context):
        """List all stored channel prefixes for this server."""
        gtheme = tm.get_guild_theme(ctx.guild.id) or {}
        prefixes = gtheme.get("channel_prefix", {})
        if not prefixes:
            return await ctx.send("-# no prefixes stored — use `.theme prefix add`")
        lines = []
        for cat_id, emoji in prefixes.items():
            cat = ctx.guild.get_channel(int(cat_id))
            lines.append(f"`{emoji}` → **{cat.name if cat else f'deleted ({cat_id})'}**")
        await ctx.send(embed=_embed(ctx, "channel prefixes", "\n".join(lines)))

    # ══════════════════════════════════════════════════════════
    # ROLEFIX — strip text from channel names
    # ══════════════════════════════════════════════════════════

    @theme.group(name="channel", invoke_without_command=True)
    @_is_theme_admin()
    @help_meta(
        usage="`.theme channel`",
        desc="Manages channel names. Subcommand: `strip`.",
        section="Channels",
        examples=[".theme channel", ".theme channel strip -"],
        params=[],
        note="Root command for channel name subcommands.",
    )
    async def theme_channel(self, ctx: commands.Context):
        await ctx.send("-# channel subcommands: `strip`")

    @theme_channel.command(name="strip")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme channel strip <text>`",
        desc="Strips an exact piece of text from every channel name in the server.",
        section="Channels",
        examples=[".theme channel strip -", ".theme channel strip oldtext"],
        params=[
            {"name": "text", "type": "str", "required": True, "desc": "The exact text to remove from all channel names."},
        ],
        note="Case-sensitive. Only exact matches are removed.",
    )
    async def theme_channel_strip(self, ctx: commands.Context, *, text: str = None):
        """Strip an exact piece of text from every channel name in the server.
        .theme channel strip scan
        .theme channel strip pfp
        """
        if not text:
            return await ctx.send("-# usage: `.theme channel strip <text to remove>`")

        text = text.strip()
        all_channels = [ch for ch in ctx.guild.channels if not isinstance(ch, discord.CategoryChannel)]
        changes = []
        for ch in all_channels:
            new_name = ch.name.replace(text, "").strip("-").strip()
            if new_name and new_name != ch.name:
                changes.append((ch.name, new_name))

        if not changes:
            return await ctx.send(f"-# `{text}` not found in any channel names")

        view = PreviewView(ctx.author.id, f"channel strip: remove `{text}`", changes)
        preview_msg = await ctx.send(embed=view.build_embed(), view=view)
        await view.wait()

        if not view.confirmed:
            return await preview_msg.edit(embed=_err_embed("cancelled."), view=None)

        await self._ensure_snapshot(ctx.guild)
        history_snap = {str(ch.id): ch.name for ch in all_channels}
        prog = await ctx.send(f"-# stripping `{text}` from {len(changes)} channels...")
        done = 0
        failures: list = []

        for ch in all_channels:
            new_name = ch.name.replace(text, "").strip("-").strip()
            if new_name and new_name != ch.name:
                try:
                    await asyncio.wait_for(
                        ch.edit(name=new_name, reason=f"NeixO channel strip: {ctx.author}"),
                        timeout=10.0,
                    )
                except (discord.HTTPException, asyncio.TimeoutError) as exc:
                    self._collect_failure(failures, "channel", ch.id, ch.name, exc)
            done += 1
            await _edit_progress(prog, done, len(all_channels), ch.name)
            await self._rate_limit_for_channel(ch)

        tm.save_prefix_history(ctx.guild.id, {"op": f"channel_strip:{text}", "channels": history_snap})
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await prog.edit(content=f"-# `{'█' * 10}` done — stripped `{text}` from {len(changes)} channels")
        await self._report_failures(ctx, failures, "channel strip failures")

    # ══════════════════════════════════════════════════════════
    # CHANNEL FONT
    # ══════════════════════════════════════════════════════════

    @theme.group(name="font", aliases=["f"], invoke_without_command=True)
    @_is_theme_admin()
    @help_meta(
        usage="`.theme font [font_name|list|set|reset]`",
        desc="Manages channel fonts — list, set, reset.",
        section="Channels",
        perm_tier="admin",
        discord_perms=["manage_channels"],
        examples=[".theme font list", ".theme font bold all", ".tfont bold all"],
        params=[],
        note="Root command for unicode channel font transformations.",
    )
    async def theme_font(self, ctx: commands.Context, *, args: str = None):
        if not args or args.strip().lower() == "list":
            return await ctx.invoke(self.font_list)
        words = args.split(None, 1)
        font_key = words[0] if words else None
        target = words[1] if len(words) > 1 else None
        if font_key:
            target_list = target.split() if target else []
            await self.font_set(ctx, font_key, *target_list)

    @theme_font.command(name="list")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme font list`",
        desc="Shows all available unicode font styles with live examples.",
        section="Channels",
        examples=[".theme font list"],
        params=[],
        note="Each font style is displayed with a sample of how it transforms text.",
    )
    async def font_list(self, ctx: commands.Context):
        """Show all available unicode font styles with live examples."""
        lines = []
        for key, info in tm.UNICODE_FONTS.items():
            lines.append(f"`{key}` — **{info['label']}** — {info['example']}")
        e = _embed(ctx, "available channel fonts", "\n".join(lines))
        e.set_footer(text="usage: .theme font set <key> [all | #cat1 #cat2 ...]")
        await ctx.send(embed=e)

    @theme_font.command(name="set")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme font set <font> [all|<#cat> ...]`",
        desc="Applies a unicode font to channel names.",
        section="Channels",
        examples=[".theme font set cursive #general", ".theme font set bold all"],
        params=[
            {"name": "font", "type": "str", "required": True, "desc": "Font style name (from `.theme font list`)."},
            {"name": "target", "type": "str", "required": False, "desc": "`all` for server-wide, or one or more category mentions."},
        ],
        note="Only visible to Discord clients that support unicode font rendering.",
    )
    async def font_set(self, ctx: commands.Context, font_key: str = None, *args):
        """
        Apply a unicode font style to channel names.
        Scope: all (all channels), or one/more category mentions.
        .theme font set bold all
        .theme font set script #chat #info
        """
        if not font_key or font_key not in tm.UNICODE_FONTS:
            keys = ", ".join(f"`{k}`" for k in tm.UNICODE_FONTS)
            return await ctx.send(f"-# unknown font. available: {keys}")

        # parse scope
        if not args:
            return await ctx.send(
                "-# specify scope: `.theme font set <font> all` or `.theme font set <font> #cat1 #cat2`"
            )

        scope_cats: list[discord.CategoryChannel] = []
        use_all = False

        for arg in args:
            if arg.lower() == "all":
                use_all = True
                break
            # try to resolve as category channel mention or id
            cat = None
            # strip <#id> format
            if arg.startswith("<#") and arg.endswith(">"):
                cid = int(arg[2:-1])
                cat = ctx.guild.get_channel(cid)
            else:
                try:
                    cat = ctx.guild.get_channel(int(arg))
                except ValueError:
                    # try by name
                    cat = discord.utils.get(ctx.guild.categories, name=arg)
            if cat and isinstance(cat, discord.CategoryChannel):
                scope_cats.append(cat)

        if not use_all and not scope_cats:
            return await ctx.send("-# couldn't find any of those categories")

        channels_to_edit = (
            [ch for ch in ctx.guild.channels if not isinstance(ch, discord.CategoryChannel)]
            if use_all else
            [ch for cat in scope_cats for ch in cat.channels]
        )

        if not channels_to_edit:
            return await ctx.send("-# no channels to edit in that scope")

        # show before→after preview
        changes = [(ch.name, tm.convert_font(tm.strip_font(ch.name), font_key)) for ch in channels_to_edit]
        changes_filtered = [(b, a) for b, a in changes if b != a]
        if not changes_filtered:
            return await ctx.send("-# no font changes needed")
        view = PreviewView(ctx.author.id, f"font set {font_key}", changes_filtered)
        preview_msg = await ctx.send(embed=view.build_embed(), view=view)
        await view.wait()

        if not view.confirmed:
            return await preview_msg.edit(embed=_err_embed("cancelled."), view=None)

        await self._ensure_snapshot(ctx.guild)
        prog = await ctx.send(f"-# applying `{font_key}` font to {len(channels_to_edit)} channels...")
        done = 0
        failures: list = []

        for ch in channels_to_edit:
            plain_name = tm.strip_font(ch.name)          # strip any previous font first
            new_name = tm.convert_font(plain_name, font_key)
            if new_name != ch.name:
                try:
                    await asyncio.wait_for(
                        ch.edit(name=new_name, reason=f"NeixO theme font: {ctx.author}"),
                        timeout=10.0,
                    )
                except (discord.HTTPException, asyncio.TimeoutError) as exc:
                    self._collect_failure(failures, "channel", ch.id, ch.name, exc)
                await self._rate_limit_for_channel(ch)
            done += 1
            await _edit_progress(prog, done, len(channels_to_edit), ch.name)

        # save to theme
        gtheme = tm.get_guild_theme(ctx.guild.id) or tm.build_empty_theme()
        gtheme["channel_style"] = {
            "font": font_key,
            "scope": "all" if use_all else [str(cat.id) for cat in scope_cats],
        }
        tm.save_guild_theme(ctx.guild.id, gtheme)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await prog.edit(content=f"-# `{'█' * 10}` done — `{font_key}` font applied to {done} channels")
        await self._report_failures(ctx, failures, "font set failures")

    @theme_font.command(name="reset")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme font reset [all|<#cat>]`",
        desc="Strips unicode font styling from channel names.",
        section="Channels",
        examples=[".theme font reset all", ".theme font reset #general"],
        params=[
            {"name": "target", "type": "str", "required": False, "desc": "`all` for server-wide, or a category mention."},
        ],
        note="Restores the original (pre-font) channel names.",
    )
    async def font_reset(self, ctx: commands.Context, *args):
        """
        Strip unicode font from channel names.
        .theme font reset all
        .theme font reset #category
        """
        if not args:
            return await ctx.send("-# usage: `.theme font reset [all | #cat1 #cat2 ...]`")

        scope_cats: list[discord.CategoryChannel] = []
        use_all = args[0].lower() == "all"

        if not use_all:
            for arg in args:
                if arg.startswith("<#") and arg.endswith(">"):
                    cid = int(arg[2:-1])
                    cat = ctx.guild.get_channel(cid)
                    if cat and isinstance(cat, discord.CategoryChannel):
                        scope_cats.append(cat)

        channels_to_edit = (
            [ch for ch in ctx.guild.channels if not isinstance(ch, discord.CategoryChannel)]
            if use_all else
            [ch for cat in scope_cats for ch in cat.channels]
        )

        if not channels_to_edit:
            return await ctx.send("-# no channels found in that scope")

        # preview before applying
        changes = [(ch.name, tm.strip_font(ch.name)) for ch in channels_to_edit]
        changes_filtered = [(b, a) for b, a in changes if b != a]
        if not changes_filtered:
            return await ctx.send("-# no font changes needed")
        view = PreviewView(ctx.author.id, "font reset", changes_filtered)
        preview_msg = await ctx.send(embed=view.build_embed(), view=view)
        await view.wait()

        if not view.confirmed:
            return await preview_msg.edit(embed=_err_embed("cancelled."), view=None)

        await self._ensure_snapshot(ctx.guild)
        prog = await ctx.send(f"-# stripping font from {len(channels_to_edit)} channels...")
        done = 0

        failures: list = []
        for ch in channels_to_edit:
            new_name = tm.strip_font(ch.name)
            if new_name != ch.name:
                try:
                    await asyncio.wait_for(
                        ch.edit(name=new_name, reason=f"NeixO theme font reset: {ctx.author}"),
                        timeout=10.0,
                    )
                except (discord.HTTPException, asyncio.TimeoutError) as exc:
                    self._collect_failure(failures, "channel", ch.id, ch.name, exc)
                await self._rate_limit_for_channel(ch)
            done += 1
            await _edit_progress(prog, done, len(channels_to_edit), ch.name)

        gtheme = tm.get_guild_theme(ctx.guild.id) or {}
        gtheme.pop("channel_style", None)
        tm.save_guild_theme(ctx.guild.id, gtheme)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await prog.edit(content=f"-# `{'█' * 10}` done — font stripped from {done} channels")
        await self._report_failures(ctx, failures, "font reset failures")

    # ══════════════════════════════════════════════════════════
    # PRESETS — save / apply / delete
    # ══════════════════════════════════════════════════════════

    @theme.command(name="save")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme save <name>`",
        desc="Saves the current server role and channel state as a named preset.",
        section="Presets",
        examples=[".theme save mytheme", ".theme save backup1"],
        params=[
            {"name": "name", "type": "str", "required": True, "desc": "Name for the preset."},
        ],
        note="Presets include role mappings, channel names, prefixes, and fonts.",
    )
    async def theme_save(self, ctx: commands.Context, *, name: str = None):
        """Save the current server state as a named preset."""
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        if not name:
            return await ctx.send("-# usage: `.theme save <preset name>`")
        # build a snapshot of current role names + current theme config
        gid = ctx.guild.id
        role_map = tm.get_role_map(gid)
        roles_state: dict = {}
        for slot, rid in role_map.items():
            role = ctx.guild.get_role(int(rid))
            if role:
                entry: dict = {"name": role.name}
                # capture role colour
                try:
                    entry["color"] = role.colour.value
                except Exception:
                    # fallback for older discord.py versions
                    entry["color"] = getattr(role, "color", None) and getattr(role.color, "value", None)
                # capture role icon if available
                if getattr(role, "icon", None):
                    try:
                        raw = await role.icon.read()
                        entry["icon"] = base64.b64encode(raw).decode("ascii")
                    except Exception:
                        entry["icon"] = None
                roles_state[slot] = entry

        # ensure we merge with any existing saved theme roles
        gtheme = tm.get_guild_theme(gid) or tm.build_empty_theme(name)
        merged_roles = dict(gtheme.get("roles", {}))
        for s, info in roles_state.items():
            merged_roles.setdefault(s, {})
            merged_roles[s].update(info)

        # capture guild icon/banner (base64) if present
        guild_media: dict = {}
        try:
            if ctx.guild.icon:
                raw = await ctx.guild.icon.read()
                guild_media["icon"] = base64.b64encode(raw).decode("ascii")
        except Exception:
            guild_media["icon"] = None
        try:
            if ctx.guild.banner:
                raw = await ctx.guild.banner.read()
                guild_media["banner"] = base64.b64encode(raw).decode("ascii")
        except Exception:
            guild_media["banner"] = None

        preset = {
            "name": name,
            "roles": merged_roles,
            "channel_prefix": gtheme.get("channel_prefix", {}),
            "channel_style": gtheme.get("channel_style", {}),
            "guild": guild_media,
        }
        tm.save_preset(name, preset)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await ctx.send(embed=_ok_embed(f"preset **{name}** saved"))

    @theme.command(name="apply")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme apply <name>`",
        desc="Previews then applies a saved preset to this server.",
        section="Presets",
        examples=[".theme apply mytheme"],
        params=[
            {"name": "name", "type": "str", "required": True, "desc": "Name of the preset to apply."},
        ],
        note="A snapshot is created before applying so you can revert with `.theme reset`.",
    )
    async def theme_apply(self, ctx: commands.Context, *, name: str = None):
        """Preview and apply a saved preset to this server."""
        if not name:
            return await ctx.send("-# usage: `.theme apply <preset name>`")
        preset = tm.get_preset(name)
        if not preset:
            return await ctx.send(f"-# preset `{name}` not found — see `.theme presets`")

        role_map = tm.get_role_map(ctx.guild.id)
        if not role_map:
            return await ctx.send("-# no role slots mapped — run `.theme setup` first")

        # ── Build preview ──────────────────────────────────
        preview_lines: list[str] = []

        for slot, data in preset.get("roles", {}).items():
            if slot not in role_map:
                continue
            role = ctx.guild.get_role(int(role_map[slot]))
            if not role:
                continue
            new_name = data.get("name", role.name)
            if new_name != role.name:
                preview_lines.append(f"🏷️ **{slot}**: `{role.name}` → `{new_name}`")

        for cat_id, emoji in preset.get("channel_prefix", {}).items():
            cat = ctx.guild.get_channel(int(cat_id))
            if cat:
                preview_lines.append(f"🔖 prefix `{emoji}` on **{cat.name}**")

        if preset.get("channel_style"):
            cs = preset["channel_style"]
            preview_lines.append(f"✒️ font `{cs.get('font')}` on {cs.get('scope','?')}")

        if not preview_lines:
            preview_lines.append("*(no changes detected)*")

        e = _embed(ctx, f"preview: {name}")
        e.description = "\n".join(preview_lines)
        e.set_footer(text="click Apply to confirm, or Cancel to abort")

        view = ConfirmView(ctx.author.id)
        confirm_msg = await ctx.send(embed=e, view=view)
        await view.wait()

        if not view.confirmed:
            return await confirm_msg.edit(embed=_err_embed("cancelled."), view=None)

        # ── Apply ──────────────────────────────────────────
        await self._ensure_snapshot(ctx.guild)

        # count explicit work units: roles + channels affected by prefix changes + channels affected by font changes
        total_ops = 0
        # roles
        total_ops += len(preset.get("roles", {}))
        # channel prefix target channels
        for cid in preset.get("channel_prefix", {}):
            cat = ctx.guild.get_channel(int(cid))
            if cat and isinstance(cat, discord.CategoryChannel):
                total_ops += len(cat.channels)
        # channel style targets (fonts)
        cs = preset.get("channel_style")
        if cs:
            scope = cs.get("scope")
            if scope == "all":
                total_ops += len([ch for ch in ctx.guild.channels if not isinstance(ch, discord.CategoryChannel)])
            else:
                for cid in (scope or []):
                    try:
                        cat = ctx.guild.get_channel(int(cid))
                    except Exception:
                        cat = None
                    if cat and isinstance(cat, discord.CategoryChannel):
                        total_ops += len(cat.channels)
        if total_ops == 0:
            total_ops = 1
        prog = await ctx.send(f"-# applying preset **{name}**...")
        done = 0
        failures: list = []

        # roles (apply name, color, icon if provided)
        for slot, data in preset.get("roles", {}).items():
            if slot not in role_map:
                continue
            role = ctx.guild.get_role(int(role_map[slot]))
            if not role:
                continue
            kwargs = {}
            new_name = data.get("name", role.name)
            if new_name != role.name:
                kwargs["name"] = new_name
            # color
            if data.get("color") is not None:
                try:
                    kwargs["colour"] = discord.Colour(int(data.get("color")))
                except Exception:
                    pass
            # role icon (base64)
            if data.get("icon"):
                try:
                    raw = base64.b64decode(data.get("icon"))
                    kwargs["display_icon"] = raw
                except Exception:
                    pass

            try:
                if kwargs:
                    await role.edit(reason=f"NeixO theme apply: {name}", **kwargs)
                done += 1
                await _edit_progress(prog, done, max(total_ops, 1), f"role: {new_name}")
                await self._rate_limit_for_guild(ctx.guild)
            except discord.HTTPException as exc:
                self._collect_failure(failures, "role", slot, new_name, exc)

        # apply guild icon/banner if present in preset
        guild_media = preset.get("guild") or {}
        if guild_media:
            g_kwargs = {}
            if guild_media.get("icon"):
                try:
                    g_kwargs["icon"] = base64.b64decode(guild_media.get("icon"))
                except Exception:
                    pass
            if guild_media.get("banner"):
                try:
                    g_kwargs["banner"] = base64.b64decode(guild_media.get("banner"))
                except Exception:
                    pass
            if g_kwargs:
                try:
                    await ctx.guild.edit(reason=f"NeixO theme apply: {name}", **g_kwargs)
                except discord.HTTPException as exc:
                    self._collect_failure(failures, "guild", ctx.guild.id, ctx.guild.name, exc)

        # channel prefixes
        for cat_id, emoji in preset.get("channel_prefix", {}).items():
            cat = ctx.guild.get_channel(int(cat_id))
            if not cat or not isinstance(cat, discord.CategoryChannel):
                continue
            for ch in cat.channels:
                new_name = tm.apply_prefix(ch.name, emoji)
                if new_name != ch.name:
                    try:
                        await ch.edit(name=new_name, reason=f"NeixO theme apply: {name}")
                    except discord.HTTPException as exc:
                        self._collect_failure(failures, "channel", ch.id, ch.name, exc)
                done += 1
                await _edit_progress(prog, done, max(total_ops, 1), ch.name)
                await self._rate_limit_for_channel(ch)

        # apply channel_style (fonts) if present
        cs = preset.get("channel_style")
        if cs:
            font_key = cs.get("font")
            scope = cs.get("scope")
            target_channels = []
            if scope == "all":
                target_channels = [ch for ch in ctx.guild.channels if not isinstance(ch, discord.CategoryChannel)]
            else:
                for cid in (scope or []):
                    try:
                        cat = ctx.guild.get_channel(int(cid))
                    except Exception:
                        cat = None
                    if cat and isinstance(cat, discord.CategoryChannel):
                        target_channels.extend(cat.channels)
            for ch in target_channels:
                plain = tm.strip_font(ch.name)
                new_name = tm.convert_font(plain, font_key)
                if new_name != ch.name:
                    try:
                        await ch.edit(name=new_name, reason=f"NeixO theme apply: {name}")
                    except discord.HTTPException as exc:
                        self._collect_failure(failures, "channel", ch.id, ch.name, exc)
                done += 1
                await _edit_progress(prog, done, max(total_ops, 1), ch.name)
                await self._rate_limit_for_channel(ch)

        tm.save_guild_theme(ctx.guild.id, preset)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await prog.edit(content=f"-# `{'█' * 10}` done — preset **{name}** applied — {done} changes made")
        await self._report_failures(ctx, failures, f"preset apply failures: {name}")

    @theme.command(name="presets")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme presets`",
        desc="Lists all saved presets for this server.",
        section="Presets",
        examples=[".theme presets"],
        params=[],
        note="Shows preset names and creation dates.",
    )
    async def theme_presets(self, ctx: commands.Context):
        """List all saved presets."""
        names = tm.list_presets()
        if not names:
            return await ctx.send("-# no presets saved yet — use `.theme save <name>`")
        e = _embed(ctx, "saved presets", "\n".join(f"• `{n}`" for n in names))
        e.set_footer(text=".theme apply <name> to use one")
        await ctx.send(embed=e)

    @theme.command(name="delete")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme delete <name>`",
        desc="Deletes a saved preset.",
        section="Presets",
        examples=[".theme delete oldtheme"],
        params=[
            {"name": "name", "type": "str", "required": True, "desc": "Name of the preset to delete."},
        ],
        note="This cannot be undone.",
    )
    async def theme_delete(self, ctx: commands.Context, *, name: str = None):
        """Delete a saved preset."""
        if not name:
            return await ctx.send("-# usage: `.theme delete <name>`")
        ok = tm.delete_preset(name)
        if not ok:
            return await ctx.send(f"-# preset `{name}` not found")
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await ctx.send(embed=_ok_embed(f"preset **{name}** deleted"))

    @theme.command(name="seticon")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme seticon [url|attachment]`",
        desc="Sets and stores the server icon in the current theme.",
        section="Setup",
        examples=[".theme seticon https://i.imgur.com/icon.png", ".theme seticon"],
        params=[
            {"name": "source", "type": "str", "required": False, "desc": "Image URL or attachment. Omit to clear the stored icon."},
        ],
        note="The icon is saved as part of the theme and can be restored with `.theme apply`.",
    )
    async def theme_seticon(self, ctx: commands.Context, *, source: str = None):
        """Set and store the server icon in the current theme (attach or pass URL)."""
        try:
            raw = await _resolve_icon_bytes(ctx, source)
        except Exception as exc:
            return await ctx.send(embed=_err_embed(f"couldn't download image: {exc}"))
        if not raw:
            return await ctx.send("-# attach an image or pass a URL")
        try:
            await ctx.guild.edit(icon=raw, reason=f"NeixO theme: set server icon by {ctx.author}")
        except discord.HTTPException as exc:
            return await ctx.send(embed=_err_embed(f"failed to set server icon: {exc}"))
        gtheme = tm.get_guild_theme(ctx.guild.id) or tm.build_empty_theme()
        try:
            gtheme.setdefault("guild", {})["icon"] = base64.b64encode(raw).decode("ascii")
            tm.save_guild_theme(ctx.guild.id, gtheme)
        except Exception:
            pass
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await ctx.send(embed=_ok_embed("server icon updated"))

    @theme.command(name="setbanner")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme setbanner [url|attachment]`",
        desc="Sets and stores the server banner in the current theme.",
        section="Setup",
        examples=[".theme setbanner https://i.imgur.com/banner.png", ".theme setbanner"],
        params=[
            {"name": "source", "type": "str", "required": False, "desc": "Image URL or attachment. Omit to clear the stored banner."},
        ],
        note="The banner is saved as part of the theme and can be restored with `.theme apply`.",
    )
    async def theme_setbanner(self, ctx: commands.Context, *, source: str = None):
        """Set and store the server banner in the current theme (attach or pass URL)."""
        try:
            raw = await _resolve_icon_bytes(ctx, source)
        except Exception as exc:
            return await ctx.send(embed=_err_embed(f"couldn't download image: {exc}"))
        if not raw:
            return await ctx.send("-# attach an image or pass a URL")
        try:
            await ctx.guild.edit(banner=raw, reason=f"NeixO theme: set server banner by {ctx.author}")
        except discord.HTTPException as exc:
            return await ctx.send(embed=_err_embed(f"failed to set server banner: {exc}"))
        gtheme = tm.get_guild_theme(ctx.guild.id) or tm.build_empty_theme()
        try:
            gtheme.setdefault("guild", {})["banner"] = base64.b64encode(raw).decode("ascii")
            tm.save_guild_theme(ctx.guild.id, gtheme)
        except Exception:
            pass
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await ctx.send(embed=_ok_embed("server banner updated"))

    # ══════════════════════════════════════════════════════════
    # RESET (undo via snapshot)
    # ══════════════════════════════════════════════════════════

    @theme.command(name="reset")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme reset`",
        desc="Undoes the last `.theme apply` — restores roles and channels from the snapshot.",
        section="Presets",
        examples=[".theme reset"],
        params=[],
        note="Only one snapshot is stored. A new `.theme apply` overwrites it.",
    )
    async def theme_reset(self, ctx: commands.Context, mode: str = None):
        """Undo the last `.theme apply` — reverts the most recent snapshot.

        Pass `--full` to revert back to the factory snapshot (if available).
        """
        if ctx.guild is None:
            return await ctx.send("-# this command only works in servers.")
        gid = ctx.guild.id

        # peek, don't pop — the snapshot must survive until the user confirms
        full = mode and mode.lower() in ("--full", "full")
        if full:
            factory = tm.get_factory_snapshot(gid)
            if not factory:
                return await ctx.send("-# no factory snapshot found — cannot perform full reset")
            snap = factory
        else:
            snap = tm.get_snapshot(gid)

        if not snap:
            return await ctx.send("-# no snapshot found — nothing to undo")

        role_count = len(snap.get("roles", {}))
        ch_count = len(snap.get("channels", {}))
        total = role_count + ch_count

        e = _embed(ctx, "undo last theme apply")
        e.description = (
            f"this will revert **{role_count}** role(s) and **{ch_count}** channel(s) "
            f"back to their saved names."
        )
        view = ConfirmView(ctx.author.id)
        confirm_msg = await ctx.send(embed=e, view=view)
        await view.wait()
        if not view.confirmed:
            return await confirm_msg.edit(embed=_err_embed("reset cancelled."), view=None)

        # destructive part — only after confirmation
        if full:
            while True:
                s = tm.pop_undo_snapshot(gid)
                if not s:
                    break
                if s == factory:
                    break
        else:
            tm.pop_undo_snapshot(gid)

        prog = await ctx.send(f"-# reverting {total} items...")
        done = 0
        failures: list = []

        # roles
        for role_id_str, data in snap.get("roles", {}).items():
            role = ctx.guild.get_role(int(role_id_str))
            if role:
                try:
                    await asyncio.wait_for(
                        role.edit(name=data["name"], reason="NeixO theme reset"),
                        timeout=10.0,
                    )
                except (asyncio.TimeoutError, discord.HTTPException) as exc:
                    self._collect_failure(failures, "role", role_id_str, data.get("name",""), exc)
            done += 1
            await _edit_progress(prog, done, total, f"role: {data['name']}")
            await self._rate_limit_for_guild(ctx.guild)

        # channels
        for ch_id_str, data in snap.get("channels", {}).items():
            ch = ctx.guild.get_channel(int(ch_id_str))
            if ch:
                try:
                    await asyncio.wait_for(
                        ch.edit(name=data["name"], reason="NeixO theme reset"),
                        timeout=10.0,
                    )
                except (asyncio.TimeoutError, discord.HTTPException) as exc:
                    self._collect_failure(failures, "channel", ch_id_str, data.get("name",""), exc)
            done += 1
            await _edit_progress(prog, done, total, f"channel: {data['name']}")
            await self._rate_limit_for_channel(ch)

        # do not clear the entire undo stack here unless doing a full reset; the pop already removed the applied snapshot
        if mode and mode.lower() in ("--full", "full"):
            # after full reset, clear any remaining snapshots
            tm.clear_undo_stack(gid)
            tm.clear_guild_theme(gid)

        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await prog.edit(content=f"-# `{'█' * 10}` done — reset complete — {done} items reverted")
        await self._report_failures(ctx, failures, "reset failures")

    @theme.command(name="undo")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme undo`",
        desc="Alias for `.theme reset` — pops a single snapshot to undo the last change.",
        section="Presets",
        examples=[".theme undo"],
        params=[],
        note="Same as `.theme reset`.",
    )
    async def theme_undo(self, ctx: commands.Context):
        """Alias for `.theme reset` that pops a single snapshot (undo last change)."""
        await ctx.invoke(self.theme_reset)

    @theme.command(name="snapshot")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme snapshot`",
        desc="Shows what's currently saved in the undo snapshot.",
        section="Presets",
        examples=[".theme snapshot"],
        params=[],
        note="Displays the pre-apply state that would be restored by `.theme reset`.",
    )
    async def theme_snapshot(self, ctx: commands.Context):
        """Show what's saved in the current snapshot (undo data)."""
        snap = tm.get_snapshot(ctx.guild.id)
        if not snap:
            return await ctx.send("-# no snapshot saved")
        roles_count    = len(snap.get("roles", {}))
        channels_count = len(snap.get("channels", {}))

        lines = [f"**{roles_count}** role(s), **{channels_count}** channel(s) stored"]

        # show a few role previews
        for rid, data in list(snap.get("roles", {}).items())[:5]:
            role = ctx.guild.get_role(int(rid))
            current = role.name if role else "deleted"
            lines.append(f"🏷️ `{data['name']}` (current: `{current}`)")

        if roles_count > 5:
            lines.append(f"... and {roles_count - 5} more roles")

        e = _embed(ctx, "snapshot contents", "\n".join(lines))
        e.set_footer(text="use .theme reset to restore all of these")
        await ctx.send(embed=e)

    # ══════════════════════════════════════════════════════════
    # PREFIX GROUPS — named persistent prefix sets
    # ══════════════════════════════════════════════════════════

    @theme.group(name="group", invoke_without_command=True)
    @_is_theme_admin()
    @help_meta(
        usage="`.theme group` / `.tg`",
        desc="Manages named prefix groups — run `.theme group setup` to get started.",
        section="Channels",
        examples=[".theme group", ".theme group setup", ".tg"],
        params=[],
        note="Root command. Subcommands: setup, list, create, delete, set, add, remove, apply. `.tg` is a shorthand alias.",
    )
    async def theme_group(self, ctx: commands.Context):
        await ctx.send(
            "-# group subcommands: `setup`, `list`, `create`, `delete`, `set`, `add`, `remove`, `apply`\n"
            "-# run `.theme group setup` to auto-create groups from detected prefixes"
        )

    @commands.command()
    @_is_theme_admin()
    @help_meta(
        usage="`.tg`",
        desc="Shorthand alias for `.theme group` — accepts all the same subcommands.",
        section="Channels",
        examples=[".tg", ".tg list", ".tg setup"],
        params=[],
        note="Same as `.theme group` but shorter to type.",
    )
    async def tg(self, ctx: commands.Context, *args):
        """Compatibility wrapper for `.tg` — forwards to `.theme group` (best-effort)."""
        # simple forward: if no args, invoke the group help; otherwise try to call the named subcommand
        if not args:
            return await ctx.invoke(self.theme_group)
        # try invoking `theme group <sub>` via bot command lookup
        sub = args[0]
        cmd = self.bot.get_command(f"theme group {sub}")
        if cmd:
            try:
                return await ctx.invoke(cmd, *args[1:])
            except Exception as exc:
                log.warning(f"tg command failed: {exc}")
        # fallback: show group help
        await ctx.invoke(self.theme_group)

    @theme_group.command(name="setup")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme group setup`",
        desc="Scans the entire server for prefixes and interactively creates named prefix groups.",
        section="Channels",
        examples=[".theme group setup"],
        params=[],
        note="Interactive wizard. Detects existing prefixes and lets you assign them to named groups.",
    )
    async def group_setup(self, ctx: commands.Context):
        """Scan the server for prefixes and interactively create named groups.
        .theme group setup
        """
        # scan entire server
        all_found: dict[str, dict] = {}
        for cat in ctx.guild.categories:
            for ch in cat.channels:
                p = tm.detect_prefix(ch.name)
                if p:
                    all_found.setdefault(p, {}).setdefault(str(cat.id), []).append(ch.name)

        if not all_found:
            return await ctx.send("-# no prefixes detected anywhere in the server — nothing to set up")

        existing_groups = tm.get_prefix_groups(ctx.guild.id)
        created = 0

        for prefix, cat_map in all_found.items():
            total_chs = sum(len(v) for v in cat_map.values())
            cat_names = []
            for cid in cat_map:
                cat = ctx.guild.get_channel(int(cid))
                if cat:
                    cat_names.append(cat.name)

            # check if already in a group
            already = next(
                (n for n, d in existing_groups.items() if d.get("prefix") == prefix),
                None,
            )
            if already:
                await ctx.send(embed=_ok_embed(
                    f"prefix `{prefix}` is already group **{already}** — skipping"
                ))
                continue

            e = discord.Embed(
                title=f"create group for `{prefix}`?",
                description=(
                    f"detected `{prefix}` on **{total_chs}** channel(s) across: "
                    f"{', '.join(f'**{n}**' for n in cat_names)}\n\n"
                    f"click ✅ to name and create this group, or skip."
                ),
                color=Neixocolor,
            )

            class SetupGroupView(View):
                def __init__(self_v):
                    super().__init__(timeout=60)
                    self_v.action = None

                async def interaction_check(self_v, interaction: discord.Interaction) -> bool:
                    return interaction.user.id == ctx.author.id

                @discord.ui.button(emoji="<:7079verifiedblacksimplified:1255031445806780467>", style=discord.ButtonStyle.gray)
                async def confirm_btn(self_v, interaction: discord.Interaction, button: Button):
                    class NameModal(discord.ui.Modal, title="Name this prefix group"):
                        name_input = discord.ui.TextInput(
                            label="Group name (e.g. main, media)",
                            placeholder="main",
                            max_length=32,
                        )
                        async def on_submit(modal_self, inter: discord.Interaction):
                            self_v.action = modal_self.name_input.value.strip().lower()
                            await inter.response.edit_message(
                                embed=_ok_embed(f"group **{self_v.action}** → `{prefix}`"),
                                view=None,
                            )
                            self_v.stop()
                    await interaction.response.send_modal(NameModal())

                @discord.ui.button(emoji="<:Blackkatana:1252608867876212778>", style=discord.ButtonStyle.gray)
                async def skip_btn(self_v, interaction: discord.Interaction, button: Button):
                    self_v.action = None
                    await interaction.response.edit_message(
                        embed=_err_embed(f"skipped `{prefix}`"),
                        view=None,
                    )
                    self_v.stop()

            view = SetupGroupView()
            await ctx.send(embed=e, view=view)
            await view.wait()

            if view.action:
                existing_groups[view.action] = {
                    "prefix": prefix,
                    "categories": list(cat_map.keys()),
                }
                created += 1

        tm.save_prefix_groups(ctx.guild.id, existing_groups)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await ctx.send(embed=_ok_embed(
            f"setup complete — `{created}` group(s) created. use `.theme group list` to see them."
        ))

    @theme_group.command(name="list")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme group list`",
        desc="Shows all prefix groups — name, prefix, categories, and channel count.",
        section="Channels",
        examples=[".theme group list"],
        params=[],
        note="Displays a summary of all configured prefix groups.",
    )
    async def group_list(self, ctx: commands.Context):
        """List all prefix groups for this server."""
        groups = tm.get_prefix_groups(ctx.guild.id)
        if not groups:
            return await ctx.send("-# no groups set up yet — run `.theme group setup`")
        lines = []
        for name, data in groups.items():
            prefix = data.get("prefix", "?")
            cat_ids = data.get("categories", [])
            cat_names = []
            total_chs = 0
            for cid in cat_ids:
                cat = ctx.guild.get_channel(int(cid))
                if cat:
                    cat_names.append(f"**{cat.name}**")
                    total_chs += len(cat.channels)
            cats_str = ", ".join(cat_names) if cat_names else "*(no categories)*"
            lines.append(f"**{name}** — `{prefix}` — {total_chs} channel(s) — {cats_str}")
        e = _embed(ctx, "prefix groups", "\n".join(lines))
        e.set_footer(text=".theme group set <name> <new prefix> to change · .theme group add <name> #cat to add a category")
        await ctx.send(embed=e)

    @theme_group.command(name="create")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme group create <name> <prefix>`",
        desc="Manually creates a named prefix group.",
        section="Channels",
        examples=[".theme group create gaming 🎮", ".theme group create info ℹ️"],
        params=[
            {"name": "name", "type": "str", "required": True, "desc": "Name for the new prefix group."},
            {"name": "prefix", "type": "str", "required": True, "desc": "The prefix emoji or symbol for channels in this group."},
        ],
        note="The prefix is not applied to any channels yet. Use `.theme group add` to assign categories.",
    )
    async def group_create(self, ctx: commands.Context, name: str = None, prefix: str = None):
        """Manually create a prefix group.
        .theme group create main ∞・
        """
        if not name or not prefix:
            return await ctx.send("-# usage: `.theme group create <name> <prefix>`")
        name = name.lower().strip()
        groups = tm.get_prefix_groups(ctx.guild.id)
        if name in groups:
            return await ctx.send(f"-# group `{name}` already exists — use `.theme group set {name} <prefix>` to change its prefix")
        groups[name] = {"prefix": prefix, "categories": []}
        tm.save_prefix_groups(ctx.guild.id, groups)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await ctx.send(embed=_ok_embed(f"group **{name}** created with prefix `{prefix}` — use `.theme group add {name} #cat` to add categories"))

    @theme_group.command(name="delete")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme group delete <name>`",
        desc="Deletes a prefix group record. Does not rename any channels.",
        section="Channels",
        examples=[".theme group delete gaming"],
        params=[
            {"name": "name", "type": "str", "required": True, "desc": "Name of the group to delete."},
        ],
        note="Channel names are NOT changed when a group is deleted.",
    )
    async def group_delete(self, ctx: commands.Context, name: str = None):
        """Delete a prefix group (doesn't touch channel names)."""
        if not name:
            return await ctx.send("-# usage: `.theme group delete <name>`")
        name = name.lower().strip()
        groups = tm.get_prefix_groups(ctx.guild.id)
        if name not in groups:
            return await ctx.send(f"-# group `{name}` not found — see `.theme group list`")
        e = discord.Embed(
            title=f"delete group \"{name}\"?",
            description=f"prefix `{groups[name]['prefix']}` — this won't rename any channels, just removes the group record.",
            color=Neixocolor,
        )
        view = ConfirmView(ctx.author.id)
        confirm_msg = await ctx.send(embed=e, view=view)
        await view.wait()
        if not view.confirmed:
            return await confirm_msg.edit(embed=_err_embed("cancelled."), view=None)
        del groups[name]
        tm.save_prefix_groups(ctx.guild.id, groups)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await confirm_msg.edit(embed=_ok_embed(f"group **{name}** deleted."), view=None)

    @theme_group.command(name="set")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme group set <name> <new prefix>`",
        desc="Changes a group's prefix and renames all channels in that group.",
        section="Channels",
        examples=[".theme group set gaming 🕹️", ".theme group set info 📋"],
        params=[
            {"name": "name", "type": "str", "required": True, "desc": "Name of the group to update."},
            {"name": "new prefix", "type": "str", "required": True, "desc": "The new prefix emoji or symbol."},
        ],
        note="All channels in the group are immediately renamed with the new prefix.",
    )
    async def group_set(self, ctx: commands.Context, name: str = None, *, new_prefix: str = None):
        """Change a group's prefix and apply it to all channels in that group.
        .theme group set main ⭐
        """
        if not name or not new_prefix:
            return await ctx.send("-# usage: `.theme group set <name> <new prefix>`")
        name = name.lower().strip()
        new_prefix = new_prefix.strip()
        groups = tm.get_prefix_groups(ctx.guild.id)
        if name not in groups:
            return await ctx.send(f"-# group `{name}` not found — see `.theme group list`")

        old_prefix = groups[name]["prefix"]
        cat_ids = groups[name].get("categories", [])

        # find all channels in this group's categories
        channels_to_edit = []
        for cid in cat_ids:
            cat = ctx.guild.get_channel(int(cid))
            if cat and isinstance(cat, discord.CategoryChannel):
                channels_to_edit.extend(cat.channels)

        if not channels_to_edit:
            # just update the stored prefix, no channels to rename
            groups[name]["prefix"] = new_prefix
            tm.save_prefix_groups(ctx.guild.id, groups)
            await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
            return await ctx.send(embed=_ok_embed(f"group **{name}** prefix updated to `{new_prefix}` (no channels to rename)"))

        changes = []
        for ch in channels_to_edit:
            detected = tm.detect_prefix(ch.name)
            if detected == old_prefix:
                rest = ch.name[len(old_prefix):].lstrip("-").strip()
                new_name = tm.apply_prefix(rest, new_prefix)
            else:
                new_name = tm.apply_prefix(ch.name, new_prefix)
            if new_name != ch.name:
                changes.append((ch.name, new_name))

        warn = f"-# ⚠️ `{new_prefix}` looks like a plain word — channels will look like `{new_prefix}general`\n" if tm.is_plain_word(new_prefix) else ""
        view = PreviewView(ctx.author.id, f"group set: {name} → `{new_prefix}`", changes, warn=warn)
        preview_msg = await ctx.send(embed=view.build_embed(), view=view)
        await view.wait()

        if not view.confirmed:
            return await preview_msg.edit(embed=_err_embed("cancelled."), view=None)

        await self._ensure_snapshot(ctx.guild)
        history_snap = {str(ch.id): ch.name for ch in channels_to_edit}
        prog = await ctx.send(f"-# updating group **{name}** prefix `{old_prefix}` → `{new_prefix}` on {len(changes)} channels...")
        done = 0
        failures: list = []

        for ch in channels_to_edit:
            detected = tm.detect_prefix(ch.name)
            if detected == old_prefix:
                rest = ch.name[len(old_prefix):].lstrip("-").strip()
                new_name = tm.apply_prefix(rest, new_prefix)
            else:
                new_name = tm.apply_prefix(ch.name, new_prefix)
            if new_name != ch.name:
                try:
                    await asyncio.wait_for(
                        ch.edit(name=new_name, reason=f"NeixO group set: {ctx.author}"),
                        timeout=10.0,
                    )
                except (discord.HTTPException, asyncio.TimeoutError) as exc:
                    self._collect_failure(failures, "channel", ch.id, ch.name, exc)
                await self._rate_limit_for_channel(ch)
            done += 1
            await _edit_progress(prog, done, len(channels_to_edit), ch.name)

        groups[name]["prefix"] = new_prefix
        tm.save_prefix_groups(ctx.guild.id, groups)
        tm.save_prefix_history(ctx.guild.id, {"op": f"group_set:{name}:{old_prefix}→{new_prefix}", "channels": history_snap})

        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await prog.edit(content=f"-# `{'█' * 10}` done — group **{name}** prefix updated, {len(changes)} channels renamed")
        await self._report_failures(ctx, failures, "group set failures")

    @theme_group.command(name="add")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme group add <name> #category`",
        desc="Adds a category to a group and applies the group's prefix to all its channels.",
        section="Channels",
        examples=[".theme group add gaming #game-chat", ".theme group add info #information"],
        params=[
            {"name": "name", "type": "str", "required": True, "desc": "Name of the target group."},
            {"name": "category", "type": "discord.CategoryChannel", "required": True, "desc": "The category to add to the group."},
        ],
        note="All channels in the category will be prefixed with the group's prefix.",
    )
    async def group_add(self, ctx: commands.Context, name: str = None, *, args: str = None):
        """Add a category to a group. Applies the group's prefix to all channels in it.
        .theme group add main #general-category
        """
        if not name or not args:
            return await ctx.send("-# usage: `.theme group add <name> #category`")
        name = name.lower().strip()
        groups = tm.get_prefix_groups(ctx.guild.id)
        if name not in groups:
            return await ctx.send(f"-# group `{name}` not found — see `.theme group list`")

        # resolve category
        cat = None
        arg = args.strip()
        if arg.startswith("<#") and arg.endswith(">"):
            cid = int(arg[2:-1])
            cat = ctx.guild.get_channel(cid)
        else:
            try:
                cat = ctx.guild.get_channel(int(arg))
            except ValueError:
                cat = discord.utils.get(ctx.guild.categories, name=arg)

        if not cat or not isinstance(cat, discord.CategoryChannel):
            return await ctx.send("-# couldn't find that category")

        cat_id_str = str(cat.id)
        if cat_id_str in groups[name]["categories"]:
            return await ctx.send(f"-# **{cat.name}** is already in group **{name}**")

        # check if in another group
        other = tm.get_group_for_category(ctx.guild.id, cat.id)
        if other and other[0] != name:
            return await ctx.send(f"-# **{cat.name}** already belongs to group **{other[0]}** — remove it first")

        prefix = groups[name]["prefix"]
        changes = []
        for ch in cat.channels:
            new_name = tm.apply_prefix(ch.name, prefix)
            if new_name != ch.name:
                changes.append((ch.name, new_name))

        if changes:
            view = PreviewView(ctx.author.id, f"addcat: {cat.name} → group {name}", changes)
            preview_msg = await ctx.send(embed=view.build_embed(), view=view)
            await view.wait()
            if not view.confirmed:
                return await preview_msg.edit(embed=_err_embed("cancelled."), view=None)

            await self._ensure_snapshot(ctx.guild)
            history_snap = {str(ch.id): ch.name for ch in cat.channels}
            prog = await ctx.send(f"-# applying prefix `{prefix}` to {len(changes)} channels in **{cat.name}**...")
            done = 0
            failures: list = []
            for ch in cat.channels:
                new_name = tm.apply_prefix(ch.name, prefix)
                if new_name != ch.name:
                    try:
                        await asyncio.wait_for(
                            ch.edit(name=new_name, reason=f"NeixO group add: {ctx.author}"),
                            timeout=10.0,
                        )
                    except (discord.HTTPException, asyncio.TimeoutError) as exc:
                        self._collect_failure(failures, "channel", ch.id, ch.name, exc)
                    await self._rate_limit_for_channel(ch)
                done += 1
                await _edit_progress(prog, done, len(cat.channels), ch.name)
            tm.save_prefix_history(ctx.guild.id, {"op": f"group_add:{name}", "channels": history_snap})
            await prog.edit(content=f"-# `{'█' * 10}` done — prefix applied")
            await self._report_failures(ctx, failures, "group add failures")

        groups[name]["categories"].append(cat_id_str)
        tm.save_prefix_groups(ctx.guild.id, groups)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await ctx.send(embed=_ok_embed(f"**{cat.name}** added to group **{name}** — use `.theme group remove {name} #cat` to undo"))

    @theme_group.command(name="remove")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme group remove <name> #category`",
        desc="Removes a category from a group. Channel names are not changed.",
        section="Channels",
        examples=[".theme group remove gaming #game-chat"],
        params=[
            {"name": "name", "type": "str", "required": True, "desc": "Name of the group."},
            {"name": "category", "type": "discord.CategoryChannel", "required": True, "desc": "The category to remove from the group."},
        ],
        note="Channel names retain their current prefix. Use `.theme prefix remove` to strip prefixes.",
    )
    async def group_remove(self, ctx: commands.Context, name: str = None, *, args: str = None):
        """Remove a category from a group (doesn't strip prefix from channels)."""
        if not name or not args:
            return await ctx.send("-# usage: `.theme group remove <name> #category`")
        name = name.lower().strip()
        groups = tm.get_prefix_groups(ctx.guild.id)
        if name not in groups:
            return await ctx.send(f"-# group `{name}` not found")

        cat = None
        arg = args.strip()
        if arg.startswith("<#") and arg.endswith(">"):
            cid = int(arg[2:-1])
            cat = ctx.guild.get_channel(cid)
        else:
            try:
                cat = ctx.guild.get_channel(int(arg))
            except ValueError:
                cat = discord.utils.get(ctx.guild.categories, name=arg)

        if not cat:
            return await ctx.send("-# couldn't find that category")

        cat_id_str = str(cat.id)
        if cat_id_str not in groups[name]["categories"]:
            return await ctx.send(f"-# **{cat.name}** is not in group **{name}**")

        groups[name]["categories"].remove(cat_id_str)
        tm.save_prefix_groups(ctx.guild.id, groups)
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await ctx.send(embed=_ok_embed(f"**{cat.name}** removed from group **{name}** — channel names unchanged — use `.theme prefix undo` if you want to strip the prefix too"))

    @theme_group.command(name="apply")
    @_is_theme_admin()
    @help_meta(
        usage="`.theme group apply <name> #channel`",
        desc="Stamps a group's prefix onto one specific channel without adding it to the group.",
        section="Channels",
        examples=[".theme group apply gaming #random"],
        params=[
            {"name": "name", "type": "str", "required": True, "desc": "Name of the group whose prefix to use."},
            {"name": "channel", "type": "discord.TextChannel", "required": True, "desc": "The channel to stamp the prefix on."},
        ],
        note="The channel's name is prefixed but it is NOT added to the group's category list.",
    )
    async def group_apply(self, ctx: commands.Context, name: str = None, channel: discord.TextChannel = None):
        """Stamp a group's prefix onto one specific channel without adding it to the group.
        .theme group apply main #general
        """
        if not name or not channel:
            return await ctx.send("-# usage: `.theme group apply <name> #channel`")
        name = name.lower().strip()
        groups = tm.get_prefix_groups(ctx.guild.id)
        if name not in groups:
            return await ctx.send(f"-# group `{name}` not found")

        prefix = groups[name]["prefix"]
        new_name = tm.apply_prefix(channel.name, prefix)
        if new_name == channel.name:
            return await ctx.send(f"-# **{channel.name}** already has that prefix")

        view = PreviewView(ctx.author.id, f"apply: group {name}", [(channel.name, new_name)])
        preview_msg = await ctx.send(embed=view.build_embed(), view=view)
        await view.wait()

        if not view.confirmed:
            return await preview_msg.edit(embed=_err_embed("cancelled."), view=None)

        old_name = channel.name
        await self._ensure_snapshot(ctx.guild)
        history_snap = {str(channel.id): old_name}
        try:
            await asyncio.wait_for(
                channel.edit(name=new_name, reason=f"NeixO group apply: {ctx.author}"),
                timeout=10.0,
            )
        except (discord.HTTPException, asyncio.TimeoutError) as exc:
            return await ctx.send(embed=_err_embed(f"failed to rename: {exc}"))

        tm.save_prefix_history(ctx.guild.id, {"op": f"group_apply:{name}", "channels": history_snap})
        await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
        await ctx.send(embed=_ok_embed(f"**{old_name}** renamed to **{new_name}**"))

    # ── Auto-apply on channel create ──────────────────────────

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        if isinstance(channel, discord.CategoryChannel):
            return
        if not channel.category:
            return

        group_info = tm.get_group_for_category(channel.guild.id, channel.category.id)
        if not group_info:
            return

        group_name, prefix = group_info
        new_name = tm.apply_prefix(channel.name, prefix)
        if new_name == channel.name:
            return

        # find who created the channel via audit log
        creator = None
        await asyncio.sleep(1.0)  # brief wait for audit log to populate
        try:
            async for entry in channel.guild.audit_logs(
                limit=5, action=discord.AuditLogAction.channel_create
            ):
                if entry.target.id == channel.id:
                    creator = entry.user
                    break
        except discord.Forbidden:
            pass

        class PrefixPromptView(View):
            def __init__(self_v):
                super().__init__(timeout=30)
                self_v.result = None

            async def interaction_check(self_v, interaction: discord.Interaction) -> bool:
                if creator:
                    if interaction.user.id != creator.id:
                        await interaction.response.send_message(
                            "-# only the channel creator can respond to this", ephemeral=True
                        )
                        return False
                elif interaction.user.id != channel.guild.owner_id:
                    perms = channel.permissions_for(interaction.user)
                    if not perms.manage_channels:
                        await interaction.response.send_message(
                            "-# only the channel creator or a user with manage_channel permission can respond", ephemeral=True
                        )
                        return False
                return True

            @discord.ui.button(emoji="<:7079verifiedblacksimplified:1255031445806780467>", style=discord.ButtonStyle.gray)
            async def confirm_btn(self_v, interaction: discord.Interaction, button: Button):
                self_v.result = True
                await interaction.response.defer()
                self_v.stop()

            @discord.ui.button(emoji="<:Blackkatana:1252608867876212778>", style=discord.ButtonStyle.gray)
            async def cancel_btn(self_v, interaction: discord.Interaction, button: Button):
                self_v.result = False
                await interaction.response.defer()
                self_v.stop()

        ping = creator.mention if creator else ""
        view = PrefixPromptView()
        try:
            prompt_msg = await channel.send(
                f"{ping} — {channel.mention} is themed on **{group_name}** (`{prefix}`) prefix.\n"
                f"-# rename to: **{new_name}**?",
                view=view,
            )
        except discord.HTTPException:
            return

        await view.wait()

        try:
            await prompt_msg.delete()
        except discord.HTTPException:
            pass

        if view.result is True:
            try:
                await asyncio.wait_for(
                    channel.edit(name=new_name, reason=f"NeixO auto prefix: group {group_name}"),
                    timeout=10.0,
                )
            except (discord.HTTPException, asyncio.TimeoutError) as exc:
                log.warning(f"on_guild_channel_create rename failed: {exc}")

        elif view.result is None:
            # timeout — auto rename and notify
            try:
                await asyncio.wait_for(
                    channel.edit(name=new_name, reason=f"NeixO auto prefix (timeout): group {group_name}"),
                    timeout=10.0,
                )
                await channel.send(
                    f"-# renamed this channel to **{new_name}** — use `.theme prefix undo` to revert"
                )
            except (discord.HTTPException, asyncio.TimeoutError) as exc:
                log.warning(f"on_guild_channel_create timeout rename failed: {exc}")

    # ══════════════════════════════════════════════════════════
    # INTERNAL HELPERS
    # ══════════════════════════════════════════════════════════

    async def _ensure_snapshot(self, guild: discord.Guild) -> None:
        """Push a snapshot of current role names + channel names onto the undo stack.

        We also save a factory snapshot (first snapshot after setup) if none exists.
        """
        role_map = tm.get_role_map(guild.id)
        roles_snap: dict = {}
        for rid in role_map.values():
            role = guild.get_role(int(rid))
            if role:
                roles_snap[str(role.id)] = {"name": role.name}

        channels_snap: dict = {}
        for ch in guild.channels:
            if not isinstance(ch, discord.CategoryChannel):
                channels_snap[str(ch.id)] = {"name": ch.name}

        snap = {"roles": roles_snap, "channels": channels_snap}
        tm.push_undo_snapshot(guild.id, snap)
        # set factory snapshot on first push
        if not tm.get_factory_snapshot(guild.id):
            tm.save_factory_snapshot(guild.id, snap)
        log.info(f"snapshot pushed for guild {guild.id}: {len(roles_snap)} roles, {len(channels_snap)} channels")

    # ══════════════════════════════════════════════════════════
    # TOP-LEVEL FAST SHORTCUTS (.tr, .tri, .troles, .tmap, .tfont, .tprefix, etc.)
    # ══════════════════════════════════════════════════════════

    @commands.command(name="tr", aliases=["themerole"])
    @_is_theme_admin()
    @help_meta(
        usage="`.tr <slot|#index> <new name>`",
        desc="Fast shortcut to rename a mapped server role. Supports slot number or name.",
        section="Roles",
        perm_tier="admin",
        discord_perms=["manage_roles"],
        examples=[".tr 1 True Dragon", ".tr owner God Emperor", ".tr co owner Vice King"],
        params=[
            {"name": "slot", "type": "str", "required": True, "desc": "Slot number (1, 2...) or slot name."},
            {"name": "new_name", "type": "str", "required": True, "desc": "The new display name for the role."},
        ],
    )
    async def fast_tr(self, ctx: commands.Context, *, args: str = None):
        """Fast role rename: .tr 1 True Dragon"""
        await self.theme_role(ctx, args=args)

    @commands.command(name="tri", aliases=["themeroleicon", "ticon"])
    @_is_theme_admin()
    @help_meta(
        usage="`.tri <slot|#index> [emoji|url|attachment]`",
        desc="Fast shortcut to set a role icon for a slot. Supports slot number or name.",
        section="Roles",
        perm_tier="admin",
        discord_perms=["manage_roles"],
        examples=[".tri 1 👑", ".tri owner https://i.imgur.com/icon.png"],
        params=[
            {"name": "slot", "type": "str", "required": True, "desc": "Slot number (1, 2...) or slot name."},
            {"name": "source", "type": "str", "required": False, "desc": "Emoji, URL, or image attachment."},
        ],
    )
    async def fast_tri(self, ctx: commands.Context, *, args: str = None):
        """Fast role icon: .tri 1 👑"""
        await self.theme_roleicon(ctx, args=args)

    @commands.command(name="troles", aliases=["tslots", "trl", "themeroles"])
    @help_meta(
        usage="`.troles`",
        desc="Fast shortcut to list all mapped role slots and their current Discord roles.",
        section="Setup",
        perm_tier="public",
        examples=[".troles", ".tslots"],
        params=[],
    )
    async def fast_troles(self, ctx: commands.Context):
        """Fast list role slots: .troles"""
        await self.theme_roles(ctx)

    @commands.command(name="tmap", aliases=["tsetrole", "themesetrole"])
    @_is_theme_admin()
    @help_meta(
        usage="`.tmap <slot|#index> [@role]`",
        desc="Fast shortcut to bind a Discord role to a theme slot.",
        section="Setup",
        perm_tier="admin",
        discord_perms=["manage_roles"],
        examples=[".tmap 1 @Owner", ".tmap owner @Owner"],
        params=[
            {"name": "slot", "type": "str", "required": True, "desc": "Slot number or slot name to map."},
            {"name": "role", "type": "role", "required": False, "desc": "The Discord role to map to this slot."},
        ],
    )
    async def fast_tmap(self, ctx: commands.Context, slot: str = None, role: discord.Role = None):
        """Fast map slot: .tmap 1 @role"""
        await self.theme_setrole(ctx, slot=slot, role=role)

    @commands.command(name="trevert", aliases=["themeresetrole", "tresetrole"])
    @_is_theme_admin()
    @help_meta(
        usage="`.trevert <slot|#index>`",
        desc="Fast shortcut to revert a role name from the latest snapshot.",
        section="Roles",
        perm_tier="admin",
        discord_perms=["manage_roles"],
        examples=[".trevert 1", ".trevert owner"],
        params=[
            {"name": "slot", "type": "str", "required": True, "desc": "Slot number or slot name to revert."},
        ],
    )
    async def fast_trevert(self, ctx: commands.Context, *, slot_arg: str = None):
        """Fast revert role: .trevert 1"""
        await self.role_revert(ctx, slot_arg=slot_arg)

    @commands.command(name="tfont", aliases=["themefont"])
    @_is_theme_admin()
    @help_meta(
        usage="`.tfont <font_name> [all|<#category>]`",
        desc="Fast shortcut to apply a unicode font style to server channels.",
        section="Fonts",
        perm_tier="admin",
        discord_perms=["manage_channels"],
        examples=[".tfont bold all", ".tfont gothic #general", ".tfont list"],
        params=[
            {"name": "font", "type": "str", "required": True, "desc": "Font name (e.g. bold, italic, gothic, script)."},
            {"name": "target", "type": "str", "required": False, "desc": "'all' or category/channel mention."},
        ],
    )
    async def fast_tfont(self, ctx: commands.Context, *, args: str = None):
        """Fast font command: .tfont bold all"""
        await self.theme_font(ctx, args=args)

    @commands.command(name="tfonts", aliases=["themefonts"])
    @help_meta(
        usage="`.tfonts`",
        desc="Fast shortcut to view all available unicode fonts.",
        section="Fonts",
        perm_tier="public",
        examples=[".tfonts"],
        params=[],
    )
    async def fast_tfonts(self, ctx: commands.Context):
        """Fast font list: .tfonts"""
        await ctx.invoke(self.font_list)

    @commands.command(name="tprefix", aliases=["themeprefix"])
    @_is_theme_admin()
    @help_meta(
        usage="`.tprefix <emoji|symbol> [<#category> ...]`",
        desc="Fast shortcut to apply channel prefixes across categories.",
        section="Prefixes",
        perm_tier="admin",
        discord_perms=["manage_channels"],
        examples=[".tprefix ✦ #general", ".tprefix list"],
        params=[
            {"name": "emoji", "type": "str", "required": True, "desc": "Emoji or unicode symbol to prepend."},
            {"name": "categories", "type": "str", "required": False, "desc": "Category mentions or 'all'."},
        ],
    )
    async def fast_tprefix(self, ctx: commands.Context, *, args: str = None):
        """Fast prefix command: .tprefix ✦ #general"""
        await self.theme_prefix(ctx, args=args)

    @commands.command(name="tapply", aliases=["themeapply"])
    @_is_theme_admin()
    @help_meta(
        usage="`.tapply <preset_name>`",
        desc="Fast shortcut to apply a saved server theme preset.",
        section="Presets",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".tapply dark_fantasy", ".tapply cyber_pink"],
        params=[
            {"name": "name", "type": "str", "required": True, "desc": "The name of the saved preset to apply."},
        ],
    )
    async def fast_tapply(self, ctx: commands.Context, *, name: str = None):
        """Fast theme apply: .tapply <name>"""
        await self.theme_apply(ctx, name=name)

    @commands.command(name="tsave", aliases=["themesave"])
    @_is_theme_admin()
    @help_meta(
        usage="`.tsave <preset_name>`",
        desc="Fast shortcut to save the current server theme as a preset.",
        section="Presets",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".tsave my_theme"],
        params=[
            {"name": "name", "type": "str", "required": True, "desc": "Name for the new preset."},
        ],
    )
    async def fast_tsave(self, ctx: commands.Context, *, name: str = None):
        """Fast theme save: .tsave <name>"""
        await self.theme_save(ctx, name=name)

    @commands.command(name="tsetup", aliases=["themesetup"])
    @_is_theme_admin()
    @help_meta(
        usage="`.tsetup`",
        desc="Fast shortcut to run the interactive role slot mapping wizard.",
        section="Setup",
        perm_tier="admin",
        discord_perms=["manage_roles"],
        examples=[".tsetup"],
        params=[],
    )
    async def fast_tsetup(self, ctx: commands.Context):
        """Fast theme setup: .tsetup"""
        await self.theme_setup(ctx)

    @commands.command(name="treset", aliases=["themereset"])
    @_is_theme_admin()
    @help_meta(
        usage="`.treset`",
        desc="Fast shortcut to restore server roles and channels to snapshot.",
        section="Presets",
        perm_tier="admin",
        discord_perms=["manage_guild"],
        examples=[".treset"],
        params=[],
    )
    async def fast_treset(self, ctx: commands.Context):
        """Fast theme reset: .treset"""
        await self.theme_reset(ctx)


async def setup(bot: commands.Bot):
    await bot.add_cog(ThemeCog(bot))
