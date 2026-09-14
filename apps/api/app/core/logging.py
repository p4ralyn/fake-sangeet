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

# Suffixes/substrings that make a key sensitive regardless of exact spelling,
# so a new field (e.g. `entra_client_secret`, `smtp_password`) is caught without
# needing a matching entry appended to PII_KEYS by hand.
_SENSITIVE_SUBSTRINGS = ("secret", "password")
_SENSITIVE_SUFFIXES = ("_email", "_net_id", "_netid", "_token")


def hash_pii(value: object) -> str:
    """Stable, non-reversible, log-safe rendering of a PII value."""
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


def _is_sensitive_key(key: str) -> bool:
    k = key.lower()
    if k in PII_KEYS:
        return True
    if any(s in k for s in _SENSITIVE_SUBSTRINGS):
        return True
    return k.endswith(_SENSITIVE_SUFFIXES)


def _redact_value(key: str | None, value: Any) -> Any:
    if value is None:
        return None
    if key is not None and _is_sensitive_key(key):
        return hash_pii(value)
    if isinstance(value, dict):
        return {k: _redact_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        redacted = [_redact_value(None, v) for v in value]
        return redacted if isinstance(value, list) else tuple(redacted)
    if isinstance(value, str) and _EMAIL_RE.search(value):
        return _EMAIL_RE.sub(lambda m: hash_pii(m.group()), value)
    return value


def redact_pii(_logger: Any, _method: str, event_dict: dict) -> dict:
    """structlog processor. Hashes sensitive keys and any inline email address,
    recursing into nested dicts, lists and tuples so PII cannot hide a level down.
    """
    for key, value in list(event_dict.items()):
        event_dict[key] = _redact_value(key, value)
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
