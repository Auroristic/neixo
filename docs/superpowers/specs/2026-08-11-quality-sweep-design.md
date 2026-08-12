# Quality Sweep: Disable, Welcome Test, Digest Now, Help Overhaul

**Date:** 2026-08-11
**Status:** Approved
**Modules:** `neixo.py`, `cogs/leveling.py`, `cogs/welcome.py`, `cogs/digest.py`, `utils.py`, all cogs (help sweep), `.agents/AGENTS.md`

## Goal

Four improvements: (1) generic per-guild `.disable <command>` / `.enable <command>`; (2) `.welcome test` preview card; (3) `.digest now` on-demand stats card; (4) overhaul help descriptions across **every** command in **every** cog, plus an AGENTS.md rule that future commands carry complete help metadata.

## 1. Generic per-guild disable/enable

### Storage
- New file `DATA_DIR/disabled_commands.json`, shape `{guild_id_str: [qualified_command_name, ...]}`.
- New utils helpers:
  - `get_disabled_commands(guild_id) -> list[str]`
  - `add_disabled_command(guild_id, name) -> None`
  - `remove_disabled_command(guild_id, name) -> None`
- Follows the existing JSON-config pattern (`load_json`/`save_json` in utils).

### Enforcement
- New bot-level check `_disabled_command_check(ctx)` in `neixo.py`, registered via `self.add_check(...)` in `setup_hook` right after `_channel_rule_check` registration.
- Logic: guild-only; look up `ctx.command.qualified_name` in the guild's disabled list; if present, send `-# \`.<qualified>\` is disabled.` and return `False` (the `on_command_error` handler already swallows `CheckFailure` silently since check funcs message the user themselves).
- Must run AFTER `_channel_rule_check` is fine — checks are independent; each returns True to continue.
- Subcommands: `ctx.command.qualified_name` naturally covers `lb emojis`, `welcome setup`, etc. Disabling a group name (e.g. `welcome`) blocks the group AND every subcommand since `qualified_name` starts with the group name — the check must therefore match both exact names and prefix matches (`qualified == name or qualified.startswith(name + " ")`).

### Commands (expand in place, `cogs/leveling.py`)
- **Argument parsing:** both commands use a greedy keyword-only capture — `async def disable_cmd(self, ctx, *, command: str = None)` (same for `enable_cmd`). `.disable welcome test` therefore arrives as `command == "welcome test"` — full multi-word subcommand names work. No single-word converter.
- **Explicit dispatch branch** (identical shape for both commands):

```python
# 1. level = the existing bot-global leveling system flag (owner/creator only)
if command == "level":
    if not is_creator(ctx.author.id):
        return await ctx.send("creator only")
    # toggle self._leveling_disabled + persist '__global__' row in leveling_settings
    # (exact existing behavior — unchanged)
    return

# 2. generic per-guild command disable (any other name)
#    permission: guild owner, creator, or whitelisted
#    resolve via bot.get_command(name); not found -> "no command called that"
#    exempt: disable / enable / help
#    persist in guild's disabled list, react redlotus (disable) / pinklotus (enable)
```

  `.enable level` routes through branch 1 identically — never the generic path.
