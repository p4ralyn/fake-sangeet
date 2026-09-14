import hashlib
import logging
import re
from typing import Any

import structlog

from app.core.settings import get_settings

# Keys whose values are PII and must never reach a log line.
PII_KEYS = frozenset(
    {
        "email", "to_email", "purchaser_email",
        "net_id", "netid", "bound_net_id", "actor_net_id", "net_id_hint",
        "phone", "payer_phone", "payer_name", "display_name", "full_name",
        "utr", "token", "id_token", "qr_token", "access_token", "refresh_token",
        "code", "code_verifier", "client_secret", "password", "secret_key",
        "ms_object_id", "subject", "session_id", "sid", "claim_code",
    }
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def hash_pii(value: object) -> str:
    """Stable, non-reversible, log-safe rendering of a PII value."""
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


def redact_pii(_logger: Any, _method: str, event_dict: dict) -> dict:
    """structlog processor. Hashes known PII keys and any inline email address."""
    for key, value in list(event_dict.items()):
        if value is None:
            continue
        if key.lower() in PII_KEYS:
            event_dict[key] = hash_pii(value)
        elif isinstance(value, str) and _EMAIL_RE.search(value):
            event_dict[key] = _EMAIL_RE.sub(lambda m: hash_pii(m.group()), value)
    return event_dict


def configure_logging() -> None:
    settings = get_settings()
    level = logging.getLevelNamesMapping()[settings.log_level.upper()]
    logging.basicConfig(format="%(message)s", level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_pii,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
