"""Centralized logging setup for SAT.

Configures the root ``sat`` logger with:
- A **console** handler (``StreamHandler`` → stderr).
- An optional **rotating file** handler (``RotatingFileHandler``).

Both handlers share the same format.  The file handler rotates based on
size (``max_bytes``) and keeps a configurable number of backups
(``backup_count``).
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


_DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    *,
    level: str = "INFO",
    log_file: str = "logs/sat.log",
    max_bytes: int = 5_242_880,   # 5 MB
    backup_count: int = 5,
    fmt: str = _DEFAULT_FORMAT,
    datefmt: str = _DEFAULT_DATE_FORMAT,
) -> None:
    """Configure logging for the entire ``sat`` package.

    Parameters
    ----------
    level:
        Root log level (``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``, ``CRITICAL``).
    log_file:
        Path to the log file.  Parent directories are created automatically.
        Set to an empty string to disable file logging.
    max_bytes:
        Maximum size in bytes before the log file is rotated.
    backup_count:
        Number of rotated backup files to keep.
    fmt:
        Log message format string.
    datefmt:
        Date/time format for ``%(asctime)s``.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    formatter = logging.Formatter(fmt, datefmt=datefmt)

    # Configure the 'sat' logger (parent of all sat.* loggers)
    root_logger = logging.getLogger("sat")
    root_logger.setLevel(numeric_level)

    # Avoid adding duplicate handlers on repeated calls
    root_logger.handlers.clear()

    # ── Console handler ──────────────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # ── Rotating file handler ────────────────────────────────────────────
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = RotatingFileHandler(
            filename=str(log_path),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
