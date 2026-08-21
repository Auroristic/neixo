# dashboard/config.py
import os

DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8765"))
DASHBOARD_ENABLED = os.getenv("DASHBOARD_ENABLED", "1") == "1"

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
OAUTH_REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "")
SESSION_SECRET = os.getenv("DASHBOARD_SECRET", "")

SESSION_COOKIE = "neixo_session"
SESSION_MAX_AGE = 7 * 24 * 3600
RESTART_CMD = os.getenv("DASHBOARD_RESTART_CMD", "systemctl restart neixo")
