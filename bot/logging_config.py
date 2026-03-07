import os
import sys
from contextvars import ContextVar
from pathlib import Path

from loguru import logger

# --- Context variables injected into every log record ---
_ctx_task_id: ContextVar[str | None] = ContextVar("task_id", default=None)
_ctx_agent_name: ContextVar[str | None] = ContextVar("agent_name", default=None)
_ctx_step: ContextVar[str | None] = ContextVar("step", default=None)

# Secret values that must be redacted before emission
_SECRETS: list[str] = []


def bind_context(
    task_id: str | None = None,
    agent_name: str | None = None,
    step: str | None = None,
) -> None:
    """Set context variables for the current async task/coroutine."""
    if task_id is not None:
        _ctx_task_id.set(task_id)
    if agent_name is not None:
        _ctx_agent_name.set(agent_name)
    if step is not None:
        _ctx_step.set(step)


def _redact(text: str) -> str:
    for secret in _SECRETS:
        text = text.replace(secret, "***")
    return text


def _context_patcher(record: dict) -> None:
    """Inject contextvars into the log record's extra dict."""
    record["extra"].setdefault("task_id", _ctx_task_id.get())
    record["extra"].setdefault("agent_name", _ctx_agent_name.get())
    record["extra"].setdefault("step", _ctx_step.get())


def _make_serializer():
    """Return a sink function that serializes to JSON and redacts secrets."""

    def sink(message) -> None:
        record = message.record
        # Build the JSON payload manually to control field order and redaction
        import json
        import datetime

        extra = record["extra"]
        payload: dict = {
            "timestamp": record["time"].astimezone(datetime.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f"
            )[:-3] + "Z",
            "level": record["level"].name,
            "logger": record["name"],
            "message": _redact(record["message"]),
        }

        # Optional context fields — omit if None
        for field in ("task_id", "agent_name", "step"):
            val = extra.get(field)
            if val is not None:
                payload[field] = _redact(str(val))

        # Event-specific extra fields (duration_ms, tokens_*, success, …)
        skip = {"task_id", "agent_name", "step"}
        for key, val in extra.items():
            if key not in skip and val is not None:
                payload[key] = _redact(str(val)) if isinstance(val, str) else val

        print(json.dumps(payload), flush=True)

    return sink


def _make_file_sink(log_path: str):
    """Return a loguru-compatible sink callable that writes JSON to a file with rotation."""
    import json
    import datetime

    _fh = None

    def _open():
        nonlocal _fh
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        _fh = open(log_path, "a", encoding="utf-8")

    def sink(message) -> None:
        nonlocal _fh
        if _fh is None:
            _open()
        record = message.record
        extra = record["extra"]
        payload: dict = {
            "timestamp": record["time"].astimezone(datetime.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f"
            )[:-3] + "Z",
            "level": record["level"].name,
            "logger": record["name"],
            "message": _redact(record["message"]),
        }
        for field in ("task_id", "agent_name", "step"):
            val = extra.get(field)
            if val is not None:
                payload[field] = _redact(str(val))
        skip = {"task_id", "agent_name", "step"}
        for key, val in extra.items():
            if key not in skip and val is not None:
                payload[key] = _redact(str(val)) if isinstance(val, str) else val

        _fh.write(json.dumps(payload) + "\n")
        _fh.flush()

    return sink


def setup_logging(secrets: list[str] | None = None, log_dir: str = "logs") -> None:
    """Configure loguru with JSON stdout + rotating file sinks.

    Call once at process startup (from main.py) before any other logging.
    """
    global _SECRETS

    if secrets:
        _SECRETS = [s for s in secrets if s]

    # Remove loguru's default stderr handler
    logger.remove()

    # Register the context patcher so it runs on every record
    logger.configure(patcher=_context_patcher)

    # Stdout sink (JSON)
    logger.add(_make_serializer(), level="INFO", colorize=False, format="{message}")

    # File sink (JSON, rotation by size)
    log_path = Path(log_dir) / "bot.log"
    logger.add(
        _make_file_sink(str(log_path)),
        level="INFO",
        rotation="50 MB",
        retention=5,
        colorize=False,
        format="{message}",
    )

    # Bridge stdlib logging into loguru so third-party libs (aiogram, openai, …)
    # also emit JSON
    import logging

    class _InterceptHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            # Find caller from where the log originated
            try:
                level = logger.level(record.levelname).name
            except ValueError:
                level = record.levelno

            frame, depth = sys._getframe(6), 6
            while frame and frame.f_code.co_filename == logging.__file__:
                frame = frame.f_back
                depth += 1

            logger.opt(depth=depth, exception=record.exc_info).log(
                level, record.getMessage()
            )

    logging.basicConfig(handlers=[_InterceptHandler()], level=logging.INFO, force=True)
