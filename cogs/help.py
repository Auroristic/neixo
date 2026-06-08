from __future__ import annotations

import logging

import discord
from discord.ext import commands
from discord.ui import Select, View, Button
from typing import Any, Dict, List, Optional, Tuple

from utils import load_json, get_embed_color, is_owner_or_creator, CONFIG_FILE

log = logging.getLogger(__name__)

# ── Category & Section icon mapping ───────────────────────────────


# Category icons by (lowercase) category label
_CAT_ICONS: dict[str, str] = {
    "music": "\u266A",          # ♪
    "theme": "\u2605",          # ★
    "admin": "\u25C6",          # ◆
    "moderation": "\u2694",     # ⚔
    "fun": "\u2662",            # ♢
    "profile": "\u25CF",        # ●
    "utility": "\u25A0",        # ■
    "miscellaneous": "\u25AA",  # ▪
    "misc": "\u25AA",           # ▪
    "help": "?",                # ?
    "setup": "\u2699",          # ⚙
    "support": "\u2665",        # ♥
    "server": "\u25BC",         # ▼
    "ai": "\u25B2",             # ▲  (was ⊡)
    "imagine": "\u2726",        # ✦
    "vanity": "\u25C6",         # ◆  (was ✧)
    "reminders": "\u25CF",      # ●  (was ◷)
    "reactions": "\u2665",      # ♥  (was ☺)
    "confessions": "\u270E",    # ✎
    "check": "\u2713",          # ✓
    "serverstats": "\u25A3",    # ▣
    "funimage": "\u2726",       # ✦
    "gifs": "\u25CB",           # ○
    "memory": "\u25C8",         # ◈
    "config": "\u2699",         # ⚙
    "general": "\u2606",        # ☆
}

# Section icons (override per command section if desired)
_SEC_ICONS: dict[str, str] = {
    "playback": "\u25BA",       # ►
    "controls": "\u25C8",       # ◈
    "filters": "\u25C7",        # ◇
    "queue": "\u2261",          # ≡
    "overview": "\u25C9",       # ◉
    "setup": "\u2699",          # ⚙
    "roles": "\u25CE",          # ◎
    "channels": "\u25C7",       # ◇  (was ☰)
    "presets": "\u25A0",        # ■  (was ♺)
    "user": "\u25CF",           # ●
    "rpc": "\u25C6",            # ◆  (was ◇)
    "moderation": "\u2694",     # ⚔
    "config": "\u2699",         # ⚙
    "general": "\u2606",        # ☆
    "image": "\u2726",          # ✦
    "text": "\u270E",           # ✎
    "anime": "\u25CE",          # ◎
    "games": "\u2662",          # ♢
    "quotes": "\u275D",         # ❝
    "owner": "\u265B",          # ♛
    "server": "\u25BC",         # ▼  (was ⌂)
    "member": "\u25CF",         # ●
    "commands": "\u25C6",       # ◆  (was ⌨)
    "voice": "\u266A",          # ♪
    "messages": "\u2709",       # ✉
    "info": "\u2139",           # ℹ
    "manage": "\u2699",         # ⚙
    "utilities": "\u25A0",      # ■
    "search": "\u25C7",         # ◇  (was ☰)
    "stats": "\u25A3",          # ▣
    "ai": "\u25B2",             # ▲  (was ⊡)
    "image gen": "\u2726",      # ✦
    "vanity": "\u25C6",         # ◆  (was ✧)
    "reminders": "\u25CF",      # ●  (was ◷)
    "birthdays": "\u2606",      # ☆
    "confessions": "\u270E",    # ✎
    "reactions": "\u2665",      # ♥  (was ☺)
    "checks": "\u2713",         # ✓
    "server stats": "\u25A3",   # ▣
    "verification": "\u2713",   # ✓
    "notes": "\u270E",          # ✎
    "blacklist": "\u2715",      # ✕  (was ⊘)
    "whispers": "\u2665",       # ♥  (was ♡)
    "command channels": "\u25C6", # ◆  (was ⌨)
    "captcha": "\u25B2",        # ▲  (was ⊡)
    "suggestions": "\u2709",    # ✉
}


def _cat_icon(cat_label: str) -> str:
    return _CAT_ICONS.get(cat_label.lower(), "\u25aa")