- `.disable <command>` — new behavior for any other name:
  - Permission: **guild owner (`ctx.guild.owner_id == ctx.author.id`), creator (`is_creator`), or whitelisted** (`str(ctx.author.id)` in the guild config's `whitelist` — same source help.py uses). Deny → `-# no perms`.
  - Resolve `command` via `self.bot.get_command(name)` (also try `get_aliases()` for custom aliases); not found → `-# no command called that`.
  - Exempt: `disable`, `enable`, `help` → `-# can't disable that`.
  - Success: add to guild list, persist, react `<:redlotus:1263556248310386800>` (disable is a "disable" action per AGENTS.md).
  - No argument: list disabled commands for the guild. **Formatting:** each entry printed as `.<qualified name>` with the literal dot prefix and the multi-word name with spaces — e.g. `-# disabled here: .welcome test, .lb emojis` (never underscore-joined, never missing the dot). Empty → `-# nothing disabled here.`
- `.enable <command>` — mirror image; success react `<:pinklotus:1263556545686405170>`; no argument → same list output as disable.
- Both commands keep their `help_meta` updated (usage now `.disable <command>` · `.enable <command>`).

## 2. `.welcome test`

- New subcommand on the `welcome` group in `cogs/welcome.py`: `welcome_test(self, ctx)`.
- Anyone can use (same as `.welcome status`).
- **Cooldown:** `@commands.cooldown(1, 10, commands.BucketType.user)` — 1 use per 10 seconds per user. The bot's `on_command_error` already renders `CommandOnCooldown` as `-# slow down, try again in Xs`, so no extra handling needed. Prevents avatar/banner fetch + image render spam.
- Pipeline: fetch `ctx.author.display_avatar.url` and the guild banner (same `_get` + `aiohttp` pattern as `on_member_join`), then `asyncio.to_thread(_render_welcome_card, avatar, banner, guild.name, ctx.author.display_name, guild.member_count)`.
- Send `discord.File(fp=buf, filename="welcome.png")` in the invoking channel, no text.
- `help_meta`: usage `.welcome test`, desc "Previews the welcome card with your own avatar.", section General, note "anyone can preview — does not change settings."
- Extract the shared avatar/banner fetch from `on_member_join` into a module helper `_fetch_member_art(member) -> tuple[bytes | None, bytes | None]` used by both paths (DRY).

## 3. `.digest now`

- `cogs/digest.py`: `_baselines(gid_str, conf)` gains keyword param `update: bool = True`. When `False`, compute deltas exactly as today but do NOT write the new baselines back into `conf`/state. All existing callers unaffected.
- New `digest_now` subcommand on the `digest` group:
  - Admin only (`self._admin(ctx)`), guild-only.
  - Compute: `_baselines(gid_str, conf, update=False)`, then build rows with the same `_name`/sorting logic as `_run_digest`.
  - Render via `_render_digest_card` with icon fetch (same pattern as `_run_digest`), week label `week of <month day>`.
  - Send `discord.File(fp=buf, filename="digest.png")` in the **invoking** channel.
  - No digest config / no baselines yet → card renders with whatever deltas exist (zeros on first-ever run). Honest preview; no baseline is created by `now`.
  - `help_meta`: usage `.digest now`, desc "Sends the current week's digest card on demand.", section General, note "admin only. shows the same stats as the sunday card — does not affect the scheduled run."
- Refactor: extract the shared "compute rows + render" body of `_run_digest` into `_build_digest_file(guild, conf, update: bool) -> io.BytesIO | None` used by both `_run_digest` (update=True) and `digest_now` (update=False) — avoids duplicating the ~35-line compute/render block. `_run_digest` keeps its post-channel-send behavior.

## 4. Help overhaul (all cogs) + AGENTS.md

### Description sweep
- Every command in **every cog** gets its `help_meta` reviewed and improved: accurate `usage` (with real prefix `.`), a one-line lowercase `desc` that says what the command actually does, `section` set, `examples` where useful, `params` documented, `note` for restrictions.
- Style follows the bot voice (lowercase, casual) but descriptions stay informative (this is documentation, not chatter).
- Commands already complete (e.g. snipe suite, `.welcome setup`) may need only touch-ups — the sweep is quality-driven, not "change everything".
- Do not change command behavior, aliases, signatures, or permission flags in this sweep. Metadata only.
- The bot's existing startup warning (missing `@help_meta`) is the compliance signal: after the sweep, `grep` for `@commands.command` without a following/adjacent `@help_meta` returns zero in every cog.

### Quality bar (applies uniformly to every command, early or late in the sweep)
1. **`desc`:** one line, ≤ 100 characters, all lowercase, starts with a verb or short phrase ("shows...", "sends...", "turns...").
2. **`usage`:** always present, starts with `.`, matches the real signature (greedy `*` args shown as `<text>`).
3. **`section`:** always set; uses the cog's existing section label when the cog already has one.
4. **`examples`:** only where genuinely useful — ≤ 3, with a real `.` prefix; omit for trivial commands.
5. **`params`:** one entry per documented argument; `required` reflects the actual default (no default = True, has default = False).
6. **`note`:** restrictions (admin/owner/staff/cooldown), or the `{user}`-style placeholder explanations when relevant; omit when nothing to add.
7. **Every command decorated with `@help_meta`** — zero commands missing it after the sweep (the startup dev-warning must print an empty list).

### AGENTS.md patch (`.agents/AGENTS.md`)
- New section "Command Help Metadata": every new or modified command MUST ship complete `help_meta` (usage, desc, section, examples where applicable, params for all arguments, note for restrictions); when touching a command, verify `.help <command>` renders sensibly; descriptions must match the bot voice but stay accurate and informative; never leave a command without `@help_meta`.

## Out of Scope

- Slash commands.
- Changing the leveling system flag's storage (stays in `leveling_settings`).
- Per-channel behavior of disabled commands (existing `cmd_channel_rules` handles that separately).
- Hiding disabled commands from `.help` (they stay visible).
- Editing command behavior/signatures in the help sweep.
