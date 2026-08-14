from types import SimpleNamespace

import discord
import pytest
from discord.ext import commands


@pytest.mark.asyncio
async def test_welcome_test_registered_with_cooldown():
    import cogs.welcome

    bot = commands.Bot(command_prefix='.', intents=discord.Intents.all())
    await bot.add_cog(cogs.welcome.Welcome(bot))
    cmd = bot.get_command('welcome test')
    assert cmd is not None
    # the @commands.cooldown decorator on the plain function becomes
    # Command._buckets (a CooldownMapping) at command creation
    assert getattr(cmd, '_buckets', None) is not None


@pytest.mark.asyncio
async def test_welcome_test_help_meta_present():
    import cogs.welcome
    from utils import get_help_meta

    bot = commands.Bot(command_prefix='.', intents=discord.Intents.all())
    await bot.add_cog(cogs.welcome.Welcome(bot))
    meta = get_help_meta(bot.get_command('welcome test'))
    assert meta['section'] in ('General', 'Server Management')
    assert '.welcome test' in meta['usage']


@pytest.mark.asyncio
async def test_fetch_member_art_returns_avatar_and_banner():
    from cogs.welcome import _fetch_member_art

    member = SimpleNamespace(
        display_avatar=SimpleNamespace(url=''),
        guild=SimpleNamespace(banner=None),
    )
    avatar_bytes, banner_bytes = await _fetch_member_art(member)
    assert avatar_bytes is None
    assert banner_bytes is None
