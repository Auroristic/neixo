# Command Systems Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a systems registry to the per-guild disable/enable feature so families of commands (reactionlb) can be toggled as a unit and start disabled for every guild.

**Architecture:** `utils.py` gains `COMMAND_SYSTEMS`, `resolve_system`, `system_commands`, and an enabled-commands store (`data/enabled_commands.json`) with `get/add/remove_enabled_command` helpers. `is_command_disabled` gets 4-step precedence (disabled-list → enabled-list → default-disabled systems → allowed). `cogs/leveling.py`'s disable/enable commands resolve system names before command names.

**Tech Stack:** Python 3.11+, discord.py, pytest.

**Spec:** `docs/superpowers/specs/2026-08-11-command-systems-design.md`

## Global Constraints

- `COMMAND_SYSTEMS` exactly: `{"reactionlb": {"aliases": ["rclb", "reactionleaderboards"], "commands": ["rtop", "rctop", "rc", "lb emojis"], "default_disabled": True}}`
- Entries in disabled/enabled JSON can be command names OR system names.
- Precedence: guild disabled list → guild enabled list → default-disabled systems → allowed.
- `is_command_disabled` matching per entry: system entries expand to their commands, each matched with `== name or startswith(name + " ")`; command entries use the same existing rule.
- `.disable level` / `.enable level` unchanged (creator-only bot-global flag).
- Bot voice: `-# ` prefixes, reactions redlotus (disable) / pinklotus (enable).
- Tests run on muixo (commit → push → `ssh muixo "cd /home/ubuntu/neiXO && git pull && venv/bin/python -m pytest -q"`).
- The `isolate_data_dir` fixture in tests/test_disabled.py must pin `utils.DATA_DIR`, `utils.DB_FILE`, reset caches, and create the leveling table — extend it for the new enabled-commands file if needed (it is JSON-based, covered by DATA_DIR pinning).

---

### Task 1: Registry + storage + precedence + commands

**Files:**
- Modify: `utils.py` (COMMAND_SYSTEMS, resolve_system, system_commands, enabled-commands helpers, is_command_disabled rewrite)
- Modify: `cogs/leveling.py` (disable_cmd/enable_cmd system resolution, _disabled_list_text effective output)
- Test: `tests/test_disabled.py` (extend)

**Interfaces:**
- Produces:
  - `utils.COMMAND_SYSTEMS: dict[str, dict]`
  - `utils.resolve_system(name: str) -> str | None`
  - `utils.system_commands(name: str) -> list[str]`
  - `utils.get_enabled_commands(guild_id) -> list[str]`, `utils.add_enabled_command(guild_id, name)`, `utils.remove_enabled_command(guild_id, name)` (mirror the disabled helpers; file `f"{DATA_DIR}/enabled_commands.json"` computed at call time)
  - `utils.is_command_disabled(guild_id, qualified)` — new precedence
  - `Leveling.disable_cmd` / `Leveling.enable_cmd` — system-aware
  - `Leveling._disabled_list_text` — effective-disabled output

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_disabled.py`:

```python
def test_resolve_system_by_name_and_alias():
    from utils import resolve_system

    assert resolve_system('reactionlb') == 'reactionlb'
    assert resolve_system('rclb') == 'reactionlb'
    assert resolve_system('REACTIONLEADERBOARDS') == 'reactionlb'
    assert resolve_system('rtop') is None


def test_system_commands():
    from utils import system_commands

    assert system_commands('reactionlb') == ['rtop', 'rctop', 'rc', 'lb emojis']
    assert system_commands('nope') == []


def test_default_disabled_system_blocked_without_enable():
    from utils import is_command_disabled

    # no guild state at all — default_disabled applies
    assert is_command_disabled(111, 'rtop') is True
    assert is_command_disabled(111, 'lb emojis') is True
    assert is_command_disabled(111, 'snipe') is False


def test_enabled_system_overrides_default():
    from utils import add_enabled_command, is_command_disabled

    add_enabled_command(111, 'reactionlb')
    assert is_command_disabled(111, 'rtop') is False
    assert is_command_disabled(111, 'rctop') is False
    assert is_command_disabled(222, 'rtop') is True  # other guild unaffected


def test_enabled_single_command_overrides_default():
    from utils import add_enabled_command, is_command_disabled

    add_enabled_command(111, 'rtop')
    assert is_command_disabled(111, 'rtop') is False
    assert is_command_disabled(111, 'rctop') is True


