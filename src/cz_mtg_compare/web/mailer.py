"""Outbound mail.

A thin pluggable interface so the rest of the codebase never imports an
SMTP client directly. The default is a ``LoggingMailer`` that emits the
message to the log — good enough for dev / staging and for the CI smoke
suite, and intentionally inert in tests by default. Production deployments
swap in a real implementation (SMTP, SES, Resend) by passing it to
``create_app(mailer=...)``.
"""

from __future__ import annotations

import logging
from typing import Protocol

log = logging.getLogger(__name__)


class Mailer(Protocol):
    """Anything that can asynchronously send a verification email.

    Kept as a Protocol so callers don't have to subclass — a recording
    fake in tests is a one-liner.
    """

    async def send_verification_email(
        self, *, to: str, verification_url: str
    ) -> None: ...


class LoggingMailer:
    """Default mailer. Writes the verification URL to the logger at INFO.

    Wins for local dev: the operator can copy the URL from stdout
    without setting up SMTP. Loses in production where mails actually
    need to be delivered — swap it for a real Mailer there.
    """

    async def send_verification_email(self, *, to: str, verification_url: str) -> None:
        log.info("verification email: to=%s url=%s", to, verification_url)
