# dashboard/names.py


def resolve_name(bot, guild_id, user_id) -> tuple[str, str]:
    """Return (display_name, discriminator-ish handle) for a user in a guild."""
    uid = int(user_id)
    guild = bot.get_guild(int(guild_id)) if guild_id else None
    member = guild.get_member(uid) if guild else None
    if member:
        handle = f"@{member.name}"
        return (member.display_name or member.name), handle
    user = bot.get_user(uid)
    if user:
        return (user.global_name or user.name), f"@{user.name}"
    return str(user_id), f"@{user_id}"


def annotate(bot, rows, guild_key="guild_id", user_key="user_id", value_keys=()):
    """Add display/handle keys to each row dict in place; returns rows."""
    for r in rows:
        r["display"], r["handle"] = resolve_name(
            bot, r.get(guild_key) or 0, r.get(user_key)
        )
    return rows
