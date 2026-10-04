"""
Log redaction for secret-bearing request URLs and headers.

httpx logs every request at INFO as "HTTP Request: GET <full url>", and
HTTPStatusError messages embed the same URL. NASA FIRMS puts its MAP_KEY in
the URL *path* (/api/area/csv/<MAP_KEY>/...), api.nasa.gov takes api_key as a
query param, and Earthdata uses a bearer token, so any of them can reach
CloudWatch unless log records are scrubbed before a handler formats them.

install() wraps the process-wide LogRecord factory, so every logger (httpx,
httpcore, tenacity, ours) and every exception traceback is redacted.
"""
from __future__ import annotations

import logging
import os
import re
import traceback
from typing import Callable, List, Optional

REDACTED = "REDACTED"

SECRET_ENV_VARS = (
    "NASA_API_KEY",
    "NASA_KEY",
    "NASA_FIRMS_MAP_KEY",
    "FIRMS_MAP_KEY",
    "FIRMS_API_KEY",
    "EARTHDATA_TOKEN",
    "NASA_EARTHDATA_TOKEN",
    "EDL_TOKEN",
    "EARTHDATA_BEARER_TOKEN",
    "EARTHDATA_PASSWORD",
)

_PATTERNS = [
    # FIRMS area/country/data_availability APIs: /api/<endpoint>/<fmt>/<MAP_KEY>/...
    re.compile(r"(/api/[a-z_]+/(?:csv|json|kml)/)([A-Za-z0-9]{16,})", re.IGNORECASE),
    # FIRMS WMS/WFS map server: /mapserver/<svc>/<layer>/<MAP_KEY>/
    re.compile(r"(/mapserver/[a-z]+/[a-z_]+/)([A-Za-z0-9]{16,})", re.IGNORECASE),
    # Query params: api_key, apikey, map_key, key, token, access_token
    re.compile(
        r"([?&](?:api_key|apikey|map_key|key|token|access_token)=)([^&\s\"'<>]+)",
        re.IGNORECASE,
    ),
    # Authorization header in dict/repr/header-line form
    re.compile(
        r"(authorization[\"']?\s*[:=]\s*[\"']?(?:bearer\s+|basic\s+|token\s+)?)([^\s\"',}]+)",
        re.IGNORECASE,
    ),
    re.compile(r"(bearer\s+)([A-Za-z0-9._~+/=-]{8,})", re.IGNORECASE),
]

_installed = False


def _secret_values() -> List[str]:
    values = []
    for name in SECRET_ENV_VARS:
        value = (os.environ.get(name) or "").strip()
        if len(value) >= 8:
            values.append(value)
    return sorted(set(values), key=len, reverse=True)


def redact(text: Optional[str]) -> Optional[str]:
    """Mask secret path segments, query params, auth headers and known key values."""
    if not text:
        return text
    for pattern in _PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + REDACTED, text)
    for value in _secret_values():
        text = text.replace(value, REDACTED)
    return text


def _redact_record(record: logging.LogRecord) -> None:
    try:
        message = record.getMessage()
    except Exception:
        message = str(record.msg)
    clean = redact(message)
    if clean != message or record.args:
        record.msg = clean
        record.args = ()
    if record.exc_info and record.exc_info[0] is not None:
        record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)).rstrip("\n"))
        record.exc_info = None
    if record.stack_info:
        record.stack_info = redact(record.stack_info)


def install() -> None:
    """Redact every log record created in this process. Idempotent."""
    global _installed
    if _installed:
        return
    previous: Callable[..., logging.LogRecord] = logging.getLogRecordFactory()

    def factory(*args, **kwargs) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        _redact_record(record)
        return record

    logging.setLogRecordFactory(factory)
    _installed = True
