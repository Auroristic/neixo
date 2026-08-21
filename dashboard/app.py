# dashboard/app.py
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from . import config  # noqa: F401


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


class NotAuthenticated(Exception):  # noqa: N818
    pass


def create_app(bot=None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.bot = bot
    app.add_middleware(SecurityHeadersMiddleware)

    @app.exception_handler(NotAuthenticated)
    async def _not_auth(request: Request, exc: NotAuthenticated):
        return RedirectResponse("/login", status_code=303)

    @app.get("/healthz")
    async def healthz():
        return JSONResponse({"ok": True})

    @app.get("/")
    async def root():
        raise NotAuthenticated()

    # later tasks register routers here
    return app
