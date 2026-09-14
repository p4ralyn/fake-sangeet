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


def test_nested_dict_values_are_redacted():
    out = redact_pii(
        None,
        "info",
        {"event": "action", "actor": {"net_id": "aarav.s", "email": "aarav.s@university.edu"}},
    )
    assert out["actor"]["net_id"].startswith("sha256:")
    assert out["actor"]["email"].startswith("sha256:")
    assert "aarav.s" not in out["actor"]["net_id"]
    assert "aarav.s@university.edu" not in out["actor"]["email"]


def test_list_of_emails_is_redacted():
    out = redact_pii(
        None,
        "info",
        {"event": "bounce", "recipients": ["a@university.edu", "b@university.edu"]},
    )
    assert all("@university.edu" not in r for r in out["recipients"])
    assert all(r.startswith("sha256:") for r in out["recipients"])


def test_real_secret_field_names_are_redacted():
    out = redact_pii(
        None,
        "info",
        {"event": "config", "entra_client_secret": "abc123", "smtp_password": "hunter2"},
    )
    assert out["entra_client_secret"].startswith("sha256:")
    assert out["smtp_password"].startswith("sha256:")
    assert "abc123" not in out["entra_client_secret"]
    assert "hunter2" not in out["smtp_password"]


def test_error_code_survives_while_code_is_hashed():
    out = redact_pii(
        None,
        "info",
        {"event": "app_error", "error_code": "VALIDATION_ERROR", "code": "some-oidc-code"},
    )
    assert out["error_code"] == "VALIDATION_ERROR"
    assert out["code"].startswith("sha256:")
    assert "some-oidc-code" not in out["code"]
