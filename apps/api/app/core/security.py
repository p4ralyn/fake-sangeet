import hashlib
import hmac
import secrets


def new_token(nbytes: int = 32) -> str:
    """Opaque, unguessable token. Used for session ids, CSRF tokens, OIDC state."""
    return secrets.token_urlsafe(nbytes)


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def hash_value(value: str) -> str:
    """Non-reversible fingerprint for storing IP/UA without storing PII."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def client_ip_prefix(ip: str) -> str:
    """Soft-bind to a /24 — campus wifi roams, so this must not be a hard block."""
    parts = ip.split(".")
    return ".".join(parts[:3]) if len(parts) == 4 else ip
