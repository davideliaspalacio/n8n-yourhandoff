"""Logging estructurado: con LOG_FORMAT=json cada línea sale como JSON con el
run_id del flujo en curso, para correlacionar los logs de Railway con un run."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from .runs import current_run

TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        ctx = current_run.get()
        if ctx is not None:
            payload["run_id"] = ctx.run_id
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(fmt: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if fmt == "json" else logging.Formatter(TEXT_FORMAT))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
