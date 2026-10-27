# tests/test_obslog.py
from __future__ import annotations

import json
import logging

from app.obslog import JsonFormatter
from app.runs import MemoryRunStore, bind_run


def _record(msg: str = "hola %s", args: tuple = ("mundo",)) -> logging.LogRecord:
    return logging.LogRecord("app.test", logging.INFO, __file__, 1, msg, args, None)


def test_json_formatter_without_run():
    payload = json.loads(JsonFormatter().format(_record()))
    assert payload["message"] == "hola mundo" and payload["level"] == "INFO"
    assert "run_id" not in payload


async def test_json_formatter_includes_run_id():
    store = MemoryRunStore()
    run_id = await store.create("echo", {})
    with bind_run(store, run_id):
        payload = json.loads(JsonFormatter().format(_record()))
    assert payload["run_id"] == run_id
