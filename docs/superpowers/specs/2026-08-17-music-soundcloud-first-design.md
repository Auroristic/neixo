# Music Playback Restoration — SoundCloud-First Fallback

**Date:** 2026-08-17
**Status:** Implemented (Phase 1)

## Problem

YouTube music playback is broken. Lavalink (chappy) fails every track with
`AllClientsFailedException`: all YT clients report "This video requires login" /
"Sign in to confirm you're not a bot".

Root cause, verified by direct testing from chappy:

- The youtube-source plugin (1.18.2) is **already the latest release** — no update fixes it.
- `yt-dlp` (2026.07.04) on chappy fails identically across all player clients
  (tv, tv_simply, web_embedded, mweb, ios) — the block is **IP-level**, aimed at
  the datacenter IP, not at our clients/tokens.
- PO token pipeline (bgutil) and OAuth refresh token are functioning but cannot
  bypass an IP-level block. Cloudflare WARP was tried previously — also blocked.
- Same test on sobvenger (Oracle IN) shows the identical block — moving servers
  would not help.

## Solution (Phase 1 — shipped)

Reorder search fallback chains in `cogs/music.py` so SoundCloud (which plays
reliably from chappy) resolves first; YouTube search stays as last resort since
search metadata still works, and the existing track-exception handler skips
failed YT tracks gracefully.

Changed chains (all in `cogs/music.py`):

1. `_search_with_fallback` — now `scsearch → ytmsearch → ytsearch`
2. `.play` command flow — same order
3. Spotify `_resolve_one` — same order

Explicit-source commands (`.playsc`, `.playytm`, `.playbc`) unchanged.
Failed-track skip/retry logic unchanged.

## Rejected alternatives

- **Server move** — both candidate IPs are equally YT-blocked (tested).
- **Deezer (LavaSrc)** — needs an `arl` cookie from a logged-in account;
  Deezer Free signup is geo-unavailable to the user. Deferred, not rejected:
  if an arl is obtained later (e.g. via German exit through chappy), enable
  `lavasrc.deezer` with `dzisrc/dzsearch` providers ahead of scsearch.
- **yt-dlp + browser cookies** — auth whack-a-mole the user opted out of.
- **PO tokens / OAuth / WARP retries** — cannot fix IP-level blocks.

## Verification

- `ruff`: no new issues in changed regions (34 pre-existing style warnings unchanged).
- No unit tests cover the fallback chain; verified live post-deploy
  (`.play`, `.playsc`, Spotify link) on the production bot.

## Rollback

`git revert` of the deploy commit; no server-side changes in Phase 1.