def _sec_icon(sec_label: str) -> str:
    return _SEC_ICONS.get(sec_label.lower(), "\u00b7")





# ── DYNAMIC HELP SYSTEM ──────────────────────────────────────────
# (see module docstring above — each cog declares COG_META at module level)
# ─────────────────────────────────────────────────────────────────

# ── permission helpers ────────────────────────────────────────

def _can_see(d: Dict[str, Any], is_owner: bool, is_wl: bool) -> bool:
    if d.get("owner") and not is_owner:
        return False
    if d.get("admin") and not is_owner:
        return False
    if d.get("staff") and not (is_owner or is_wl):
        return False
    return True

# ── runtime metadata collector ────────────────────────────────

def _collect(bot: commands.Bot, is_owner: bool, is_wl: bool):
    categories: Dict[str, Any] = {}
    cmd_index: Dict[str, Any] = {}
    seen_metas: set[int] = set()

    import sys
    from utils import get_help_meta

    def _process_command(cmd, cat_id, cat_label):
        meta = get_help_meta(cmd)
        if meta is None:
            return

        cmd_owner = meta.get("owner", False)
        cmd_staff = meta.get("staff", False)

        d = {
            "owner": cmd_owner,
            "staff": cmd_staff,
        }
        if not _can_see(d, is_owner, is_wl):
            return

        cmd_name = cmd.qualified_name
        desc = meta.get("desc") or cmd.help or "No description."
        usage = meta.get("usage") or (f"`.{cmd_name} {cmd.signature}`" if cmd.signature else f"`.{cmd_name}`")
        aliases = cmd.aliases

        sec = meta.get("section") or cat_label

        d.update({
            "desc": desc,
            "usage": usage,
            "aliases": aliases,
            "section": sec,
            "_cat_label": cat_label,
        })

        categories[cat_id]["sections"].setdefault(sec, [])
        categories[cat_id]["sections"][sec].append((cmd_name, d))
        cmd_index[cmd_name] = d

        if isinstance(cmd, commands.Group):
            for subcmd in cmd.commands:
                _process_command(subcmd, cat_id, cat_label)

    for cog in bot.cogs.values():
        meta: Optional[Dict] = getattr(cog.__class__, "COG_META", None)
        if not meta:
            module = sys.modules.get(cog.__class__.__module__)
            meta = getattr(module, "COG_META", None)
        if not meta or not isinstance(meta, dict):
            continue

        if id(meta) in seen_metas:
            continue
        seen_metas.add(id(meta))

        cat_id = str(meta.get("category", cog.__class__.__name__.lower())).strip()
        cat_label = str(meta.get("label", cat_id.title())).strip()
        cat_desc = str(meta.get("desc", "")).strip()
        cat_staff = meta.get("staff", False)
        cat_owner = meta.get("owner", False)

        if cat_owner and not is_owner:
            continue
        if cat_staff and not (is_owner or is_wl):
            continue

        if cat_id not in categories:
            categories[cat_id] = {
                "label": cat_label,
                "desc": cat_desc,
                "staff": cat_staff,
                "owner": cat_owner,
                "sections": {},
            }

        for cmd in cog.get_commands():
            _process_command(cmd, cat_id, cat_label)

    for cat in categories.values():
        for sec in cat["sections"]:
            cat["sections"][sec].sort(key=lambda x: x[0])

    return categories, cmd_index


# ── embed builders (kept for .help <command> direct lookup) ────

_PAGE_CHARS = 1800

def _total_cmds(categories: Dict) -> int:
    return sum(
        len(cmds)
        for cat in categories.values()
        for cmds in cat["sections"].values()
    )

def _build_main_embed(bot: commands.Bot, color: int, categories: Dict) -> discord.Embed:
    desc_lines = [
        "Need help? Ping me and ask — I'll point you to the right command.\n",
        "Select a **category** from the dropdown below to browse commands.\n",
        f"{_cat_icon('general')} **Total commands:** {_total_cmds(categories)}  ·  **Prefix:** `.`\n",
        f"Use `.help <command>` for details on any specific command.",
    ]
    embed = discord.Embed(description="\n".join(desc_lines), color=color)
    embed.set_author(name="Command Help", icon_url=bot.user.display_avatar.url)
    embed.set_footer(text=".help <command> for more info", icon_url=bot.user.display_avatar.url)
    return embed

