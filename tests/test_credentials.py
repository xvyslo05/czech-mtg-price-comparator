"""Tests for the per-shop credential resolver (env vars + 1Password fallback)."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from cz_mtg_compare import credentials


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch: pytest.MonkeyPatch):
    """Each test starts with no inherited CZ_MTG_*_USER/PASS env vars and an
    empty 1Password resolution cache."""
    for key in list(__import__("os").environ):
        if key.startswith("CZ_MTG_") and (key.endswith("_USER") or key.endswith("_PASS")):
            monkeypatch.delenv(key, raising=False)
    credentials.reset_secret_cache()
    yield
    credentials.reset_secret_cache()


def test_returns_none_when_both_vars_unset() -> None:
    assert credentials.credentials_for("najada") is None
    assert credentials.has_credentials("najada") is False


def test_returns_literal_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CZ_MTG_NAJADA_USER", "alice@example.com")
    monkeypatch.setenv("CZ_MTG_NAJADA_PASS", "hunter2")
    creds = credentials.credentials_for("najada")
    assert creds is not None
    assert creds.shop_id == "najada"
    assert creds.username == "alice@example.com"
    assert creds.password == "hunter2"
    assert credentials.has_credentials("najada") is True


def test_partial_config_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CZ_MTG_NAJADA_USER", "alice")
    # PASS missing → error
    with pytest.raises(credentials.CredentialError, match="CZ_MTG_NAJADA_PASS"):
        credentials.credentials_for("najada")


def test_empty_string_treated_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CZ_MTG_NAJADA_USER", "   ")
    monkeypatch.setenv("CZ_MTG_NAJADA_PASS", "   ")
    assert credentials.credentials_for("najada") is None


def test_op_reference_resolved_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CZ_MTG_NAJADA_USER", "op://Personal/Najada/username")
    monkeypatch.setenv("CZ_MTG_NAJADA_PASS", "op://Personal/Najada/password")
    calls: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/usr/local/bin/op" if name == "op" else None

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(argv))
        # Last arg is the op:// reference; return the field name as the value.
        ref = argv[-1]
        field = ref.rsplit("/", 1)[-1]
        return subprocess.CompletedProcess(argv, 0, stdout=f"resolved-{field}\n", stderr="")

    with patch.object(credentials.shutil, "which", side_effect=fake_which), patch.object(
        credentials.subprocess, "run", side_effect=fake_run
    ):
        creds = credentials.credentials_for("najada")
        # Second call must hit the cache, not the CLI.
        creds_again = credentials.credentials_for("najada")

    assert creds is not None
    assert creds.username == "resolved-username"
    assert creds.password == "resolved-password"
    assert creds == creds_again
    # Two op:// references → exactly two `op read` invocations across both calls.
    assert len(calls) == 2
    assert all(call[:2] == ["op", "read"] for call in calls)


def test_op_cli_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CZ_MTG_NAJADA_USER", "op://Personal/Najada/username")
    monkeypatch.setenv("CZ_MTG_NAJADA_PASS", "literal-pass")
    with patch.object(credentials.shutil, "which", return_value=None):
        with pytest.raises(credentials.CredentialError, match=r"'op' CLI"):
            credentials.credentials_for("najada")


def test_op_read_failure_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CZ_MTG_NAJADA_USER", "op://Personal/Najada/username")
    monkeypatch.setenv("CZ_MTG_NAJADA_PASS", "literal-pass")

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.CalledProcessError(returncode=1, cmd=argv, output="", stderr="not signed in")

    with patch.object(credentials.shutil, "which", return_value="/usr/local/bin/op"), patch.object(
        credentials.subprocess, "run", side_effect=fake_run
    ):
        with pytest.raises(credentials.CredentialError, match="not signed in"):
            credentials.credentials_for("najada")


def test_op_read_empty_value_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CZ_MTG_NAJADA_USER", "op://Personal/Najada/username")
    monkeypatch.setenv("CZ_MTG_NAJADA_PASS", "literal-pass")

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(argv, 0, stdout="\n", stderr="")

    with patch.object(credentials.shutil, "which", return_value="/usr/local/bin/op"), patch.object(
        credentials.subprocess, "run", side_effect=fake_run
    ):
        with pytest.raises(credentials.CredentialError, match="empty value"):
            credentials.credentials_for("najada")
