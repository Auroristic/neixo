#!/usr/bin/env python3
import os
import sys
import json
import asyncio

# Mock environment token before importing neixo
os.environ.setdefault("DISCORD_TOKEN", "mock_token")

# Add current path to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import discord
from discord.ext import commands
from neixo import bot, load_cogs
from utils import get_help_meta

_CAT_ICONS = {
    "music": "♪",
    "theme": "★",
    "admin": "◆",
    "moderation": "⚔",
    "fun": "♢",
    "profile": "●",
    "utility": "■",
    "miscellaneous": "▪",
    "misc": "▪",
    "help": "?",
    "setup": "⚙",
    "support": "♥",
    "server": "▼",
    "ai": "▲",
    "imagine": "✦",
    "vanity": "◆",
    "reminders": "●",
    "reactions": "♥",
    "confessions": "✎",
    "check": "✓",
    "serverstats": "▧",
    "funimage": "✦",
    "gifs": "○",
    "memory": "◆",
    "config": "⚙",
    "general": "☆",
}

def process_command(cmd, cat_id, cat_label, categories, cog):
    count = 0
    meta = get_help_meta(cmd)

    desc = ""
    usage_str = ""
    owner = False
    admin = False
    staff = False
    examples = []
    params = []
    note = None
    section = cat_label

    if meta:
        desc = meta.get("desc") or cmd.help or "No description."
        usage_str = meta.get("usage") or f".{cmd.qualified_name}"
        owner = meta.get("owner", False)
        admin = meta.get("admin", False)
        staff = meta.get("staff", False)
        examples = meta.get("examples") or []
        params = meta.get("params") or []
        note = meta.get("note")
        section = meta.get("section") or cat_label
    else:
        desc = cmd.help or "No description."
        sig = f" {cmd.signature}" if cmd.signature else ""
        usage_str = f".{cmd.qualified_name}{sig}"

    u_clean = usage_str.strip("` ")
    if u_clean.startswith("."):
        u_clean = u_clean[1:].strip()

    is_subcmd = cmd.parent is not None
    if is_subcmd:
        args = u_clean
    else:
        if u_clean.startswith(cmd.name):
            args = u_clean[len(cmd.name):].strip()
        else:
            args = u_clean

    usage_parts = {
        "prefix": ".",
        "name": cmd.name,
        "args": args
    }

    cog_file = cog.__class__.__module__.split(".")[-1] + ".py"

    cmd_obj = {
        "name": cmd.name,
        "usage": usage_str,
        "usage_parts": usage_parts,
        "description": desc,
        "aliases": cmd.aliases,
        "owner": owner,
        "admin": admin,
        "staff": staff,
        "examples": examples,
        "params": params,
        "note": note,
        "category_id": cat_id,
        "section": section,
        "group": cmd.parent.name if is_subcmd else None,
        "is_subcommand": is_subcmd,
        "has_subcommands": isinstance(cmd, commands.Group),
        "cog_file": cog_file
    }

    categories[cat_id]["sections"].setdefault(section, [])
    exists = False
    for existing_cmd in categories[cat_id]["sections"][section]:
        if existing_cmd["name"] == cmd.name and existing_cmd["group"] == cmd_obj["group"]:
            exists = True
            break
    if not exists:
        categories[cat_id]["sections"][section].append(cmd_obj)
        count += 1

    if isinstance(cmd, commands.Group):
        for subcmd in cmd.commands:
            count += process_command(subcmd, cat_id, cat_label, categories, cog)

    return count

async def main():
    print("Loading cogs...")
    await load_cogs()

    categories = {}
    total_cmds = 0

    for cog in bot.cogs.values():
        cog_class = cog.__class__
        meta = getattr(cog_class, "COG_META", None)
        if not meta:
            module = sys.modules.get(cog_class.__module__)
            meta = getattr(module, "COG_META", None)

        if not meta or not isinstance(meta, dict):
            continue

        cat_id = str(meta.get("category", cog_class.__name__.lower())).strip()
        cat_label = str(meta.get("label", cat_id.title())).strip()
        cat_desc = str(meta.get("desc", "")).strip()
        cat_staff = meta.get("staff", False)
        cat_owner = meta.get("owner", False)
        cat_admin = meta.get("admin", False)

        if cat_id not in categories:
            icon = _CAT_ICONS.get(cat_id.lower(), "▪")
            categories[cat_id] = {
                "label": cat_label,
                "desc": cat_desc,
                "admin": cat_admin,
                "owner": cat_owner,
                "staff": cat_staff,
                "icon": icon,
                "sections": {}
            }

        for cmd in cog.get_commands():
            total_cmds += process_command(cmd, cat_id, cat_label, categories, cog)

    # Sort the commands under each section alphabetically
    for cat in categories.values():
        for sec in cat["sections"]:
            cat["sections"][sec].sort(key=lambda x: x["name"])

    output = {
        "total_commands": total_cmds,
        "prefix": ".",
        "categories": categories
    }

    target_xo = "/home/retro/retroisticx/projects/nei/xo/commands.json"
    target_local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "commands.json")

    print(f"Writing to {target_xo}...")
    os.makedirs(os.path.dirname(target_xo), exist_ok=True)
    with open(target_xo, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Writing to {target_local}...")
    with open(target_local, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Successfully generated {total_cmds} commands across {len(categories)} categories!")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)

if __name__ == "__main__":
    asyncio.run(main())
