import base64

from app.images import sanitize_images


def _b64(n: int) -> str:
    return base64.b64encode(b"x" * n).decode()


def test_none_when_not_a_list():
    assert sanitize_images(None) is None
    assert sanitize_images("nope") is None


def test_accepts_valid_png_and_strips_data_url_prefix():
    out = sanitize_images([{"mimeType": "image/png", "data": "data:image/png;base64," + _b64(10)}])
    assert out == [{"data": _b64(10), "mimeType": "image/png"}]


def test_rejects_disallowed_mime():
    assert sanitize_images([{"mimeType": "image/svg+xml", "data": _b64(10)}]) is None


def test_per_image_size_cap():
    big = _b64(6 * 1024 * 1024)  # ~6 MB > 5 MB cap
    assert sanitize_images([{"mimeType": "image/png", "data": big}]) is None


def test_max_count_is_four():
    items = [{"mimeType": "image/png", "data": _b64(10)} for _ in range(6)]
    out = sanitize_images(items)
    assert out is not None and len(out) == 4
