"""Per-shop credential resolution with optional 1Password CLI integration.

Each shop reads its credentials from two env vars:

    CZ_MTG_<SHOP>_USER
    CZ_MTG_<SHOP>_PASS

Either value may be a literal string or a 1Password secret reference of the form
``op://Vault/Item/Field``. References are resolved on first access by shelling
out to the ``op`` CLI, and the resolved secret is cached in-process for the
remainder of the run so each ``op read`` happens at most once.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from threading import Lock

log = logging.getLogger(__name__)

OP_REFERENCE_PREFIX = "op://"
_OP_READ_TIMEOUT_S = 10


class CredentialError(RuntimeError):
    """Failed to resolve a shop credential."""


@dataclass(frozen=True)
class ShopCredentials:
    shop_id: str
    username: str
    password: str


_cache_lock = Lock()
_resolved_secrets: dict[str, str] = {}


def _resolve_value(raw: str, *, var_name: str) -> str:
    if not raw.startswith(OP_REFERENCE_PREFIX):
        return raw
    with _cache_lock:
        cached = _resolved_secrets.get(raw)
        if cached is not None:
            return cached
        if shutil.which("op") is None:
            raise CredentialError(
                f"{var_name}={raw!r} is a 1Password reference but the 'op' CLI "
                "isn't installed or isn't on PATH"
            )
        try:
            result = subprocess.run(  # noqa: S603 — argv is fully controlled
                ["op", "read", "--no-newline", raw],
                check=True,
                capture_output=True,
                text=True,
                timeout=_OP_READ_TIMEOUT_S,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            raise CredentialError(
                f"`op read {raw}` failed (exit {exc.returncode}): "
                f"{stderr or '<no stderr>'}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise CredentialError(
                f"`op read {raw}` timed out after {_OP_READ_TIMEOUT_S}s — "
                "is the 1Password CLI signed in?"
            ) from exc
        value = result.stdout.strip("\n")
        if not value:
            raise CredentialError(f"`op read {raw}` returned an empty value")
        _resolved_secrets[raw] = value
        return value


def credentials_for(shop_id: str) -> ShopCredentials | None:
    """Return resolved credentials for ``shop_id``, or ``None`` if both env
    vars are unset.

    Raises ``CredentialError`` if the pair is half-configured, if a 1Password
    reference can't be resolved, or if the resolved value is empty.
    """
    key = shop_id.upper()
    user_var = f"CZ_MTG_{key}_USER"
    pass_var = f"CZ_MTG_{key}_PASS"
    raw_user = (os.environ.get(user_var) or "").strip()
    raw_pass = (os.environ.get(pass_var) or "").strip()
    if not raw_user and not raw_pass:
        return None
    if not raw_user or not raw_pass:
        missing = user_var if not raw_user else pass_var
        raise CredentialError(
            f"shop '{shop_id}' is partially configured: {missing} is empty or unset"
        )
    username = _resolve_value(raw_user, var_name=user_var)
    password = _resolve_value(raw_pass, var_name=pass_var)
    return ShopCredentials(shop_id=shop_id, username=username, password=password)


def has_credentials(shop_id: str) -> bool:
    """Cheap check that does *not* resolve 1Password references — just looks at
    env var presence. Useful for capability/status reporting.
    """
    key = shop_id.upper()
    user = (os.environ.get(f"CZ_MTG_{key}_USER") or "").strip()
    password = (os.environ.get(f"CZ_MTG_{key}_PASS") or "").strip()
    return bool(user and password)


def reset_secret_cache() -> None:
    """Drop the cached resolved-secret table. Intended for tests."""
    with _cache_lock:
        _resolved_secrets.clear()
