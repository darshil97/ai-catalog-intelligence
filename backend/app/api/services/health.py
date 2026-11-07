"""Builds the health response."""

from app.config import get_app_config


def get_health() -> dict[str, str]:
    """Return status and version from config only.

    UptimeRobot calls this every few minutes. A database query here would stop Neon
    from ever suspending and use up its free compute hours.
    """
    config = get_app_config()
    return {
        "status": "ok",
        "environment": config["environment"],
        "code_sha": config["code_sha"],
    }
