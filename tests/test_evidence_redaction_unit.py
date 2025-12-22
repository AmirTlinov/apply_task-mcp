"""Unit tests for evidence digest + redaction policy."""

from core import Attachment, VerificationCheck


def test_verification_check_redacts_and_hashes():
    raw = {
        "kind": "command",
        "spec": "pytest -q",
        "outcome": "pass",
        "preview": "Authorization: Bearer ghp_ABCDEF1234567890SECRET",
        "details": {"token": "ghp_ABCDEF1234567890SECRET", "nested": {"password": "p@ss"}},
    }
    c1 = VerificationCheck.from_dict(raw)
    c2 = VerificationCheck.from_dict(raw)

    assert "<redacted>" in c1.preview
    assert c1.details["token"] == "<redacted>"
    assert c1.details["nested"]["password"] == "<redacted>"
    assert c1.digest
    assert c1.digest == c2.digest


def test_attachment_redacts_and_hashes():
    raw = {
        "kind": "log",
        "path": "logs/build?token=supersecret",
        "meta": {"authorization": "Bearer sk-THISISSECRET"},
    }
    a = Attachment.from_dict(raw)
    assert "token=<redacted>" in a.path
    assert a.meta["authorization"] == "<redacted>"
    assert a.digest

