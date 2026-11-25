"""JSON logging with explicit run and time context, without global setup."""

import json
import logging
from datetime import UTC, datetime
from typing import TextIO


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        item = {
            "run_id": getattr(record, "run_id", None),
            "module": record.name,
            "level": record.levelname,
            "ts_event": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "sim_elapsed_ms": getattr(record, "sim_elapsed_ms", None),
            "event": getattr(record, "event", record.getMessage()),
            "message": record.getMessage(),
            "error": self.formatException(record.exc_info) if record.exc_info else None,
        }
        return json.dumps(item, ensure_ascii=False, allow_nan=False)


def create_logger(name: str, stream: TextIO) -> logging.Logger:
    logger = logging.Logger(name, logging.INFO)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger
