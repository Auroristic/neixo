import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone

from utils import (
    load_json, save_json, get_embed_color, get_config, invalidate_config,
    log_audit, help_meta,
    CONFESSIONS_FILE, CONFIG_FILE, SEOULITIES_SERVER_ID
)


# ── cogs/confessions.py ─────────────────────────────────────────
COG_META = {
    "category": "staff",
    "label": "Staff",
    "desc": "Staff moderation and vanity tools.",
    "staff": True,
}
 



class ConfessionModal(discord.ui.Modal, title="Submit Anonymous Confession"):
    confession_text = discord.ui.TextInput(
        label="Your Confession",
        style=discord.TextStyle.paragraph,
        placeholder="Share your thoughts anonymously...",
        required=True,
        max_length=1000
    )
    
    def __init__(self, bot_instance):
        super().__init__()
        self.bot = bot_instance
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            text = self.confession_text.value.lower()
            blocked_patterns = ['http://', 'https://', 'www.', '.com', '.net', '.org', '.gg', 'discord.gg', '.io', '.co', '.me']
            
            if any(pattern in text for pattern in blocked_patterns):
                await interaction.response.send_message(
                    "? Links and URLs are not allowed in confessions!",
                    ephemeral=True
                )
                return
            
            user_id = interaction.user.id
            now = datetime.now(timezone.utc)
            cooldowns = self.bot.get_cog("Confessions").user_cooldowns
            
            if user_id in cooldowns:
                time_left = (cooldowns[user_id] - now).total_seconds()
                if time_left > 0:
                    await interaction.response.send_message(
                        f"wait {int(time_left)}s",
                        ephemeral=True
                    )
                    return
            
            cooldowns[user_id] = now + timedelta(seconds=15)
            
            config = load_json(CONFIG_FILE)
            guild_config = config.get(str(interaction.guild_id), {})
            confession_channel_id = guild_config.get('confession_channel')
            
            if not confession_channel_id:
                await interaction.response.send_message(
                    "uh just `.confess set #channel`",
                    ephemeral=True
                )
                return
            
            channel = interaction.guild.get_channel(int(confession_channel_id))
            if not channel:
                await interaction.response.send_message(
                    "ts still on demo gng hold yo horses.",
                    ephemeral=True
                )
                return
            
            confessions = load_json(CONFESSIONS_FILE)
            guild_confessions = [c for c in confessions.values() if c.get('guild_id') == str(interaction.guild_id)]
            confession_id = (max(c['id'] for c in guild_confessions) if guild_confessions else 0) + 1

            embed = discord.Embed(
                    title="Anonymous Confession",
                    description=self.confession_text.value,
                    color=get_embed_color(interaction.guild_id),
                    timestamp=datetime.now(timezone.utc)
                )
            embed.set_footer(text=f"Confession #{confession_id:03d}")
            
            view = ConfessionButtons(self.bot, confession_id)
            message = await channel.send(embed=embed, view=view)
            
            confession_key = f"{interaction.guild_id}_{confession_id}"
            confessions[confession_key] = {
                'id': confession_id,
                'guild_id': str(interaction.guild_id),
                'user_id': str(user_id),
                'text': self.confession_text.value,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'message_id': str(message.id),
                'channel_id': str(channel.id),
                'replies': []
            }
            save_json(CONFESSIONS_FILE, confessions)
            
            await interaction.response.send_message(
                "No one will know what u posted not even staff, so please be careful what you say",
                ephemeral=True
            )
            
        except Exception as e:
            print(f"Error in ConfessionModal: {e}")
            try:
                await interaction.response.send_message(
                    f"? Something went wrong: {str(e)}",
                    ephemeral=True
                )
            except:
                await interaction.followup.send(
                    f"? Something went wrong: {str(e)}",
                    ephemeral=True
                )

