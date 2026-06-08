# Neixo (Discord Bot)

**Neixo** is a Discord bot with command + slash-command features, music playback (via Lavalink / Wavelink), and AI features (DM + guild-configured channels).

This repository is intended for **private GitHub publishing** (keep secrets out of git).

## Quick overview
- **Entrypoint:** `neixo.py`
- **Commands:**
  - Prefix commands use `.` and the bot mention (see `get_prefix` in `neixo.py`)
  - Slash commands are registered in `neixo.py` (e.g. `/play`, `/skip`, `/help`, etc.)
- **Cogs:** loaded dynamically from `./cogs` and `./cogs/events`
- **Music:** Wavelink (requires Lavalink server + Java 17)
- **Persistent storage:** SQLite (`data/bot.db`) via helpers in `utils.py`

## Requirements
- Python: **3.10+ recommended** (uses modern type hints)
- Discord bot token (see `.env`)
- Lavalink (only needed if you use music features)

Python dependencies:
```bash
pip install -r requirements.txt
```

## Required environment variables
Create a file named **`.env`** in the project root (it is already ignored by git via `.gitignore`):

```env
DISCORD_TOKEN=your_discord_bot_token
```

Optional:
```env
CREATOR_ID=887382911924441139
LAVALINK_URI=http://localhost:2333
LAVALINK_PASS=youshallnotpass
```

### Notes on `neixoset.toml`
The bot reads `neixoset.toml` for branding/config like emojis and embed color:
- `neixoconfig.py` loads it at startup
- If the file fails to load, the bot falls back to safe defaults

## How to run (development / local)
```bash
python neixo.py
```

On first run, the bot:
- creates the `data/` directory
- creates `data/bot.db` (SQLite)
- optionally migrates legacy JSON files from `data/*.json` into SQLite and renames them to `*.json.migrated`

## How it stores data
All bot “JSON files” are stored in SQLite using a key/value table (`kv`), via:
- `data/bot.db`

Legacy JSON files (if present) are migrated once:
- `data/config.json`
- `data/aliases.json`
- `data/dm_whitelist.json`
- `data/ignore_list.json`
- `data/conversations*.json`
- `data/bot_memory*.json`
- `data/confessions.json`
- `data/audit.json`
- `data/cmd_channel_rules.json` (command/channel allow/deny)

## Lavalink (music playback)
Music uses **Lavalink** with **Wavelink**.
- Lavalink defaults in code:
  - `LAVALINK_URI`: `http://localhost:2333`
  - `LAVALINK_PASS`: `youshallnotpass`
- If you don’t run Lavalink, the bot will log an error on connection and music commands may fail.

## Private GitHub publishing checklist
- Do **not** commit `.env`
- Keep `data/` out of git if it contains any private information (you can add `data/` to `.gitignore` if needed)
- Consider setting the repo visibility to **Private** in GitHub

## License
Add your preferred license (or keep it “All Rights Reserved” by not specifying one).