def _build_hub_embed(bot: commands.Bot, color: int, cat: Dict) -> discord.Embed:
    lines = [f"{_sec_icon(sec)} **{sec}** \u2014 {len(cmds)} command{'s' if len(cmds) != 1 else ''}"
             for sec, cmds in cat["sections"].items() if cmds]
    embed = discord.Embed(description="\n".join(lines), color=color)
    icon = _cat_icon(cat["label"])
    label = f"{icon}  {cat['label']}" if icon else cat["label"]
    embed.set_author(name=label, icon_url=bot.user.display_avatar.url)
    embed.set_footer(text=".help <command> for more info  ·  \u2190 back to categories", icon_url=bot.user.display_avatar.url)
    return embed

def _build_list_embed(
    bot: commands.Bot,
    color: int,
    cat_label: str,
    sec_label: str,
    cmds: List[Tuple[str, Dict]],
    page: int,
) -> Tuple[discord.Embed, int]:
    if not cmds:
        return discord.Embed(description="No commands available.", color=color), 1

    lines = []
    for idx, (cmd_name, d) in enumerate(cmds, start=1):
        usage = d.get("usage", f"`.{cmd_name}`")
        desc = d.get("desc", "")
        badges = []
        if d.get("owner"):
            badges.append("\u265b")
        elif d.get("admin"):
            badges.append("\u2694")
        elif d.get("staff"):
            badges.append("\u2605")
        badge_str = " ".join(badges) + " " if badges else ""
        lines.append(f"**`{idx:02d}.` {badge_str}{usage}**\n{desc}\n\n")

    pages, current = [], ""
    for line in lines:
        if len(current) + len(line) > _PAGE_CHARS:
            pages.append(current.rstrip())
            current = line
        else:
            current += line
    if current:
        pages.append(current.rstrip())

    total = len(pages)
    page = max(0, min(page, total - 1))

    embed = discord.Embed(description=pages[page], color=color)
    embed.set_author(name=f"{cat_label}  \u203a  {sec_label}", icon_url=bot.user.display_avatar.url)
    embed.set_footer(text=f"Page {page+1}/{total}  ·  .help <command> for more info  ·  \u2190 back",
                     icon_url=bot.user.display_avatar.url)
    return embed, total

def _build_detail_embed(
    bot: commands.Bot,
    color: int,
    cmd_name: str,
    d: Dict[str, Any],
    cat_label: str = "",
    sec_label: str = "",
) -> discord.Embed:
    icon = _sec_icon(sec_label) if sec_label else ""
    title = f"{icon} {cat_label}  \u203a  {sec_label}  \u203a  .{cmd_name}" if sec_label else f".{cmd_name}"
    embed = discord.Embed(color=color)
    embed.set_author(name=title, icon_url=bot.user.display_avatar.url)
    usage = d.get("usage", f"`.{cmd_name}`")
    embed.add_field(name="Usage", value=f"```ini\n{usage}\n```", inline=False)
    embed.add_field(name="Description", value=d.get("desc", "No description."), inline=False)
    if d.get("aliases"):
        embed.add_field(name="Aliases", value="\u00b7  ".join(f"`{a}`" for a in d["aliases"]), inline=False)
    perms = []
    if d.get("owner"):
        perms.append("\u265b owner only")
    if d.get("admin"):
        perms.append("\u2694 admin only")
    if d.get("staff"):
        perms.append("\u2605 staff only")
    if perms:
        embed.add_field(name="Permissions", value="\n".join(perms), inline=False)
    embed.set_footer(text=".help <command> for more info  ·  \u2190 back  ·  use dropdown to switch commands",
                     icon_url=bot.user.display_avatar.url)
    return embed

# ── base view ─────────────────────────────────────────────────

