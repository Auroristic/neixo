# Neixo

Feature-rich Discord bot with music playback, real-time synced lyrics, AI chat, leveling, and per-guild customization.

## Features

### 🎵 Music

Full-featured audio playback via Lavalink/Wavelink. Queue management, audio filters, and the bot's standout feature:

#### ⭐ Real-Time Synced Lyrics

Neixo has a **multi-source synced lyrics engine** that pulls timestamped LRC lyrics from Musixmatch, NetEase, Genius, LRCLIB, and OVH — automatically matching lyrics to the exact millisecond of the song as it plays in your voice channel.

- **Live karaoke-style display** — a dedicated message updates in real-time as the song progresses, showing the current line bolded with surrounding context (3 lines before, 3 after)
- **`.sync` command** — force-trigger live synced lyrics for the current track
- **Smart line grouping** — short lines (<1.5s) are double-bolded with the next line to avoid rapid flickering
- **Speed-aware** — respects playback speed changes (timescale filters) and handles seeking correctly
- **Now Playing button** — the `/np` view includes a lyrics button that lights up when synced lyrics are available
- **Paginated fallback** — if only plain lyrics are available, displays them in a paginated embed

This is one of the few Discord music bots with true timestamp-accurate synced lyrics — a rare feature that makes a huge difference for singalongs.

**Other music features:**
- Play, skip, pause, resume, stop, volume, queue
- Audio filters: bass boost, nightcore, slow motion, karaoke (vocal removal), rotation, low pass, tremolo
- Playlist management (save, load, delete per guild)
- Similar track recommendations
- Equalizer presets (rock, pop, jazz, classical, etc.)

### 🤖 AI Chat
- Configure AI channels per guild
- Direct message AI conversations with context awareness

### 📊 Leveling & XP
- Earn XP through messages (10 XP) and voice activity (5 XP/min)
- `.rank [@user]` — check rank and progress
- `.leaderboard` — top users globally or per guild
- `.levelrole [level] [@role]` — auto-assign roles at level thresholds
- Optional compact level-up notifications

### 🖼️ Per-Guild Avatars
- `.setavatar <image_url>` — set a server-specific avatar
- `.removeavatar` — remove custom avatar
- `.serveravatars` — browse all custom avatars in the server
- Displays in `.profile`

### 🛠️ Utilities
- Anonymous confessions system
- Reminders with natural language parsing ("remind me in 2 hours")
- Reaction roles
- GIF editor
- Server stats tracking
- Theme/embed customization
- Vanity URL detection and moderation
- Help system with categorized command index

## Requirements
- **Python 3.10+**
- Discord bot token
- Lavalink server + Java 17 (for music)

## Quick start

```env
# .env
DISCORD_TOKEN=your_token_here
```

```bash
pip install -r requirements.txt
python neixo.py
```

On first run the bot creates `data/bot.db` (SQLite) and migrates any legacy JSON files automatically.

### Optional env vars
```env
CREATOR_ID=your_discord_user_id_here
LAVALINK_URI=http://localhost:2333
LAVALINK_PASS=youshallnotpass
```

## Configuration

`neixoset.toml` controls branding (emojis, embed colors). Falls back to safe defaults if missing.

## Storage

All persistent data lives in `data/bot.db` (SQLite key/value store via `utils.py`). Legacy JSON files are migrated once on first run.

## Lavalink
- Default URI: `http://localhost:2333`
- Default password: `youshallnotpass`
- Music commands require a running Lavalink instance

## Notes
- Uses `.` as prefix (configurable via `get_prefix` in `neixo.py`)
- Slash commands also available (`/play`, `/skip`, `/help`, etc.)
- Cogs loaded dynamically from `cogs/` and `cogs/events/`
- Keep `.env` and `data/` out of version control
