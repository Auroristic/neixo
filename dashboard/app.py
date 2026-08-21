# dashboard/app.py
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

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

    # ── Temporary root until the overview page lands ─────────
    @router.get("/")
    async def root():
        raise NotAuthenticatedError()

    app.include_router(router)
    return app
