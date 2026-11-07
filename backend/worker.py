"""Worker entry point. A placeholder until M0B builds the processing loop."""

from app.utils.logger import configure_logging, get_logger


def main() -> None:
    """Log that no worker exists yet, then exit."""
    configure_logging()
    get_logger(__name__).info("Worker placeholder: processing arrives in M0B")


if __name__ == "__main__":
    main()
