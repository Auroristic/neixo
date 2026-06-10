import discord
from discord.ext import commands
import logging
from utils import (
    save_playlist, load_playlist, delete_playlist, list_playlists,
    get_embed_color, help_meta
)

logger = logging.getLogger(__name__)

COG_META = {
    "category": "music",
    "commands": ["playlist"]
}


class Playlists(commands.Cog):
    """Music playlist management system."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        if ctx.guild is None:
            await ctx.send("This command only works in servers.")
            return False
        return True

    @commands.group(invoke_without_command=True)
    @help_meta(section="Music", usage=".playlist <name>", desc="Play a saved playlist")
    async def playlist(self, ctx, *, name: str = None):
        """Play a saved playlist or manage playlists.
        
        Subcommands:
        - .playlist save <name> - Save current queue as playlist
        - .playlist load <name> - Load and play a playlist
        - .playlist delete <name> - Delete a playlist
        - .playlist list - Show all your playlists
        """
        if not name:
            playlists = list_playlists(ctx.author.id)
            if not playlists:
                await ctx.send("You don't have any saved playlists. Use `.playlist save <name>` to create one!")
                return
            
            embed = discord.Embed(
                title="Your Playlists",
                description="\n".join(f"• `{p}`" for p in sorted(playlists)),
                color=discord.Color(get_embed_color(ctx.guild.id))
            )
            await ctx.send(embed=embed)
        else:
            tracks = load_playlist(ctx.author.id, name)
            if not tracks:
                await ctx.send(f"Playlist '{name}' not found! Use `.playlist list` to see your playlists.")
                return
            
            import wavelink
            player = ctx.voice_client
            if not player:
                if not ctx.author.voice or not ctx.author.voice.channel:
                    await ctx.send("You need to be in a voice channel first!")
                    return
                try:
                    player = await ctx.author.voice.channel.connect(cls=wavelink.Player)
                    player.autoplay = wavelink.AutoPlayMode.disabled
                    if not hasattr(player, "home"):
                        player.home = ctx.channel
                except Exception as e:
                    await ctx.send(f"Failed to connect: {e}")
                    return
            
            await ctx.send(f"Loading playlist **{name}** ({len(tracks)} tracks)...")
            added = 0
            for track in tracks:
                try:
                    results = await wavelink.Playable.search(track)
                    if not results:
                        continue
                    t = results[0] if isinstance(results, list) else results
                    if isinstance(t, wavelink.Playlist):
                        t = next((x for x in t.tracks if x.length and x.length <= 30*60*1000), None)
                        if t is None:
                            continue
                    await player.queue.put_wait(t)
                    added += 1
                except Exception:
                    logger.warning(f"Failed to add track: %s", track)
                    continue
            
            if added == 0:
                await ctx.send("Could not add any tracks from the playlist.")
            else:
                await ctx.send(f"Added {added}/{len(tracks)} tracks to the queue!")
                if not player.playing and not player.queue.is_empty:
                    try:
                        await player.play(player.queue.get())
                    except Exception as e:
                        logger.warning("failed to start playlist: %s", e)

    @playlist.command()
    @help_meta(section="Music", usage=".playlist save <name>", desc="Save current queue as playlist")
    async def save(self, ctx, *, name: str):
        """Save the current queue as a playlist."""
        # Get music cog and player
        music_cog = self.bot.get_cog("Music")
        if not music_cog:
            await ctx.send("Music system is not available!")
            return
        
        player = ctx.voice_client
        if not player or player.queue.is_empty:
            await ctx.send("There's no queue to save! Add some tracks first.")
            return
        
        # Extract track names/URLs from queue
        tracks = []
        for track in player.queue:
            # Try to get searchable identifier
            if hasattr(track, 'uri') and track.uri:
                tracks.append(track.uri)
            elif hasattr(track, 'title') and hasattr(track, 'author'):
                tracks.append(f"{track.author} - {track.title}")
            else:
                tracks.append(str(track))
        
        if not tracks:
            await ctx.send("Queue is empty!")
            return
        
        save_playlist(ctx.author.id, name, tracks)
        
        embed = discord.Embed(
            title="✅ Playlist Saved!",
            description=f"Saved **{len(tracks)}** tracks to playlist `{name}`",
            color=discord.Color(get_embed_color(ctx.guild.id))
        )
        embed.set_footer(text=f"Use .playlist {name} to play it")
        await ctx.send(embed=embed)

    @playlist.command()
    @help_meta(section="Music", usage=".playlist load <name>", desc="Load a playlist to queue")
    async def load(self, ctx, *, name: str):
        tracks = load_playlist(ctx.author.id, name)
        if not tracks:
            await ctx.send(f"Playlist '{name}' not found!")
            return
        
        import wavelink
        player = ctx.voice_client
        if not player:
            await ctx.send("No active player! Join a voice channel and play something first.")
            return
        
        added = 0
        for track in tracks:
            try:
                results = await wavelink.Playable.search(track)
                if not results:
                    continue
                t = results[0] if isinstance(results, list) else results
                if isinstance(t, wavelink.Playlist):
                    t = next((x for x in t.tracks if x.length and x.length <= 30*60*1000), None)
                    if t is None:
                        continue
                await player.queue.put_wait(t)
                added += 1
            except Exception:
                continue
        
        await ctx.send(f"Added {added}/{len(tracks)} tracks from playlist `{name}` to queue.")

    @playlist.command(aliases=["del"])
    @help_meta(section="Music", usage=".playlist delete <name>", desc="Delete a playlist")
    async def delete(self, ctx, *, name: str):
        """Delete a saved playlist."""
        deleted = delete_playlist(ctx.author.id, name)
        if deleted:
            await ctx.send(f"🗑️ Deleted playlist `{name}`.")
        else:
            await ctx.send(f"Playlist `{name}` not found!")

    @playlist.command(aliases=["ls"])
    @help_meta(section="Music", usage=".playlist list", desc="List all your playlists")
    async def list(self, ctx):
        """Show all your saved playlists."""
        playlists = list_playlists(ctx.author.id)
        if not playlists:
            await ctx.send("You don't have any saved playlists!")
            return
        
        embed = discord.Embed(
            title=f"🎵 Your Playlists ({len(playlists)})",
            description="\n".join(f"• `{p}`" for p in sorted(playlists)),
            color=discord.Color(get_embed_color(ctx.guild.id))
        )
        embed.set_footer(text="Use .playlist <name> to play a playlist")
        await ctx.send(embed=embed)

    @playlist.command()
    @help_meta(section="Music", usage=".playlist info <name>", desc="Show playlist details")
    async def info(self, ctx, *, name: str):
        """Show details about a specific playlist."""
        tracks = load_playlist(ctx.author.id, name)
        if not tracks:
            await ctx.send(f"Playlist '{name}' not found!")
            return
        
        embed = discord.Embed(
            title=f"📀 Playlist: {name}",
            description=f"**{len(tracks)}** tracks",
            color=discord.Color(get_embed_color(ctx.guild.id))
        )
        
        # Show first 10 tracks
        for i, track in enumerate(tracks[:10], 1):
            embed.add_field(name=f"{i}. {track[:50]}", value="\u200b", inline=False)
        
        if len(tracks) > 10:
            embed.set_footer(text=f"...and {len(tracks) - 10} more tracks")
        
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Playlists(bot))
    logger.info("Loaded cogs.playlists")
