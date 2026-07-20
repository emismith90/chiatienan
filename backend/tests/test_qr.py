from urllib.parse import parse_qs, urlparse

import pytest

from app import roster
from app.qr import QRError, make_qr_url


def test_make_qr_url_encodes_vietnamese_note(db):
    with db.session() as s:
        m = roster.create_member(
            s, display_name="An", bank_code="VCB",
            account_number="0123456789", account_holder="NGUYEN VAN AN",
        )
        url = make_qr_url(m, 150_000, "chia tiền ăn trưa", template="compact2")
        assert url.startswith("https://img.vietqr.io/image/VCB-0123456789-compact2.png?")
        q = parse_qs(urlparse(url).query)
        assert q["amount"] == ["150000"]
        assert q["addInfo"] == ["chia tiền ăn trưa"]        # parse_qs decodes it back
        assert q["accountName"] == ["NGUYEN VAN AN"]
        # ensure it was actually percent-encoded in the raw string
        assert "chia%20ti" in url or "chia+ti" in url


def test_make_qr_url_requires_bank_details(db):
    with db.session() as s:
        m = roster.create_member(s, display_name="NoBank")
        with pytest.raises(QRError):
            make_qr_url(m, 100_000, "test")


def test_make_qr_url_rejects_nonpositive_amount(db):
    with db.session() as s:
        m = roster.create_member(
            s, display_name="An", bank_code="VCB",
            account_number="001", account_holder="AN",
        )
        with pytest.raises(QRError):
            make_qr_url(m, 0, "test")