class ReplyModal(discord.ui.Modal, title="Reply Anonymously"):
    reply_text = discord.ui.TextInput(
        label="Your Reply",
        style=discord.TextStyle.paragraph,
        placeholder="Reply anonymously...",
        required=True,
        max_length=1000
    )
    
    def __init__(self, bot_instance, confession_id, guild_id):
        super().__init__()
        self.bot = bot_instance
        self.confession_id = confession_id
        self.guild_id = guild_id
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            text = self.reply_text.value.lower()
            blocked_patterns = ['http://', 'https://', 'www.', '.com', '.net', '.org', '.gg', 'discord.gg', '.io', '.co', '.me']
            
            if any(pattern in text for pattern in blocked_patterns):
                await interaction.response.send_message(
                    "u aint sending links buddy, back off.",
                    ephemeral=True
                )
                return
            
            user_id = interaction.user.id
            now = datetime.now(timezone.utc)
            cooldowns = self.bot.get_cog("Confessions").user_cooldowns
            
            if user_id in cooldowns:
                time_left = (cooldowns[user_id] - now).total_seconds()
                if time_left > 0:
                    await interaction.response.send_message(
                        f"wait {int(time_left)}s.",
                        ephemeral=True
                    )
                    return
            
            cooldowns[user_id] = now + timedelta(seconds=15)
            
            confessions = load_json(CONFESSIONS_FILE)
            confession_key = f"{self.guild_id}_{self.confession_id}"
            confession = confessions.get(confession_key)
            
            if not confession:
                await interaction.response.send_message("Confession not found.", ephemeral=True)
                return
            
            channel = interaction.guild.get_channel(int(confession['channel_id']))
            if not channel:
                await interaction.response.send_message("Channel not found.", ephemeral=True)
                return
            
            try:
                original_message = await channel.fetch_message(int(confession['message_id']))
            except:
                await interaction.response.send_message("Original confession message not found.", ephemeral=True)
                return
            
            thread = None
            thread_id = confession.get('thread_id')
            
            if thread_id:
                try:
                    thread = interaction.guild.get_thread(int(thread_id))
                    if not thread:
                        thread = await interaction.guild.fetch_channel(int(thread_id))
                except:
                    thread = None
            
            if not thread:
                try:
                    thread = await original_message.create_thread(
                        name=f"Confession #{self.confession_id:03d} Replies",
                        auto_archive_duration=1440
                    )
                    confession['thread_id'] = str(thread.id)
                    confessions[confession_key] = confession
                    save_json(CONFESSIONS_FILE, confessions)
                except Exception as e:
                    await interaction.response.send_message(
                        f"Failed to create thread: {str(e)}",
                        ephemeral=True
                    )
                    return
            
            confessions_list = load_json(CONFESSIONS_FILE)
            reply_count = sum(len(c.get('replies', [])) for c in confessions_list.values())
            reply_id = reply_count + 1
            
            reply_embed = discord.Embed(
                description=f"{self.reply_text.value}",
                color=get_embed_color(interaction.guild_id),
                timestamp=datetime.now(timezone.utc)
            )
            reply_embed.set_footer(text=f"Anonymous Reply #{reply_id:03d}")
            
            reply_message = await thread.send(embed=reply_embed)
            
            reply_data = {
                'reply_id': reply_id,
                'user_id': str(user_id),
                'text': self.reply_text.value,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'message_id': str(reply_message.id)
            }
            
            if 'replies' not in confession:
                confession['replies'] = []
            confession['replies'].append(reply_data)
            confessions[confession_key] = confession
            save_json(CONFESSIONS_FILE, confessions)
            
            await interaction.response.send_message(
                "done.",
                ephemeral=True
            )
            
        except Exception as e:
            print(f"Error in ReplyModal: {e}")
            try:
                await interaction.response.send_message(
                    f"? Something went wrong: {str(e)}",
                    ephemeral=True
                )
            except:
                await interaction.followup.send(
                    f"? Something went wrong: {str(e)}",
                    ephemeral=True
                )

