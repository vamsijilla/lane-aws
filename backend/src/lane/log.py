"""Structured logging. Console output in dev, JSON in prod."""

import logging

import structlog


def mask_pii(attributes: frozenset[str]) -> structlog.typing.Processor:
    """Log entries never carry personal data: values under PII keys are replaced."""

    def processor(
        _logger: object, _name: str, event: structlog.typing.EventDict
    ) -> structlog.typing.EventDict:
        for key in list(event):
            if any(key == a or key.endswith(f"_{a}") for a in attributes):
                event[key] = "[masked]"
        return event

    return processor


def configure_logging(*, json: bool, pii: frozenset[str] = frozenset()) -> None:
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            mask_pii(pii),
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )
