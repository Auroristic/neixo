# dashboard/config.py
import os
import secrets

DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8765"))
DASHBOARD_ENABLED = os.getenv("DASHBOARD_ENABLED", "1") == "1"

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
OAUTH_REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "")
# Ephemeral fallback keeps sessions signed even if the env var is missing;
# sessions then reset on restart until DASHBOARD_SECRET is configured.
SESSION_SECRET = os.getenv("DASHBOARD_SECRET") or secrets.token_urlsafe(32)

SESSION_COOKIE = "neixo_session"
STATE_COOKIE_NAME = "neixo_oauth_state"
SESSION_MAX_AGE = 7 * 24 * 3600
RESTART_CMD = os.getenv("DASHBOARD_RESTART_CMD", "systemctl restart neixo")