class ConfessionButtons(discord.ui.View):
    def __init__(self, bot_instance, confession_id):
        super().__init__(timeout=None)
        self.bot = bot_instance
        self.confession_id = confession_id
    
    @discord.ui.button(label="Submit", style=discord.ButtonStyle.green, custom_id="submit_confession_button")
    async def submit_confession(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ConfessionModal(self.bot)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="Reply", style=discord.ButtonStyle.blurple, custom_id="reply_button")
    async def reply_anonymous(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ReplyModal(self.bot, self.confession_id, interaction.guild_id)
        await interaction.response.send_modal(modal)

class ConfessionsCog(commands.Cog, name="Confessions"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.user_cooldowns = {}

    @help_meta(
        usage="`.cid latest | number | user <id>`",
        desc="reveals who sent a confession (DM only).",
        staff=True,
    )
    @commands.command(name="cid")
    async def reveal_confession(self, ctx, target: str = None, user_id: str = None):
        if ctx.guild is not None:
            await ctx.send("forbidden.")
            return
        
        config = load_json(CONFIG_FILE)
        guild_config = config.get(str(SEOULITIES_SERVER_ID), {})
        whitelist = guild_config.get('whitelist', [])
        
        if str(ctx.author.id) not in whitelist:
            await ctx.send("no perms")
            return
        
        confessions = load_json(CONFESSIONS_FILE)
        
        if target == "latest":
            guild_confessions = {k: v for k, v in confessions.items() if v.get('guild_id') == str(SEOULITIES_SERVER_ID)}
            if not guild_confessions:
                await ctx.send("nun found.")
                return
            
            latest = max(guild_confessions.values(), key=lambda x: x['id'])
            user_obj = await self.bot.fetch_user(int(latest['user_id']))
            
            embed = discord.Embed(
                title=f"Confession #{latest['id']:03d} Revealed",
                description=latest['text'],
                color=get_embed_color(SEOULITIES_SERVER_ID)
            )
            embed.add_field(name="Author", value=f"{user_obj.mention} ({user_obj.id})")
            embed.add_field(name="Timestamp", value=latest['timestamp'])
            
            if latest.get('replies'):
                replies_text = ""
                for reply in latest['replies'][:5]:
                    reply_user = await self.bot.fetch_user(int(reply['user_id']))
                    replies_text += f"R#{reply['reply_id']:03d} by {reply_user.mention}\n"
                embed.add_field(name="Replies", value=replies_text or "None", inline=False)
            
            await ctx.send(embed=embed)
            log_audit("reveal_latest", SEOULITIES_SERVER_ID, ctx.author.id, f"Confession #{latest['id']:03d}")
        
        elif target == "user" and user_id:
            if not user_id.isdigit():
                await ctx.send("invalid user id")
                return
            
            user_confessions = [v for v in confessions.values() 
                              if v.get('guild_id') == str(SEOULITIES_SERVER_ID) and v.get('user_id') == str(user_id)]
            
            if not user_confessions:
                await ctx.send(f"nun found from that user")
                return
            
            try:
                user_obj = await self.bot.fetch_user(int(user_id))
                user_name = user_obj.name
            except:
                user_name = "Unknown User"
            
            confession_list = "\n".join([f"#{c['id']:03d}: {c['text'][:50]}..." for c in user_confessions[:10]])
            
            embed = discord.Embed(
                title=f"Confessions by {user_name}",
                description=confession_list,
                color=get_embed_color(SEOULITIES_SERVER_ID)
            )
            embed.set_footer(text=f"Total: {len(user_confessions)}")
            
            await ctx.send(embed=embed)
            log_audit("reveal_user", SEOULITIES_SERVER_ID, ctx.author.id, f"User: {user_id}")
            
        elif target and target.isdigit():
            confession_id = int(target)
            confession_key = f"{SEOULITIES_SERVER_ID}_{confession_id}"
            confession = confessions.get(confession_key)
            
            if not confession:
                await ctx.send(f"Confession #{confession_id:03d} not found.")
                return
            
            user_obj = await self.bot.fetch_user(int(confession['user_id']))
            
            embed = discord.Embed(
                title=f"Confession #{confession_id:03d} Revealed",
                description=confession['text'],
                color=get_embed_color(SEOULITIES_SERVER_ID)
            )
            embed.add_field(name="Author", value=f"{user_obj.mention} ({user_obj.id})")
            embed.add_field(name="Timestamp", value=confession['timestamp'])
            
            if confession.get('replies'):
                replies_text = ""
                for reply in confession['replies'][:5]:
                    reply_user = await self.bot.fetch_user(int(reply['user_id']))
                    replies_text += f"R#{reply['reply_id']:03d} by {reply_user.mention}\n"
                embed.add_field(name="Replies", value=replies_text or "None", inline=False)
            
            await ctx.send(embed=embed)
            log_audit("reveal_confession", SEOULITIES_SERVER_ID, ctx.author.id, f"Confession #{confession_id:03d}")
        else:
            await ctx.send("Usage: `.cid latest` | `.cid user <user_id>` | `.cid <number>`")

async def setup(bot: commands.Bot):
    await bot.add_cog(ConfessionsCog(bot))