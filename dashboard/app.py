# dashboard/app.py
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from cogs.warns import WARNS_FILE
from utils import (
    AUDIT_FILE,
    CONFESSIONS_FILE,
    load_json,
    log_audit,
    save_json,
)

from . import config
from .auth import NotAuthenticatedError


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' https://cdn.discordapp.com; frame-ancestors 'none'"
        )
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp


def create_app(bot=None) -> FastAPI:
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles
    from fastapi.templating import Jinja2Templates

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.bot = bot
    app.add_middleware(SecurityHeadersMiddleware)

    base = Path(__file__).parent
    app.state.templates = Jinja2Templates(directory=str(base / "templates"))
    app.mount("/static", StaticFiles(directory=str(base / "static")), name="static")

    @app.exception_handler(NotAuthenticatedError)
    async def _not_auth(request: Request, exc: NotAuthenticatedError):
        return RedirectResponse("/login", status_code=303)

    @app.get("/healthz")
    async def healthz():
        return JSONResponse({"ok": True})

    # ── Auth routes ──────────────────────────────────────────
    from fastapi import APIRouter, Depends

    from . import auth
    from .security import login_limiter

    router = APIRouter()

    @router.get("/whoami")
    async def whoami(uid: int = Depends(auth.require_admin)):
        return {"user_id": uid}

    @router.get("/login")
    async def login(request: Request, go: str = ""):
        ip = request.client.host if request.client else "?"
        if not go and not login_limiter.allow(f"login:{ip}"):
            raise HTTPException(status_code=429, detail="slow down")
        if not (config.DISCORD_CLIENT_ID and config.OAUTH_REDIRECT_URI):
            return app.state.templates.TemplateResponse(
                request, "login.html", {"configured": False}
            )
        state = auth.make_state()
        resp = RedirectResponse(auth.authorize_url(state), status_code=303)
        resp.set_cookie(
            config.STATE_COOKIE_NAME,
            state,
            max_age=600,
            httponly=True,
            samesite="lax",
        )
        return resp

    @router.get("/callback")
    async def callback(request: Request, code: str = "", state: str = ""):
        ip = request.client.host if request.client else "?"
        if not login_limiter.allow(f"cb:{ip}"):
            raise HTTPException(status_code=429, detail="slow down")
        cookie_state = request.cookies.get(config.STATE_COOKIE_NAME, "")
        if not auth.verify_state(state, cookie_state):
            raise HTTPException(status_code=403, detail="bad state")
        token = await auth.exchange_code(code)
        user = await auth.fetch_discord_user(token) if token else None
        if not user:
            raise HTTPException(status_code=502, detail="discord exchange failed")
        from utils import CREATOR_ID

        if int(user["id"]) != CREATOR_ID:
            raise HTTPException(status_code=403, detail="not allowed")
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(
            config.SESSION_COOKIE,
            auth.session_value(int(user["id"])),
            max_age=config.SESSION_MAX_AGE,
            httponly=True,
            secure=True,
            samesite="lax",
        )
        resp.delete_cookie(config.STATE_COOKIE_NAME)
        return resp

    @router.post("/logout")
    async def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(config.SESSION_COOKIE)
        return resp

    # ── Pages ────────────────────────────────────────────────
    from .stats import overview_stats

    @router.get("/")
    async def overview(request: Request, uid: int = Depends(auth.require_admin)):
        return app.state.templates.TemplateResponse(
            request, "overview.html", {"stats": overview_stats(app.state.bot)}
        )

    @router.get("/guilds")
    async def guilds(request: Request, uid: int = Depends(auth.require_admin)):
        rows = [
            {
                "id": g.id,
                "name": g.name,
                "members": g.member_count or 0,
                "icon": g.icon.url if g.icon else None,
            }
            for g in sorted(app.state.bot.guilds, key=lambda x: -(x.member_count or 0))
        ]
        return app.state.templates.TemplateResponse(request, "guilds.html", {"rows": rows})

    @router.get("/guilds/{guild_id:int}")
    async def guild_detail(
        request: Request, guild_id: int, uid: int = Depends(auth.require_admin)
    ):
        g = app.state.bot.get_guild(guild_id)
        if g is None:
            raise HTTPException(status_code=404, detail="unknown guild")
        from utils import get_config

        gc = get_config().get(str(guild_id), {})
        return app.state.templates.TemplateResponse(
            request,
            "guild_detail.html",
            {
                "g": g,
                "channel_count": len(g.channels),
                "role_count": len(g.roles),
                "ai_channels": gc.get("ai_channels", []),
                "prefix_whitelist": gc.get("whitelist", []),
            },
        )

    # ── Moderation ───────────────────────────────────────────
    @router.get("/moderation")
    async def moderation(request: Request, uid: int = Depends(auth.require_admin)):
        warns = load_json(WARNS_FILE) or {}
        confs = load_json(CONFESSIONS_FILE) or {}
        warn_rows = []
        for gid, users in warns.items():
            for wid, entries in (users or {}).items():
                for i, e in enumerate(entries or []):
                    warn_rows.append(
                        {
                            "guild_id": gid,
                            "user_id": wid,
                            "idx": i,
                            "reason": (e or {}).get("reason", "?"),
                            "when": str((e or {}).get("timestamp", "?")),
                        }
                    )
        conf_rows = [
            {
                "key": k,
                "text": (v or {}).get("text", ""),
                "when": str((v or {}).get("timestamp", "")),
                "replies": len((v or {}).get("replies", [])),
            }
            for k, v in confs.items()
        ]
        names = {g.id: g.name for g in app.state.bot.guilds}
        return app.state.templates.TemplateResponse(
            request,
            "moderation.html",
            {
                "warn_rows": warn_rows,
                "conf_rows": conf_rows,
                "names": names,
                "csrf": auth.csrf_token(uid),
            },
        )

    @router.post("/moderation/warns/delete")
    async def del_warn(
        uid: int = Depends(auth.require_admin),
        guild_id: str = Form(""),
        user_id: str = Form(""),
        idx: int = Form(-1),
        csrf: str = Form(""),
    ):
        if not auth.check_csrf(uid, csrf):
            raise HTTPException(status_code=403, detail="bad csrf")
        warns = load_json(WARNS_FILE) or {}
        entries = warns.get(guild_id, {}).get(user_id, [])
        if 0 <= idx < len(entries):
            removed = entries.pop(idx)
            warns.setdefault(guild_id, {})[user_id] = entries
            save_json(WARNS_FILE, warns)
            log_audit("dashboard.warn_delete", guild_id, uid, f"{user_id}: {removed}")
        return RedirectResponse("/moderation", status_code=303)

    @router.post("/moderation/confessions/delete")
    async def del_conf(
        uid: int = Depends(auth.require_admin),
        key: str = Form(""),
        csrf: str = Form(""),
    ):
        if not auth.check_csrf(uid, csrf):
            raise HTTPException(status_code=403, detail="bad csrf")
        confs = load_json(CONFESSIONS_FILE) or {}
        if key in confs:
            confs.pop(key)
            save_json(CONFESSIONS_FILE, confs)
            log_audit("dashboard.confession_delete", 0, uid, key)
        return RedirectResponse("/moderation", status_code=303)

    # ── Data ─────────────────────────────────────────────────
    from .data_access import giveaways_view, leaderboard, reminders_view, set_xp

    @router.get("/data")
    async def data_page(request: Request, uid: int = Depends(auth.require_admin)):
        return app.state.templates.TemplateResponse(
            request,
            "data.html",
            {
                "rows": leaderboard(),
                "giveaways": giveaways_view(),
                "reminders": reminders_view(),
                "csrf": auth.csrf_token(uid),
            },
        )

    @router.post("/data/xp")
    async def data_xp(
        uid: int = Depends(auth.require_admin),
        user_id: str = Form(""),
        guild_id: str = Form(""),
        xp: int = Form(0),
        level: int = Form(0),
        csrf: str = Form(""),
    ):
        if not auth.check_csrf(uid, csrf):
            raise HTTPException(status_code=403, detail="bad csrf")
        if set_xp(user_id, guild_id, xp, level):
            log_audit("dashboard.xp_set", guild_id, uid, f"{user_id}: xp={xp} lvl={level}")
        return RedirectResponse("/data", status_code=303)

    # ── Admin ────────────────────────────────────────────────
    import asyncio
    import re as _re
    import subprocess

    from . import config as cfg
    from .logs import attach_log_ring, recent_logs

    attach_log_ring()
    EXT_RE = _re.compile(r"^cogs\.[a-z_]+$")

    @router.get("/admin")
    async def admin_page(request: Request, uid: int = Depends(auth.require_admin)):
        exts = sorted(app.state.bot.extensions.keys()) if app.state.bot else []
        return app.state.templates.TemplateResponse(
            request,
            "admin.html",
            {"exts": exts, "csrf": auth.csrf_token(uid)},
        )

    @router.post("/admin/cogs")
    async def admin_cogs(
        uid: int = Depends(auth.require_admin),
        ext: str = Form(""),
        action: str = Form(""),
        csrf: str = Form(""),
    ):
        if not auth.check_csrf(uid, csrf):
            raise HTTPException(status_code=403, detail="bad csrf")
        if not EXT_RE.match(ext) or action not in {"load", "unload", "reload"}:
            raise HTTPException(status_code=400, detail="bad input")
        fn = {
            "load": app.state.bot.load_extension,
            "unload": app.state.bot.unload_extension,
            "reload": app.state.bot.reload_extension,
        }[action]
        try:
            await fn(ext)
            result = f"OK {action} {ext}"
        except Exception as e:
            result = f"FAIL {e}"
        log_audit("dashboard.cog_" + action, 0, uid, f"{ext}: {result}")
        from urllib.parse import quote

        return RedirectResponse(f"/admin?msg={quote(result)}", status_code=303)

    @router.post("/admin/restart")
    async def admin_restart(
        uid: int = Depends(auth.require_admin), csrf: str = Form("")
    ):
        if not auth.check_csrf(uid, csrf):
            raise HTTPException(status_code=403, detail="bad csrf")
        log_audit("dashboard.restart", 0, uid, cfg.RESTART_CMD)

        def _kick():
            subprocess.Popen(cfg.RESTART_CMD.split(), start_new_session=True)

        asyncio.get_running_loop().call_later(1.0, _kick)
        return RedirectResponse("/admin?msg=restarting", status_code=303)

    @router.get("/admin/logs/recent")
    async def logs_recent(n: int = 200, uid: int = Depends(auth.require_admin)):
        return JSONResponse({"lines": recent_logs(min(n, 500))})

    app.include_router(router)
    return app
