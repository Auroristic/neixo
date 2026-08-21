# dashboard/runner.py
import asyncio
import logging

import uvicorn

from . import config
from .app import create_app


def start_dashboard(bot):
    """Start the web dashboard as a background task. Never raises."""
    if not config.DASHBOARD_ENABLED:
        return None
    try:
        app = create_app(bot)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=config.DASHBOARD_HOST,
                port=config.DASHBOARD_PORT,
                log_level="warning",
                lifespan="off",
            )
        )
        task = asyncio.create_task(server.serve(), name="dashboard")
        logging.info(
            f"Dashboard starting on http://{config.DASHBOARD_HOST}:{config.DASHBOARD_PORT}"
        )
        return task
    except Exception:
        logging.exception("failed to start dashboard")
        return None