class _BaseHelpView(View):
    def __init__(self, ctx, bot: commands.Bot, color: int, is_owner: bool, is_wl: bool):
        super().__init__(timeout=600)
        self.ctx = ctx
        self.bot = bot
        self.color = color
        self.is_owner = is_owner
        self.is_wl = is_wl
        self.message: Optional[discord.Message] = None

    def _check_user(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.ctx.author.id

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    def _cats(self) -> Dict:
        cats, _ = _collect(self.bot, self.is_owner, self.is_wl)
        return cats

    async def _send_embed(self, target, embed: discord.Embed):
        if isinstance(target, discord.Interaction):
            await target.response.edit_message(embed=embed, view=self)
            self.message = target.message
        else:
            self.message = await target.send(embed=embed, view=self)

# ── main menu ─────────────────────────────────────────────────

class MainMenuView(_BaseHelpView):
    def __init__(self, ctx, bot, color, is_owner, is_wl):
        super().__init__(ctx, bot, color, is_owner, is_wl)
        cats = self._cats()
        self.add_item(_CategorySelect(self, cats))

    async def show(self, target):
        cats = self._cats()
        embed = _build_main_embed(self.bot, self.color, cats)
        await self._send_embed(target, embed)


class _CategorySelect(Select):
    def __init__(self, view: MainMenuView, cats: Dict):
        self._view = view
        options = [
            discord.SelectOption(label=cat["label"], value=cat_id, description=cat["desc"][:50])
            for cat_id, cat in cats.items()
            if any(cmds for cmds in cat["sections"].values())
        ]
        if not options:
            options = [discord.SelectOption(label="No commands found", value="__none__")]
        super().__init__(placeholder="Select a category", options=options[:25],
                         min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if not self._view._check_user(interaction):
            return await interaction.response.send_message("not your menu.", ephemeral=True)
        if self.values[0] == "__none__":
            return await interaction.response.send_message(
                "no cogs have `COG_META` defined yet \u2014 add the metadata snippets to your cogs first.",
                ephemeral=True,
            )
        cats = self._view._cats()
        cat_id = self.values[0]
        cat = cats[cat_id]

        if len(cat["sections"]) == 1:
            sec_label = next(iter(cat["sections"]))
            cmds = cat["sections"][sec_label]
            first_cmd, _ = cmds[0]
            detail = DetailView(
                self._view.ctx, self._view.bot, self._view.color,
                self._view.is_owner, self._view.is_wl,
                cat_id, cat, sec_label, cmds, first_cmd,
            )
            detail.message = self._view.message
            await detail.show(interaction)
        else:
            hub = HubView(self._view.ctx, self._view.bot, self._view.color,
                          self._view.is_owner, self._view.is_wl, cat_id, cat)
            hub.message = self._view.message
            await hub.show(interaction)

# ── hub view (section picker) ─────────────────────────────────

class HubView(_BaseHelpView):
    def __init__(self, ctx, bot, color, is_owner, is_wl, cat_id: str, cat: Dict):
        super().__init__(ctx, bot, color, is_owner, is_wl)
        self.cat_id = cat_id
        self.cat = cat
        self.add_item(_BackButton(self, "main"))
        self.add_item(_SectionSelect(self, cat))

    async def show(self, target):
        embed = _build_hub_embed(self.bot, self.color, self.cat)
        await self._send_embed(target, embed)


class _SectionSelect(Select):
    def __init__(self, view: HubView, cat: Dict):
        self._view = view
        options = [
            discord.SelectOption(label=sec, value=sec)
            for sec, cmds in cat["sections"].items()
            if cmds
        ]
        if not options:
            options = [discord.SelectOption(label="No sections", value="__none__")]
        super().__init__(placeholder="Select a section", options=options[:25],
                         min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if not self._view._check_user(interaction):
            return await interaction.response.send_message("not your menu.", ephemeral=True)
        sec_label = self.values[0]
        cmds = self.cat["sections"][sec_label]
        if not cmds:
            return await interaction.response.send_message("No commands here.", ephemeral=True)
        first_cmd, _ = cmds[0]
        detail = DetailView(
            self._view.ctx, self._view.bot, self._view.color,
            self._view.is_owner, self._view.is_wl,
            self._view.cat_id, self._view.cat,
            sec_label, cmds, first_cmd,
        )
        detail.message = self._view.message
        await detail.show(interaction)

    @property
    def cat(self):
        return self._view.cat

# ── list view (paginated) ─────────────────────────────────────

class ListView(_BaseHelpView):
    def __init__(self, ctx, bot, color, is_owner, is_wl,
                 cat_id: str, cat: Dict, sec_label: str, cmds, page: int):
        super().__init__(ctx, bot, color, is_owner, is_wl)
        self.cat_id = cat_id
        self.cat = cat
        self.sec_label = sec_label
        self.cmds = cmds
        self.page = page
        self.per_page = 8
        self.total = max(1, (len(cmds) - 1) // self.per_page + 1)
        self.add_item(_BackButton(self, "hub"))
        if self.total > 1:
            self.add_item(_PagePrev(self))
            self.add_item(_PageNext(self))
        self.add_item(_ListDetailSelect(self))

    async def show(self, target):
        embed, _ = _build_list_embed(
            self.bot, self.color,
            f"{self.cat['label']}  \u203a  {self.sec_label}",
            self.sec_label, self.cmds, self.page,
        )
        await self._send_embed(target, embed)

    async def go_to_page(self, interaction: discord.Interaction, new_page: int):
        new_view = ListView(
            self.ctx, self.bot, self.color, self.is_owner, self.is_wl,
            self.cat_id, self.cat, self.sec_label, self.cmds, new_page,
        )
        new_view.message = self.message
        await new_view.show(interaction)


class _ListDetailSelect(Select):
    def __init__(self, view: ListView):
        self._view = view
        options = []
        seen: set[str] = set()
        for cmd, _ in view.cmds:
            if cmd in seen:
                continue
            seen.add(cmd)
            options.append(discord.SelectOption(label=f".{cmd}", value=cmd))
            if len(options) >= 25:
                break
        super().__init__(placeholder="View command details...", options=options,
                         min_values=1, max_values=1, row=1)

    async def callback(self, interaction: discord.Interaction):
        if not self._view._check_user(interaction):
            return await interaction.response.send_message("not your menu.", ephemeral=True)
        cmd_name = self.values[0]
        detail = DetailView(
            self._view.ctx, self._view.bot, self._view.color,
            self._view.is_owner, self._view.is_wl,
            self._view.cat_id, self._view.cat,
            self._view.sec_label, self._view.cmds, cmd_name,
            came_from_list_page=self._view.page,
        )
        detail.message = self._view.message
        await detail.show(interaction)

# ── detail view ───────────────────────────────────────────────

class DetailView(_BaseHelpView):
    def __init__(self, ctx, bot, color, is_owner, is_wl,
                 cat_id: str, cat: Dict, sec_label: str,
                 cmds: List, cmd_name: str,
                 came_from_list_page: Optional[int] = None):
        super().__init__(ctx, bot, color, is_owner, is_wl)
        self.cat_id = cat_id
        self.cat = cat
        self.sec_label = sec_label
        self.cmds = cmds
        self.cmd_name = cmd_name
        self.came_from_list_page = came_from_list_page
        if came_from_list_page is not None:
            self.add_item(_BackButton(self, "list"))
        elif len(self.cat["sections"]) <= 1:
            self.add_item(_BackButton(self, "main"))
        else:
            self.add_item(_BackButton(self, "hub"))
        self.add_item(_DetailSwitch(self))

    async def show(self, target):
        d = next((d for c, d in self.cmds if c == self.cmd_name), {})
        embed = _build_detail_embed(self.bot, self.color, self.cmd_name, d,
                                    self.cat["label"], self.sec_label)
        await self._send_embed(target, embed)


class _DetailSwitch(Select):
    def __init__(self, view: DetailView):
        self._view = view
        options = []
        seen: set[str] = set()
        for cmd, _ in view.cmds:
            if cmd in seen:
                continue
            seen.add(cmd)
            options.append(
                discord.SelectOption(label=f".{cmd}", value=cmd, default=(cmd == view.cmd_name))
            )
            if len(options) >= 25:
                break
        super().__init__(placeholder="Switch command...", options=options,
                         min_values=1, max_values=1, row=1)

    async def callback(self, interaction: discord.Interaction):
        if not self._view._check_user(interaction):
            return await interaction.response.send_message("not your menu.", ephemeral=True)
        new_cmd = self.values[0]
        new_view = DetailView(
            self._view.ctx, self._view.bot, self._view.color,
            self._view.is_owner, self._view.is_wl,
            self._view.cat_id, self._view.cat,
            self._view.sec_label, self._view.cmds, new_cmd,
            came_from_list_page=self._view.came_from_list_page,
        )
        new_view.message = self._view.message
        await new_view.show(interaction)

# ── shared nav buttons ────────────────────────────────────────

class _BackButton(Button):
    def __init__(self, view: _BaseHelpView, dest: str):
        super().__init__(style=discord.ButtonStyle.gray, label="\u2190", row=0)
        self._view = view
        self.dest = dest

    async def callback(self, interaction: discord.Interaction):
        if not self._view._check_user(interaction):
            return await interaction.response.send_message("not your menu.", ephemeral=True)

        cats = self._view._cats()

        if self.dest == "main":
            new = MainMenuView(self._view.ctx, self._view.bot, self._view.color,
                               self._view.is_owner, self._view.is_wl)
            new.message = self._view.message
            await new.show(interaction)

        elif self.dest == "hub":
            cat_id = self._view.cat_id
            cat = cats.get(cat_id, self._view.cat)
            new = HubView(self._view.ctx, self._view.bot, self._view.color,
                          self._view.is_owner, self._view.is_wl, cat_id, cat)
            new.message = self._view.message
            await new.show(interaction)

        elif self.dest == "list":
            page = getattr(self._view, "came_from_list_page", 0) or 0
            new = ListView(
                self._view.ctx, self._view.bot, self._view.color,
                self._view.is_owner, self._view.is_wl,
                self._view.cat_id, self._view.cat,
                self._view.sec_label, self._view.cmds, page,
            )
            new.message = self._view.message
            await new.show(interaction)


class _PagePrev(Button):
    def __init__(self, view: ListView):
        super().__init__(style=discord.ButtonStyle.gray, label="\u25c0", row=0)
        self._view = view

    async def callback(self, interaction: discord.Interaction):
        if not self._view._check_user(interaction):
            return await interaction.response.send_message("not your menu.", ephemeral=True)
        await self._view.go_to_page(interaction, (self._view.page - 1) % self._view.total)


class _PageNext(Button):
    def __init__(self, view: ListView):
        super().__init__(style=discord.ButtonStyle.gray, label="\u25b6", row=0)
        self._view = view

    async def callback(self, interaction: discord.Interaction):
        if not self._view._check_user(interaction):
            return await interaction.response.send_message("not your menu.", ephemeral=True)
        await self._view.go_to_page(interaction, (self._view.page + 1) % self._view.total)

# ── cog ───────────────────────────────────────────────────────

class HelpCog(commands.Cog, name="Help"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="help", aliases=["h"])
    async def help_command(self, ctx: commands.Context, *, command: str = None):
        config = load_json(CONFIG_FILE)
        guild_id = ctx.guild.id if ctx.guild else 0
        guild_config = config.get(str(guild_id), {})
        whitelist = guild_config.get("whitelist", [])
        is_owner = is_owner_or_creator(ctx)
        is_wl = str(ctx.author.id) in whitelist
        color = get_embed_color(guild_id)

        if command:
            cmd = command.lower().lstrip(".")
            _, cmd_index = _collect(self.bot, is_owner, is_wl)

            if cmd in cmd_index:
                d = cmd_index[cmd]
                cat_label = d.get("_cat_label", "")
                sec_label = d.get("section", "")
                embed = _build_detail_embed(self.bot, color, cmd, d, cat_label, sec_label)
                return await ctx.send(embed=embed)

            real_cmd = self.bot.get_command(cmd)
            if real_cmd:
                embed = discord.Embed(color=color)
                embed.set_author(name=f".{real_cmd.name}", icon_url=self.bot.user.display_avatar.url)
                usage = (f"`.{real_cmd.name} {real_cmd.signature}`"
                         if real_cmd.signature else f"`.{real_cmd.name}`")
                embed.add_field(name="Usage", value=usage, inline=False)
                embed.add_field(name="Description", value=real_cmd.help or "No description.", inline=False)
                if real_cmd.aliases:
                    embed.add_field(name="Aliases",
                                    value=" \u00b7 ".join(f"`{a}`" for a in real_cmd.aliases), inline=False)
                embed.add_field(name="Category", value=real_cmd.cog_name or "Unknown", inline=False)
                return await ctx.send(embed=embed)

            return await ctx.send(
                f"Command `{cmd}` not found. Use `.help` to browse all commands."
            )

        async with ctx.typing():
            view = MainMenuView(ctx, self.bot, color, is_owner, is_wl)
            await view.show(ctx)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
