import discord
from discord.ext import commands
from neixoconfig import Neixoname, Neixocolor, Neixoemojis
from utils import get_embed_color


class OnTag(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        if message.content == f"<@{self.bot.user.id}>":
            embed = discord.Embed(
                description=(
                    f"{Neixoemojis.get('love')} hey {message.author.mention}, my prefix is `.`\n\n"
                    f"{Neixoemojis.get('rightarrow')} `.help` — full command list\n"
                    f"{Neixoemojis.get('rightarrow')} `.help <command>` — command info\n"
                    f"{Neixoemojis.get('rightarrow')} ping me + ask a question — i'll point u to the right command"
                ),
                color=get_embed_color(message.guild.id)
            )
            embed.set_thumbnail(url=message.author.display_avatar.url)
            await message.reply(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(OnTag(bot))
