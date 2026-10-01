"""The NetID rule at ingestion: two letters, then three digits. Nothing else.

This replaces the earlier client rule that a local part on @snu.edu.in was the
identity "whatever its shape". A ticket bound to a malformed NetID is a ticket
nobody can sign in to collect, so a value that is not exactly `aa123` after
cleaning is unusable rather than trusted.
"""
import pytest

from app.services.ingestion.normalise import normalise_identity, split_packed


@pytest.mark.parametrize(
    ("raw", "net_id", "status"),
    [
        ("ab123@snu.edu.in", "ab123", "OK"),
        ("  AB123@SNU.EDU.IN ", "ab123", "OK"),
        # Somebody hit space before the @. Still them, but worth an admin's eye.
        ("zz103 @snu.edu.in", "zz103", "SALVAGED_WHITESPACE"),
        ("Zz105", "zz105", "SALVAGED_NO_DOMAIN"),
        ("zz106snu.edu.in", "zz106", "SALVAGED_GLUED"),
        ("zz104@gmail.com", "zz104", "SALVAGED_BAD_DOMAIN"),
        ("zz102@snu.edu.un", "zz102", "SALVAGED_BAD_DOMAIN"),
    ],
)
def test_a_value_that_reduces_to_aa123_is_that_person(raw, net_id, status):
    identity = normalise_identity(raw)
    assert identity.net_id == net_id
    assert identity.status == status
    assert identity.email == f"{net_id}@snu.edu.in"


@pytest.mark.parametrize(
    "raw",
    [
        # Right domain, wrong shape: no longer authoritative.
        "zzzz107@snu.edu.in",
        "a1234@snu.edu.in",
        "abc12@snu.edu.in",
        "ab1234@snu.edu.in",
        "student@snu.edu.in",
        # A personal address that merely contains digits is not a NetID.
        "student14058@gmail.com",
        # A glued value must not be truncated into somebody else's NetID.
        "ab1234snu.edu.in",
        "2610110320",
    ],
)
def test_anything_else_is_unusable(raw):
    identity = normalise_identity(raw)
    assert identity.net_id is None
    assert identity.status == "UNUSABLE"
    assert not identity.usable


def test_a_packed_cell_keeps_only_strict_netids():
    assert split_packed("PK944 & zq202") == ["pk944", "zq202"]
    assert split_packed("pk944. zq202") == ["pk944", "zq202"]
    # A four-letter or four-digit fragment is dropped, never trimmed to fit.
    assert split_packed("abcd123 & pk944") == ["pk944"]
    assert split_packed("ab1234 / zq202") == ["zq202"]
