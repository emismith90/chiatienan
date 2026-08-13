"""Render the three synthetic bill photos beside this file.

Committed so the PNGs are reproducible and their ground truth is auditable: the
printed TỔNG CỘNG is what `cases.json` grades against, and a rendered bill is
exact where a photograph would be a judgement call.

    pip install playwright   # Chromium is already on the image
    python bench/corpus/bills/render.py

Not run by the test suite — `tests/test_bench_corpus.py` instead recomputes every
expectation from `app.money`, so the arithmetic cannot silently drift from the
picture.
"""
import pathlib
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).resolve().parent

BILLS = {
    # id -> (title, [(dish, qty, price)], extra lines, total)
    "bill-even": ("QUÁN CƠM TẤM 68", [
        ("Cơm tấm sườn", 2, 65000), ("Cơm gà xối mỡ", 1, 70000),
        ("Trà đá", 3, 5000)], [], 215000),
    "bill-discount": ("BÚN BÒ HUẾ O LOAN", [
        ("Bún bò đặc biệt", 3, 70000), ("Chả cua", 1, 40000),
        ("Nước mía", 2, 20000)], [("Giảm giá 10%", -29000)], 261000),
    "bill-itemized": ("TRÀ SỮA MIXUE", [
        ("Trân châu đường đen (An)", 1, 45000),
        ("Matcha latte (Bình)", 1, 55000),
        ("Hồng trà sữa (Cường)", 1, 39000)], [("Phí giao hàng", 15000)], 154000),
}

CSS = """
body{margin:0;font-family:"DejaVu Sans",sans-serif;background:#e8e4dc;
     display:flex;justify-content:center;padding:18px}
.bill{background:#fffdf7;width:330px;padding:18px 20px;box-shadow:0 2px 10px #0003;
      border-radius:3px}
h1{font-size:15px;margin:0 0 2px;text-align:center;letter-spacing:.5px}
.sub{font-size:10px;text-align:center;color:#666;margin-bottom:12px}
table{width:100%;border-collapse:collapse;font-size:11.5px}
td{padding:3px 0}
td.q{text-align:center;width:26px;color:#555}
td.p{text-align:right;white-space:nowrap}
.rule{border-top:1px dashed #999;margin:9px 0}
.tot{display:flex;justify-content:space-between;font-size:14px;font-weight:bold;
     margin-top:6px}
.foot{font-size:9px;text-align:center;color:#888;margin-top:12px}
"""


def html(title, lines, extras, total):
    rows = "".join(
        f'<tr><td>{n}</td><td class="q">{q}</td><td class="p">{p:,}</td></tr>'
        for n, q, p in lines)
    extra = "".join(f'<tr><td colspan="2">{n}</td><td class="p">{v:,}</td></tr>'
                    for n, v in extras)
    return f"""<html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<div class="bill"><h1>{title}</h1><div class="sub">HOÁ ĐƠN · 20/07/2026 12:14</div>
<table>{rows}<tr><td colspan="3"><div class="rule"></div></td></tr>{extra}</table>
<div class="tot"><span>TỔNG CỘNG</span><span>{total:,}đ</span></div>
<div class="foot">Cảm ơn quý khách · Xin vui lòng kiểm tra hoá đơn</div></div>
</body></html>"""


with sync_playwright() as pw:
    # PLAYWRIGHT_BROWSERS_PATH is preset on the dev image; pass
    # executable_path=... if your Chromium lives elsewhere.
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 380, "height": 460},
                            device_scale_factor=2)
    for name, (title, lines, extras, total) in BILLS.items():
        page.set_content(html(title, lines, extras, total))
        path = OUT / f"{name}.png"
        page.locator(".bill").screenshot(path=str(path))
        print(name, total, path.stat().st_size, "bytes")
    browser.close()
