# Workspace Rules

### Bot Aesthetic & Theme Guidelines
When adding or modifying commands and responses for this bot, you **MUST** strictly adhere to its specific voice and aesthetic:

1. **Bot Voice and Formatting (Lowercase & Minimal Text)**:
   - The bot speaks in a very casual, minimalistic, and nonchalant voice. 
   - **Always use all-lowercase text** for errors, warnings, informational messages, and general responses (e.g., `"no perms"`, `"invalid color"`, `"-# avatar cleared"`). 
   - **Do not** use proper capitalization or unnecessary punctuation (no periods at the end of sentences unless stylistically required, no exclamation marks).

2. **Custom Reactions (Silent Confirmations)**:
   - Always prefer reacting to a message over sending a chat message to confirm simple state changes.
   - **Success/Enable**: React with `<:pinklotus:1263556545686405170>` or `<:7079verifiedblacksimplified:1255031445806780467>`.
   - **Failure/Disable/Clear**: React with `<:redlotus:1263556248310386800>`.

3. **Embed Minimalism**:
   - If a chat embed is strictly required (e.g., `.setcolor` outputting the new color), keep it **extremely minimal and entirely lowercase**.
   - Example: `description="nya?"`
   - **Never** use bulky titles, verbose descriptions, or `✅`/`❌` emojis inside the embed text. The UI should remain sleek, clean, and unobtrusive.

### Command Help Metadata (REQUIRED)
Every new or modified command MUST ship complete `@help_meta` metadata:
- `usage`: starts with `.`, matches the real signature
- `desc`: one line, ≤ 100 chars, lowercase, says what the command does
- `section`: always set to the cog's existing section label
- `examples`: ≤ 3, only where genuinely useful
- `params`: one entry per documented argument, `required` matches the default
- `note`: restrictions (admin/owner/staff/cooldown) or placeholder explanations
When touching a command, verify `.help <command>` renders sensibly. Never leave a
command without `@help_meta` — the bot warns on missing metadata at startup.
Metadata changes only: never alter behavior, aliases, or signatures in a help sweep.
