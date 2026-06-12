from __future__ import annotations

import discord
from discord.ui import Button, Modal, Select, TextInput, View

from neixoconfig import Neixocolor

# ── Role mapping modal ───────────────────────────────────────────

class RoleSlotModal(Modal, title="Add Role Slot"):
    slot_name = TextInput(
        label="Slot name (e.g. Owner, Head of Security)",
        placeholder="Owner",
        max_length=40,
    )

    def __init__(self, callback):
        super().__init__()
        self._cb = callback

    async def on_submit(self, interaction: discord.Interaction):
        await self._cb(interaction, self.slot_name.value.strip())

# ── Role picker view ─────────────────────────────────────────────

class RolePickerView(View):
    def __init__(self, guild: discord.Guild, slot_name: str, author_id: int, on_pick):
        super().__init__(timeout=60)
        self.slot_name = slot_name
        self.author_id = author_id
        self._cb = on_pick

        options = [
            discord.SelectOption(label=r.name[:100], value=str(r.id))
            for r in reversed(guild.roles)
            if r.name != "@everyone"
        ][:25]  # Discord max is 25 select options

        self.select = Select(
            placeholder=f"Pick the role for slot: {slot_name}",
            options=options,
        )
        self.select.callback = self._selected
        self.add_item(self.select)

        skip = Button(label="Skip this slot", style=discord.ButtonStyle.gray)
        skip.callback = self._skip
        self.add_item(skip)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    async def _selected(self, interaction: discord.Interaction):
        role_id = int(self.select.values[0])
        await self._cb(interaction, self.slot_name, role_id)
        self.stop()

    async def _skip(self, interaction: discord.Interaction):
        await self._cb(interaction, self.slot_name, None)
        self.stop()

# ── Confirmation view ────────────────────────────────────────────

class ConfirmView(View):
    def __init__(self, author_id: int):
        super().__init__(timeout=60)
        self.confirmed = False
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    @discord.ui.button(emoji="<:7079verifiedblacksimplified:1255031445806780467>", style=discord.ButtonStyle.gray)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        self.confirmed = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(emoji="<:Blackkatana:1252608867876212778>", style=discord.ButtonStyle.gray)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        self.stop()

# ── Preview view ─────────────────────────────────────────────────

class PreviewView(View):
    def __init__(
        self,
        author_id: int,
        op_label: str,
        changes: list[tuple[str, str]],
        warn: str = "",
    ):
        super().__init__(timeout=60)
        self.author_id  = author_id
        self.op_label   = op_label
        self.changes    = changes
        self.warn       = warn
        self.confirmed  = False

        if len(changes) > 3:
            show_more = Button(label="show more", style=discord.ButtonStyle.gray)
            show_more.callback = self._show_more
            self.add_item(show_more)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    def build_embed(self) -> discord.Embed:
        lines = []
        if self.warn:
            lines.append(self.warn)
        for before, after in self.changes[:3]:
            lines.append(f"`{before}` → `{after}`")
        if len(self.changes) > 3:
            lines.append(f"-# ... and {len(self.changes) - 3} more — click show more")
        e = discord.Embed(
            title=f"preview: {self.op_label}",
            description="\n".join(lines),
            color=Neixocolor,
        )
        e.set_footer(text=f"{len(self.changes)} channel(s) affected")
        return e

    @discord.ui.button(emoji="<:7079verifiedblacksimplified:1255031445806780467>", style=discord.ButtonStyle.gray)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        self.confirmed = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(emoji="<:Blackkatana:1252608867876212778>", style=discord.ButtonStyle.gray)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        self.stop()

    async def _show_more(self, interaction: discord.Interaction):
        await interaction.response.defer()
        pag = PreviewPaginatorView(self.author_id, self.op_label, self.changes)
        embed = pag.build_embed()
        await interaction.followup.send(embed=embed, view=pag, ephemeral=True)

# ── Preview paginator ────────────────────────────────────────────

class PreviewPaginatorView(View):
    def __init__(self, author_id: int, op_label: str, changes: list[tuple[str, str]]):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.op_label  = op_label
        self.changes   = changes
        self.page      = 0
        self.per_page  = 10
        self.total     = max(1, (len(changes) + self.per_page - 1) // self.per_page)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    def build_embed(self) -> discord.Embed:
        start = self.page * self.per_page
        chunk = self.changes[start:start + self.per_page]
        lines = [f"`{b}` → `{a}`" for b, a in chunk]
        e = discord.Embed(
            title=f"full preview: {self.op_label}",
            description="\n".join(lines),
            color=Neixocolor,
        )
        e.set_footer(text=f"page {self.page + 1}/{self.total} · {len(self.changes)} total")
        return e

    @discord.ui.button(label="◀", style=discord.ButtonStyle.gray)
    async def prev_btn(self, interaction: discord.Interaction, button: Button):
        if self.page > 0:
            self.page -= 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.gray)
    async def next_btn(self, interaction: discord.Interaction, button: Button):
        if self.page < self.total - 1:
            self.page += 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)
