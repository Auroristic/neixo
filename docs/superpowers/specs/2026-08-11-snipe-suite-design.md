# Snipe Suite Design

**Date:** 2026-08-11
**Status:** Approved
**Module:** `cogs/snipe.py`

## Goal

Turn the single message-snipe into a snipe suite: `.snipe`/`.s`, `.esnipe`/`.es` (edited messages), `.rsnipe`/`.rs` (removed reactions). All channel-wide, memory-based, styled consistently with the existing snipe.

## Background

`cogs/snipe.py` currently has a single `.snipe [n]` command backed by a per-channel `deque` of deleted-message snapshots (max 5 per channel — this will grow to 50). Snapshots capture content, author, avatar, attachments, sticker, deleted-at timestamp, and reply reference. The embed renders only the first attachment (or sticker) as the image.

## Feature Set

### 1. Message snipe — `.snipe` / `.s` [n]

- Add `aliases=["s"]` to the existing `snipe` command.
- Keep existing snapshot behavior (content, author, avatar, attachments, sticker, deleted_at, reply reference).
- Render **all** attachments, not just the first:
  - First attachment or sticker → embed image (existing behavior).
  - Remaining attachments → inline-less embed fields, each `[image N](url)` (or by filename), truncated reasonably.
- No other behavioral changes.

### 2. Editsnipe — `.esnipe` / `.es` [n]

- New listener `on_message_edit(before, after)`:
  - Skip if `after.author.bot`, `before.guild is None`, or `before.content == after.content`.
  - Snapshot: `before` content, author, avatar, attachments, stickers, edited-at timestamp, message jump URL.
  - Store in per-channel deque (`_edited`), max 50, same key scheme as deleted.
- Command:
  - `n` default 1, guard `n < 1` → "`n` has to be at least 1".
  - Empty deque or `n > len` → "nothing edited here. yet."
  - Embed: author (icon = avatar), description shows the `before` content or `*no text*` — the after-content is still visible in chat, so only the pre-edit content is worth showing. Show edit timestamp.
  - Attachments/stickers from the `before` message rendered the same way as snipe (first → image, rest → fields).
  - Footer: `edit #n · edited Xs ago`.

### 3. Reactionsnipe — `.rsnipe` / `.rs` [n]

- New listener `on_reaction_remove(reaction, user)`:
  - Skip if `user.bot` or `reaction.message.guild is None`.
  - Snapshot: emoji (str — handles custom emoji via `reaction.emoji`), message author, message jump URL, channel id, reactor, removed-at timestamp.
  - Store in per-channel deque (`_reactions`), max 50.
- Command:
  - `n` default 1, same guards as above.
  - Embed: author = reactor (icon = reactor avatar), description `removed :emoji:` on `@author`'s [message](jump url). Timestamp = removed-at.
  - Footer: `rsnipe #n · removed Xs ago`.

## Shared Conventions

- All three deques keyed by `(guild_id, channel_id)`.
- `_SNIPE_KEEP = 50` — all three deques keep the last 50 snapshots per channel (a lot, not a few; memory cost is trivial since snapshots are small dicts).
- Same embed color via `get_embed_color(ctx.guild.id)`, UTC timestamps, `help_meta` decorator with usage/desc/examples/params/note for each command, section "Fun".
- Guild-only guard: "this command only works in servers."
- The "nothing ... here. yet." empty state messages adapt per command ("nothing deleted here. yet." / "nothing edited here. yet." / "no reactions removed here. yet.").

## Out of Scope

- Persistent (database-backed) snipes.
- Server-wide sniping.
- Reactions on deleted messages.
- Any changes outside `cogs/snipe.py` (help metadata flows through existing `get_help_meta`).
