# Command Systems Spec

**Date:** 2026-08-11
**Status:** Approved
**Modules:** `utils.py`, `cogs/leveling.py`, `tests/test_disabled.py`

## Goal

Extend the per-guild `.disable`/`.enable` system with a **systems registry**: named families of commands (e.g. `reactionlb`) that can be toggled as a unit, with aliases, and support `default_disabled` systems (off for every guild until explicitly enabled).

## Registry (utils.py)

```python
COMMAND_SYSTEMS = {
    "reactionlb": {
        "aliases": ["rclb", "reactionleaderboards"],
        "commands": ["rtop", "rctop", "rc", "lb emojis"],
        "default_disabled": True,
    },
}
```

- `commands` are canonical qualified names (subcommand names with spaces, e.g. `"lb emojis"`).
- Helpers:
  - `resolve_system(name: str) -> str | None` — case-insensitive; matches canonical name or any alias.
  - `system_commands(name: str) -> list[str]` — the commands of a canonical system name.

## Storage

- Existing `data/disabled_commands.json`: `{guild_id: [entries]}` — entries may now be command names **or system names**.
- New `data/enabled_commands.json`: `{guild_id: [entries]}` — explicit re-enables (command names or system names). Same helper family: `get_enabled_commands(guild_id)`, `add_enabled_command(guild_id, name)`, `remove_enabled_command(guild_id, name)`.

## `is_command_disabled(guild_id, qualified)` — precedence

1. **Guild disabled list wins:** for each entry, if it's a system name, block when `qualified` equals or prefixes (entry + " ") any of the system's commands; if it's a command name, block on the existing `== / startswith(name + " ")` match.
2. **Guild enabled list overrides:** same resolution (systems expand); if matched → not disabled (skip to allowed).
3. **Default-disabled systems:** if `qualified` belongs to a `default_disabled` system's commands → disabled.
4. Otherwise → not disabled.

A command can belong to at most one system. Systems may nest commands that are also individually disableable — the enabled list (step 2) resolves before defaults (step 3), so `.enable rtop` works even while `reactionlb` is default-disabled.

## Commands (cogs/leveling.py)

- `.disable <system-or-command>`:
  - `level` → unchanged special case (creator-only global flag).
  - System name (via `resolve_system`) → `add_disabled_command(guild_id, system_name)` AND `remove_enabled_command(guild_id, system_name)`; react redlotus. Message stays silent (reaction-only) per existing behavior.
  - Command name → existing behavior, PLUS `remove_enabled_command(guild_id, cmd)` so re-disabling an individually-enabled command of a default-disabled system works.
- `.enable <system-or-command>`:
  - `level` → unchanged.
  - System name → `add_enabled_command(guild_id, system_name)` AND `remove_disabled_command(guild_id, system_name)`; react pinklotus.
  - Command name → existing behavior, PLUS `add_enabled_command(guild_id, cmd)` when the command belongs to a `default_disabled` system (so it stays usable), or `remove_enabled_command` otherwise (cleanup).
- No-arg list output (`_disabled_list_text`): effective disabled = entries in the guild's disabled list (system entries shown by their name, prefixed with `.`) + commands of default-disabled systems not covered by the guild's enabled list. Format stays `-# disabled here: .reactionlb, .rtop` — system names appear as-is; commands with the dot prefix. Empty → `-# nothing disabled here.`
- Exempt list unchanged: `disable`, `enable`, `help`.

## Out of Scope

- New systems beyond `reactionlb` (registry is ready for more).
- Changing `level` semantics (stays creator-only bot-global).
- Caching the enabled/disabled lists.
- Slash commands.
