import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

LOG_DIR  = os.path.join(os.path.dirname(__file__), "logs")
LOG_FILE = os.path.join(LOG_DIR, "agent.log")

os.makedirs(LOG_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# JSON formatter — one JSON object per line (JSONL)
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level":     record.levelname,
            "event":     record.getMessage(),
        }
        # Merge any extra fields passed via the `extra` kwarg
        for key, value in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
                "taskName"
            }:
                entry[key] = value
        return json.dumps(entry, default=str)


# ---------------------------------------------------------------------------
# Logger setup
# ---------------------------------------------------------------------------

def setup_logger(level: str = "INFO") -> logging.Logger:
    """
    Configure and return the agent logger.
    - Writes structured JSON to logs/agent.log (rotating, 5 MB × 3 files).
    - Writes WARNING+ to the console as plain text (avoids duplicating Rich output).
    """
    logger = logging.getLogger("sql_agent")

    if logger.handlers:
        return logger   # already configured

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # File handler — rotating JSON logs
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(_JsonFormatter())
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    # Console handler — warnings and errors only (Rich handles normal output)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    console_handler.setLevel(logging.WARNING)
    logger.addHandler(console_handler)

    return logger


# ---------------------------------------------------------------------------
# Module-level logger instance — import this everywhere
# ---------------------------------------------------------------------------

log = setup_logger()
