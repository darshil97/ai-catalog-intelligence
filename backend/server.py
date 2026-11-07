"""API entry point."""

import uvicorn

from app.config import get_api_config


def main() -> None:
    """Start the API."""
    config = get_api_config()
    # Uvicorn's own logging would bypass the project format. The middleware logs each request.
    uvicorn.run(
        "app.api.main:app",
        host=config["host"],
        port=config["port"],
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
