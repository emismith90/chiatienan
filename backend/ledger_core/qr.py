"""Build VietQR image URLs for settlement transfers.

Pure URL construction — no network, no QR library. The amount and payee come
straight from the deterministic settlement computation (never transcribed by the
LLM). The service is a quick-link image endpoint (design D7):

    {base_url}/{bank_code}-{account_number}-{template}.png
        ?amount=<int VND>&addInfo=<urlencoded des>&accountName=<urlencoded holder>
"""
from __future__ import annotations

from urllib.parse import quote



class QRError(ValueError):
    """A member lacks the bank details needed to build a VietQR image."""


def make_qr_url(payee, amount: int, note: str, *, base_url: str, template: str) -> str:
    """VietQR image URL paying ``amount`` VND to ``payee`` with ``note`` as addInfo.

    Raises :class:`QRError` if the payee has no bank details (so the settlement
    surfaces a clear "ask admin to fill bank info" instead of a broken image).
    ``payee`` is any object with ``bank_code``, ``account_number``, ``account_holder``,
    ``display_name`` and ``has_bank_details()`` — the host's member model.
    """
    if amount <= 0:
        raise QRError(f"QR amount must be greater than 0 (got {amount}).")
    if not payee.has_bank_details():
        raise QRError(
            f"{payee.display_name} has no bank details yet — please update them on /profile."
        )

    base = f"{base_url}/{payee.bank_code}-{payee.account_number}-{template}.png"
    query = (
        f"amount={int(amount)}"
        f"&addInfo={quote(note or '', safe='')}"
        f"&accountName={quote(payee.account_holder or '', safe='')}"
    )
    return f"{base}?{query}"
