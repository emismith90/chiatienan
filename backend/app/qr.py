"""Build VietQR image URLs for settlement transfers.

Pure URL construction — no network, no QR library. The amount and payee come
straight from the deterministic settlement computation (never transcribed by the
LLM). The service is a quick-link image endpoint (design D7):

    {QR_BASE_URL}/{bank_code}-{account_number}-{template}.png
        ?amount=<int VND>&addInfo=<urlencoded des>&accountName=<urlencoded holder>
"""
from __future__ import annotations

from urllib.parse import quote

from app.config import settings
from app.models import Member


class QRError(ValueError):
    """A member lacks the bank details needed to build a VietQR image."""


def make_qr_url(payee: Member, amount: int, note: str, *, template: str | None = None) -> str:
    """VietQR image URL paying ``amount`` VND to ``payee`` with ``note`` as addInfo.

    Raises :class:`QRError` if the payee has no bank details (so the settlement
    surfaces a clear "ask admin to fill bank info" instead of a broken image).
    """
    if amount <= 0:
        raise QRError(f"Số tiền QR phải lớn hơn 0 (nhận {amount}).")
    if not payee.has_bank_details():
        raise QRError(
            f"{payee.display_name} chưa có thông tin ngân hàng — nhờ cập nhật ở trang /profile."
        )

    tmpl = template or settings.qr_template
    base = f"{settings.qr_base_url}/{payee.bank_code}-{payee.account_number}-{tmpl}.png"
    query = (
        f"amount={int(amount)}"
        f"&addInfo={quote(note or '', safe='')}"
        f"&accountName={quote(payee.account_holder or '', safe='')}"
    )
    return f"{base}?{query}"
