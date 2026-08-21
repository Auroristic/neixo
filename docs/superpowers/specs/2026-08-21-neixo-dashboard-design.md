# Neixo Dashboard — Design Spec

**Date:** 2026-08-21
**Status:** Approved (pending final user review)
**Servers:** muixo (Germany, primary host) · chappy (Lavalink, untouched) · sobvenger (spare, unused)

## Overview

A single-admin web dashboard for the Neixo Discord bot, embedded in the bot's own process on muixo. User types a domain in a browser, logs in with Discord, sees everything about the bot and can control it. Theme: minimal editorial monochrome (black / white / cream).

## Goals

1. **See:** bot health/stats, guild overview, moderation data, fun/data views
2. **Control:** cog reload/enable/disable, bot restart, live logs; delete warnings/confessions/autoresponses/reaction roles; adjust XP
3. **Security:** top-tier, single-admin — only `CREATOR_ID` may log in
4. **Aesthetics:** black/white/cream modern editorial theme matching bot embed color `#121516`

## Non-Goals (v1)

- Multi-admin support or role-based access
- Ending/deleting giveaways, clearing reminders (view-only)
- Public API, mobile app, separate-process deployment
- Any presence on chappy or sobvenger

## Architecture (Approach A — Embedded)

```
Browser ──HTTPS──> Caddy (:443, auto-TLS) ──> uvicorn 127.0.0.1:8765 ──> FastAPI app
                                                                        ├─ in-memory bot state (latency, cogs, guilds)
                                                                        ├─ SQLite DBs via existing utils layer
                                                                        └─ direct bot method calls (reload cog, etc.)
```

- **FastAPI + uvicorn** launched as an asyncio task inside neixo's process (`setup_hook`), bound to `127.0.0.1` only
- **Caddy** reverse proxy on muixo — only public entry point, automatic Let's Encrypt certs
- **Jinja2 templates + hand-written CSS**, no Node/build step; tiny vanilla JS only where needed (log streaming)
- **Live log viewer:** in-memory ring buffer via a custom `logging.Handler`
- **Domain:** free DuckDNS subdomain (e.g. `neixo.duckdns.org`) + DNS pointing at muixo's IP

### Why embedded (rejected alternatives)

- *Separate process + IPC:* more moving parts, duplicated state, overkill for one admin
- *Mirrored DB:* sync headaches, stale data, YAGNI
- Embedded gives live stats for free, one service to manage, ~30–50 MB extra RAM vs ~365 MB available

## Security

| Layer | Measure |
|---|---|
| Identity | Discord OAuth2 (`identify` scope); hard gate: `user.id == CREATOR_ID`, else flat 403 |
| Session | Signed cookie (itsdangerous), HttpOnly, Secure, SameSite=Lax, 7-day expiry; secret from env |
| Login CSRF | OAuth `state` param validated on callback |
| Form CSRF | Session-bound CSRF token required on every POST |
| Brute force | Rate-limit login/callback routes per IP |
| Headers | CSP `default-src 'self'`, X-Frame-Options DENY, nosniff, Referrer-Policy |
| Network | App binds localhost only; Caddy terminates TLS; firewall allows 80/443 only |
| Audit | Every write action logged (timestamp, action, target) to `data/audit.log` (JSONL) |

Secrets live in `.env`: `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DASHBOARD_SECRET` (session signing), `OAUTH_REDIRECT_URI`.

## Pages

| Route | Purpose | Write actions |
|---|---|---|
| `/login` | Discord OAuth button + callback | — |
| `/` | Overview: uptime, gateway latency, CPU/RAM, guild count, member total, cog health grid, top commands | — |
| `/guilds`, `/guilds/{id}` | Guild list w/ member counts; per-guild settings snapshot | — |
| `/moderation` | Warnings, confessions, autoresponses, reaction roles | delete each |
| `/data` | Leaderboard, XP adjust, giveaways (read-only), reminders (read-only) | XP edit |
| `/admin` | Cog enable/disable/reload grid, restart button, live log stream | all of those |
| `/audit` | Audit log viewer | — |

Restart works via `systemctl restart neixo`; Caddy serves a brief 502 until systemd brings it back.

## Error Handling

- Web task wrapped so no dashboard exception can kill the bot
- Route-level try/except → styled error page; DB writes transactional; failures surface as flash messages
- Bot offline → Caddy returns branded 502 page

## Testing

- pytest suite: unauthenticated redirect, wrong-Discord-ID → 403, CSRF rejection, OAuth state validation, route smoke tests with mock bot objects, audit-log write assertions
- `ruff check` clean; tests run locally pre-push, full suite re-run on muixo after deploy

## Deployment (muixo)

1. New systemd unit `neixo.service` (auto-restart, `EnvironmentFile=.env`) — replaces current bare/tmux process
2. `caddy.service` with Caddyfile: domain → `127.0.0.1:8765`
3. Oracle Cloud: open ingress 80/443 (security list + instance iptables)
4. DuckDNS record → muixo public IP (static on Oracle; set-and-forget)

## Human-Only Steps (user, guided later)

1. Create Discord OAuth2 app → client ID/secret + redirect URL into `.env`
2. Claim DuckDNS subdomain + token
3. Approve firewall ports in Oracle Cloud console if CLI path fails

## Theme

Cream base (`#F6F1E7`), near-black ink (`#121516`), white cards, hairline borders, generous whitespace, system/Inter type stack, rounded corners, subtle shadows, responsive down to mobile.
