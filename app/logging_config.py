# app/logging_config.py
"""Structured, timestamped logging. Replaces scattered print() calls."""

import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)-24s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # These libraries are extremely chatty at INFO.
    for noisy in ("httpx", "httpcore", "telegram", "apscheduler", "pymongo",
                  "urllib3", "google", "googleapiclient", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def redact_phone(phone: str) -> str:
    """Log-safe phone number: +9198*****210. Keeps PII out of your logs."""
    if not phone or len(phone) < 6:
        return "***"
    return f"{phone[:5]}{'*' * max(0, len(phone) - 8)}{phone[-3:]}"