def test_disabled_system_beats_enabled():
    from utils import add_disabled_command, add_enabled_command, is_command_disabled

    add_enabled_command(111, 'reactionlb')
    add_disabled_command(111, 'reactionlb')
    assert is_command_disabled(111, 'rtop') is True


def test_disable_system_command():
    from cogs.leveling import Leveling

    cog = Leveling(None)
    cog.bot = SimpleNamespace(get_command=lambda n: {'rtop': SimpleNamespace(qualified_name='rtop')}.get(n))
    ctx = FakeCtx(owner_id=99, author_id=99)
    await cog.disable_cmd(ctx, command='reactionlb')
    from utils import get_disabled_commands

    assert 'reactionlb' in get_disabled_commands(111)
    assert ctx.reacted == ['<:redlotus:1263556248310386800>']


def test_enable_system_command():
    from cogs.leveling import Leveling
    from utils import add_disabled_command

    add_disabled_command(111, 'reactionlb')
    cog = Leveling(None)
    cog.bot = SimpleNamespace(get_command=lambda n: {'rtop': SimpleNamespace(qualified_name='rtop')}.get(n))
    ctx = FakeCtx(owner_id=99, author_id=99)
    await cog.enable_cmd(ctx, command='rclb')  # alias
    from utils import get_disabled_commands, get_enabled_commands

    assert 'reactionlb' not in get_disabled_commands(111)
    assert 'reactionlb' in get_enabled_commands(111)
    assert ctx.reacted == ['<:pinklotus:1263556545686405170>']


def test_disable_no_args_lists_default_disabled_systems():
    from cogs.leveling import Leveling

    cog = Leveling(None)
    ctx = FakeCtx(owner_id=99, author_id=99)
    await cog.disable_cmd(ctx)
    text = ctx.sent[0][0][0]
    assert '.reactionlb' in text
    assert 'nothing disabled' not in text


def test_level_branch_still_creator_only():
    from cogs.leveling import Leveling

    cog = Leveling(None)
    ctx = FakeCtx(author_id=777)  # not creator
    await cog.disable_cmd(ctx, command='level')
    assert ctx.sent[0][0][0] == 'creator only'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ssh muixo "cd /home/ubuntu/neiXO && git pull && venv/bin/python -m pytest tests/test_disabled.py -v"`
Expected: FAIL — resolve_system/system_commands/enabled helpers missing; default-disabled behavior absent.

- [ ] **Step 3: Implement utils.py**

Add after the disabled-commands section:

```python
# ── Command systems ───────────────────────────────────────────

COMMAND_SYSTEMS = {
    "reactionlb": {
        "aliases": ["rclb", "reactionleaderboards"],
        "commands": ["rtop", "rctop", "rc", "lb emojis"],
        "default_disabled": True,
    },
}


def resolve_system(name: str) -> str | None:
    """Canonical system name for a typed name/alias, or None."""
    key = (name or '').lower().strip()
    for sys_name, sys in COMMAND_SYSTEMS.items():
        if key == sys_name or key in sys.get('aliases', []):
            return sys_name
    return None


def system_commands(name: str) -> list[str]:
    sys = COMMAND_SYSTEMS.get(name)
    return list(sys.get('commands', [])) if sys else []


def system_default_disabled(name: str) -> bool:
    sys = COMMAND_SYSTEMS.get(name)
    return bool(sys and sys.get('default_disabled'))
```

Add the enabled-commands store helpers next to the disabled ones:

```python
def _enabled_file() -> str:
    return f"{DATA_DIR}/enabled_commands.json"


def get_enabled_commands(guild_id: int | str) -> list[str]:
    return list((load_json(_enabled_file()) or {}).get(str(guild_id), []))


def add_enabled_command(guild_id: int | str, name: str) -> None:
    state = load_json(_enabled_file()) or {}
    names = state.setdefault(str(guild_id), [])
    if name not in names:
        names.append(name)
    save_json(_enabled_file(), state)


def remove_enabled_command(guild_id: int | str, name: str) -> None:
    state = load_json(_enabled_file()) or {}
    names = state.get(str(guild_id), [])
    if name in names:
        names.remove(name)
    if not names:
        state.pop(str(guild_id), None)
    save_json(_enabled_file(), state)
```

Rewrite `is_command_disabled`:

```python
def _entry_matches(entry: str, qualified: str) -> bool:
    sys_name = resolve_system(entry)
    if sys_name:
        return any(qualified == c or qualified.startswith(c + ' ') for c in system_commands(sys_name))
    return qualified == entry or qualified.startswith(entry + ' ')


