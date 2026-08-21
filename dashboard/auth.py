# dashboard/auth.py
import secrets
from urllib.parse import quote

import aiohttp
from fastapi import HTTPException, Request
from itsdangerous import BadSignature, TimestampSigner

from . import config

_signer = TimestampSigner(config.SESSION_SECRET.encode())


class NotAuthenticatedError(Exception):
    """Raised when a request has no valid session; converted to a /login redirect."""


def session_value(user_id: int) -> str:
    return _signer.sign(str(user_id)).decode()


def read_session(request: Request) -> int | None:
    raw = request.cookies.get(config.SESSION_COOKIE)
    if not raw:
        return None
    try:
        return int(_signer.unsign(raw, max_age=config.SESSION_MAX_AGE))
    except (BadSignature, ValueError):
        return None


async def require_admin(request: Request) -> int:
    from utils import CREATOR_ID

    uid = read_session(request)
    if uid is None:
        raise NotAuthenticatedError()
    if uid != CREATOR_ID:
        raise HTTPException(status_code=403, detail="not allowed")
    return uid


def make_state() -> str:
    return _signer.sign(secrets.token_urlsafe(16)).decode()


def verify_state(state: str, cookie_state: str) -> bool:
    if not state or not cookie_state or state != cookie_state:
        return False
    try:
        _signer.unsign(state, max_age=600)
        return True
    except BadSignature:
        return False


def authorize_url(state: str) -> str:
    return (
        "https://discord.com/oauth2/authorize"
        f"?client_id={config.DISCORD_CLIENT_ID}"
        "&response_type=code&scope=identify&prompt=consent"
        f"&redirect_uri={quote(config.OAUTH_REDIRECT_URI, safe='')}"
        f"&state={quote(state, safe='')}"
    )


async def exchange_code(code: str) -> str | None:
    data = {
        "client_id": config.DISCORD_CLIENT_ID,
        "client_secret": config.DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.OAUTH_REDIRECT_URI,
    }
    async with aiohttp.ClientSession() as s:
        async with s.post("https://discord.com/api/oauth2/token", data=data) as r:
            if r.status != 200:
                return None
            return (await r.json()).get("access_token")


async def fetch_discord_user(access_token: str) -> dict | None:
    async with aiohttp.ClientSession() as s:
        async with s.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        ) as r:
            if r.status != 200:
                return None
            return await r.json()


def csrf_token(user_id: int) -> str:
    return _signer.sign(f"csrf:{user_id}").decode()


def check_csrf(user_id: int, token: str) -> bool:
    try:
        return (
            _signer.unsign(token, max_age=config.SESSION_MAX_AGE).decode()
            == f"csrf:{user_id}"
        )
    except BadSignature:
        return False
