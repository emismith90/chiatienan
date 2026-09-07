"""Shim: VietQR URL building moved to :mod:`ledger_core.qr` (plan Task 3.2); this
binds the deployment's base URL and template from settings."""
from __future__ import annotations

from app.config import settings
from ledger_core.qr import QRError  # noqa: F401
from ledger_core.qr import make_qr_url as _make_qr_url


def make_qr_url(payee, amount: int, note: str, *, template: str | None = None) -> str:
    return _make_qr_url(payee, amount, note, base_url=settings.qr_base_url,
                        template=template or settings.qr_template)