def is_command_disabled(guild_id: int | str, qualified: str) -> bool:
    # 1. explicit per-guild disabled entries win
    if any(_entry_matches(e, qualified) for e in get_disabled_commands(guild_id)):
        return True
    # 2. explicit per-guild enabled entries override defaults
    if any(_entry_matches(e, qualified) for e in get_enabled_commands(guild_id)):
        return False
    # 3. default-disabled systems block unless overridden above
    for sys_name, sys in COMMAND_SYSTEMS.items():
        if sys.get('default_disabled') and any(
            qualified == c or qualified.startswith(c + ' ') for c in sys.get('commands', [])
        ):
            return True
    # 4. otherwise allowed
    return False
```

- [ ] **Step 4: Implement cogs/leveling.py**

In `disable_cmd`, after the level branch and the `command is None` list check, insert system resolution BEFORE the generic command path:

```python
        from utils import resolve_system, remove_enabled_command
        sys_name = resolve_system(command)
        if sys_name:
            from utils import add_disabled_command
            add_disabled_command(ctx.guild.id, sys_name)
            remove_enabled_command(ctx.guild.id, sys_name)
            return await ctx.message.add_reaction("<:redlotus:1263556248310386800>")
```

In `enable_cmd`, mirror it (after the level branch and the `command is None` check):

```python
        from utils import resolve_system, add_enabled_command
        sys_name = resolve_system(command)
        if sys_name:
            from utils import remove_disabled_command
            remove_disabled_command(ctx.guild.id, sys_name)
            add_enabled_command(ctx.guild.id, sys_name)
            return await ctx.message.add_reaction("<:pinklotus:1263556545686405170>")
```

In `enable_cmd`'s generic command path (where `remove_disabled_command(ctx.guild.id, cmd_name)` already runs), add the enabled-store bookkeeping right after it — so enabling an individual command of a default-disabled system pins it against the default:

```python
        from utils import add_enabled_command
        from utils import COMMAND_SYSTEMS
        if any(
            c == cmd_name or cmd_name.startswith(c + ' ')
            for sys in COMMAND_SYSTEMS.values()
            if sys.get('default_disabled')
            for c in sys.get('commands', [])
        ):
            add_enabled_command(ctx.guild.id, cmd_name)
```

(If the command is not part of a default-disabled system, no enabled-entry is written — the generic per-guild disabled-list removal already re-enables it.)

Rewrite `_disabled_list_text` to show the effective disabled state (explicit disabled entries, plus default-disabled system commands not covered by the guild's enabled list):

```python
    def _disabled_list_text(self, ctx) -> str:
        from utils import (COMMAND_SYSTEMS, get_disabled_commands,
                           get_enabled_commands, resolve_system)

        enabled = get_enabled_commands(ctx.guild.id)
        parts = [f'.{entry}' for entry in get_disabled_commands(ctx.guild.id)]
        for sys_name, sys in COMMAND_SYSTEMS.items():
            if not sys.get('default_disabled'):
                continue
            if any(e == sys_name for e in enabled):
                continue
            for c in sys.get('commands', []):
                if any(e == c for e in enabled):
                    continue
                parts.append(f'.{c}')
        parts = sorted(set(parts))
        if not parts:
            return "-# nothing disabled here."
        return "-# disabled here: " + ", ".join(parts)
```

A system entry shows as `.reactionlb`; when it is default-disabled and not enabled, its commands appear individually (`.rtop`, `.rctop`, `.rc`, `.lb emojis`).

- [ ] **Step 6: Run tests to verify they pass**

Run: `ssh muixo "cd /home/ubuntu/neiXO && git pull && venv/bin/python -m pytest tests/test_disabled.py -v"`
Expected: all tests pass (previous 15 + 10 new = 25 in the file).

- [ ] **Step 7: Run the full suite**

Run: `ssh muixo "cd /home/ubuntu/neiXO && venv/bin/python -m pytest -q"`
Expected: no regressions (147 total: 137 + 10 new).

- [ ] **Step 8: Commit**

```bash
git add utils.py cogs/leveling.py tests/test_disabled.py
git commit -m "disable: command systems (reactionlb family, default-disabled)"
```

---

### Task 2: Deploy

- [ ] **Step 1: Push and restart**

```bash
git push origin main
ssh muixo "cd /home/ubuntu/neiXO && git pull && pm2 restart neixo"
```

- [ ] **Step 2: Verify boot**

Run: `sleep 6 && ssh muixo "pm2 logs neixo --lines 40 --nostream 2>/dev/null | tail -25; pm2 status neixo"`
Expected: `Logged in:` line, no traceback, status online.
