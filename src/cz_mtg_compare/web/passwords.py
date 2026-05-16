"""Password hashing wrapper.

Argon2id with library defaults — time_cost=2, memory_cost=64 MiB,
parallelism=1, hash_len=32, salt_len=16. Suitable for an interactive
login endpoint on a modest server; revisit if/when CPU budgets change.

Wrapped behind a thin function pair so the rest of the codebase never
imports argon2 directly — that keeps the algorithm choice swappable
(scrypt / bcrypt) without touching call sites.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(plaintext: str) -> str:
    """Return an argon2id PHC-string hash. Argon2's salt is random per
    call, so two calls with the same input return different strings."""
    return _hasher.hash(plaintext)


def verify_password(stored_hash: str, plaintext: str) -> bool:
    """Constant-time verify. Returns False for any kind of mismatch
    (wrong password, malformed hash). Never raises."""
    try:
        return _hasher.verify(stored_hash, plaintext)
    except (VerifyMismatchError, InvalidHashError):
        return False
    except Exception:
        # Argon2 can raise other low-level errors on malformed inputs.
        # The caller only cares about pass/fail.
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True when the stored hash was produced by a weaker parameter set
    than the current one. Call sites should rehash on successful login
    so old hashes upgrade over time."""
    return _hasher.check_needs_rehash(stored_hash)
