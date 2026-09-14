from app.core.logging import hash_pii, redact_pii


def test_known_pii_keys_are_hashed():
    out = redact_pii(None, "info", {"event": "login", "net_id": "aarav.s", "roles": ["OWNER"]})
    assert out["net_id"].startswith("sha256:")
    assert "aarav.s" not in out["net_id"]
    assert out["roles"] == ["OWNER"]


def test_emails_are_hashed_even_in_unknown_keys():
    out = redact_pii(
        None,
        "info",
        {"event": "bounce", "detail": "delivery to aarav@university.edu failed"},
    )
    assert "aarav@university.edu" not in out["detail"]
    assert "sha256:" in out["detail"]
    assert "delivery to" in out["detail"]


def test_none_values_survive():
    assert redact_pii(None, "info", {"email": None})["email"] is None


def test_hash_is_stable_and_truncated():
    assert hash_pii("aarav.s") == hash_pii("aarav.s")
    assert hash_pii("aarav.s") != hash_pii("aarav.t")
    assert len(hash_pii("x")) == len("sha256:") + 16
