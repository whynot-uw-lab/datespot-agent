"""실행 context와 실행별 JSONL 진단 로그."""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterator, Mapping, TextIO


_LOG_CONTEXT: ContextVar[Mapping[str, object]] = ContextVar(
    "datespot_log_context",
    default={},
)
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(authorization|cookie)\s*[:=]\s*(?:bearer\s+)?[^,\n]+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]+"),
)
_EXCEPTION_URL_PATTERN = re.compile(r"(?i)https?://[^\s,;\]\[<>{}\"']+")
_EXCEPTION_RAW_INPUT_PATTERN = re.compile(
    r"(?im)(\b(?:prompt|criteria|reviews?|review[_ ]?texts?|"
    r"photo[_ ]?urls?|image[_ ]?urls?|프롬프트|평가\s*기준|"
    r"리뷰(?:\s*원문)?|사진\s*URL)\b\s*[:=]\s*)[^\n]+"
)
_EXCEPTION_RAW_INPUT_PREFIX_PATTERN = re.compile(
    r"(?im)\b(?:prompt|criteria|reviews?|review[_ ]?texts?|"
    r"photo[_ ]?urls?|image[_ ]?urls?|프롬프트|평가\s*기준|"
    r"리뷰(?:\s*원문)?|사진\s*URL)\b\s*[:=]\s*"
)
_SENSITIVE_KEYS = {
    "apikey",
    "authorization",
    "cookie",
    "cookies",
    "criteria",
    "headers",
    "imagedata",
    "imageurl",
    "imageurls",
    "photobytes",
    "photourl",
    "photourls",
    "prompt",
    "reviews",
    "reviewtext",
    "reviewtexts",
    "secret",
    "setcookie",
    "token",
}
_REDACTED = "[REDACTED]"


