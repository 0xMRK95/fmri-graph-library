from __future__ import annotations

from rich.console import Console
from rich.logging import RichHandler
import logging


def setup_logging(level: str = "INFO") -> logging.Logger:
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True)],
    )
    return logging.getLogger("field-cartography")


def get_console() -> Console:
    return Console()
