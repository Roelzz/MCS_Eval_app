"""Root state for the eval platform."""

import os

import reflex as rx
from loguru import logger

logger.remove()
logger.add(
    sink=lambda msg: print(msg, end=""),
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="{time:DD-MM-YYYY at HH:mm:ss} | {level: <8} | {message}",
)


class State(rx.State):
    """Root application state."""

    def noop(self) -> None:
        """No-op handler for dialog on_open_change true branch."""
        pass