def _camel_case(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _redact_text(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(
            lambda match: (
                f"{match.group(1)}: {_REDACTED}" if match.lastindex else _REDACTED
            ),
            redacted,
        )
    return redacted


def _redact_exception_text(value: str) -> str:
    redacted = _redact_text(value)
    redacted = _EXCEPTION_RAW_INPUT_PATTERN.sub(
        lambda match: f"{match.group(1)}{_REDACTED}",
        redacted,
    )
    return _EXCEPTION_URL_PATTERN.sub(_REDACTED, redacted)


def _redact_exception_message(value: str) -> str:
    redacted = _redact_text(value)
    sensitive_block = _EXCEPTION_RAW_INPUT_PREFIX_PATTERN.search(redacted)
    if sensitive_block is not None:
        redacted = redacted[: sensitive_block.end()] + _REDACTED
    return _EXCEPTION_URL_PATTERN.sub(_REDACTED, redacted)


def _safe_value(value: object, *, key: object | None = None) -> object:
    if key is not None and _normalized_key(key) in _SENSITIVE_KEYS:
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            _camel_case(str(item_key)): _safe_value(
                item_value,
                key=item_key,
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe_value(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, Enum)):
        return value.isoformat() if isinstance(value, datetime) else value.value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(str(value))


class _SafeConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        context = dict(_LOG_CONTEXT.get())
        context.update(dict(getattr(record, "datespot_fields", {})))
        run_id = context.get("run_id", "-")
        component = context.get(
            "component",
            record.name.removeprefix("datespot_agent."),
        )
        event = getattr(record, "datespot_event", "log.message")
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        line = (
            f"{timestamp} {record.levelname} "
            f"[run:{_safe_value(run_id)}] "
            f"[{_safe_value(component)}] {event} "
            f"{_redact_text(record.getMessage())}"
        )
        if record.exc_info:
            raw_error = str(record.exc_info[1])
            safe_error = _redact_exception_message(raw_error)
            raw_traceback = self.formatException(record.exc_info)
            if raw_error:
                raw_traceback = raw_traceback.replace(raw_error, safe_error)
            line += "\n" + _redact_exception_text(raw_traceback)
        return line


class _RunJsonlHandler(logging.Handler):
    def __init__(self, root: Path, failure_stream: TextIO) -> None:
        super().__init__(level=logging.INFO)
        self._root = root
        self._write_lock = threading.Lock()
        self._failure_lock = threading.Lock()
        self._failure_stream = failure_stream
        self._failure_reported = False

    def emit(self, record: logging.LogRecord) -> None:
        try:
            context = dict(_LOG_CONTEXT.get())
            fields = dict(getattr(record, "datespot_fields", {}))
            context.update(fields)
            run_id = context.get("run_id")
            if not isinstance(run_id, str) or not _RUN_ID_PATTERN.fullmatch(run_id):
                return
            event = str(getattr(record, "datespot_event", "log.message"))
            component = context.pop(
                "component",
                record.name.removeprefix("datespot_agent."),
            )
            context.pop("run_id", None)
            payload: dict[str, object] = {
                "timestamp": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "level": record.levelname,
                "event": event,
                "message": _redact_text(record.getMessage()),
                "runId": run_id,
                "component": _safe_value(component),
            }
            payload.update(
                {
                    _camel_case(str(key)): _safe_value(value, key=key)
                    for key, value in context.items()
                }
            )
            if record.exc_info:
                error_type, error, _ = record.exc_info
                payload["errorType"] = error_type.__name__
                raw_error = str(error)
                safe_error = _redact_exception_message(raw_error)
                payload["errorMessage"] = safe_error
                raw_traceback = (
                    self.formatter.formatException(record.exc_info)
                    if self.formatter is not None
                    else logging.Formatter().formatException(record.exc_info)
                )
                if raw_error:
                    raw_traceback = raw_traceback.replace(raw_error, safe_error)
                payload["traceback"] = _redact_exception_text(raw_traceback)
            serialized = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            path = self._root / f"{run_id}.jsonl"
            with self._write_lock:
                self._root.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(serialized)
                    stream.write("\n")
                    stream.flush()
        except Exception:
            self._report_failure_once()

    def _report_failure_once(self) -> None:
        with self._failure_lock:
            if self._failure_reported:
                return
            self._failure_reported = True
            try:
                self._failure_stream.write(
                    "ERROR observability.write.failed 실행별 진단 로그 쓰기 실패\n"
                )
                self._failure_stream.flush()
            except Exception:
                return


class RunLogManager:
    def __init__(
        self,
        root: Path,
        *,
        console: bool = False,
        console_stream: TextIO | None = None,
    ) -> None:
        self.root = root.expanduser()
        self._console_stream = console_stream or sys.stderr
        self._handler = _RunJsonlHandler(self.root, self._console_stream)
        self._console_handler = (
            logging.StreamHandler(self._console_stream) if console else None
        )
        if self._console_handler is not None:
            self._console_handler.setLevel(logging.INFO)
            self._console_handler.setFormatter(_SafeConsoleFormatter())
        self._logger = logging.getLogger("datespot_agent")
        self._previous_level: int | None = None
        self._console_attached = False
        self._force_console = console_stream is not None
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._previous_level = self._logger.level
        if self._logger.getEffectiveLevel() > logging.INFO:
            self._logger.setLevel(logging.INFO)
        self._logger.addHandler(self._handler)
        if self._console_handler is not None and (
            self._force_console or not self._has_real_handler()
        ):
            self._logger.addHandler(self._console_handler)
            self._console_attached = True
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self._logger.removeHandler(self._handler)
        self._handler.close()
        if self._console_handler is not None and self._console_attached:
            self._logger.removeHandler(self._console_handler)
            self._console_handler.close()
            self._console_attached = False
        if self._previous_level is not None:
            self._logger.setLevel(self._previous_level)
        self._started = False

    def _has_real_handler(self) -> bool:
        logger: logging.Logger | None = self._logger
        while logger is not None:
            if any(
                not isinstance(handler, (logging.NullHandler, _RunJsonlHandler))
                for handler in logger.handlers
            ):
                return True
            if not logger.propagate:
                return False
            logger = logger.parent
        return False


@contextmanager
def bind_log_context(**fields: object) -> Iterator[None]:
    context = dict(_LOG_CONTEXT.get())
    context.update(fields)
    token = _LOG_CONTEXT.set(context)
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


def log_event(
    logger: logging.Logger,
    event: str,
    message: str,
    *,
    level: int = logging.INFO,
    exc_info: bool = False,
    **fields: object,
) -> None:
    logger.log(
        level,
        message,
        exc_info=exc_info,
        extra={
            "datespot_event": event,
            "datespot_fields": fields,
        },
    )


__all__ = ["RunLogManager", "bind_log_context", "log_event"]
