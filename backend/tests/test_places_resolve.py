from app.places import slugify


def test_slugify_folds_tones_and_hyphenates():
    assert slugify("Cơm gà Thịnh Lơ") == "com-ga-thinh-lo"
    assert slugify("Quán Bé Bự - Khoai Tây") == "quan-be-bu-khoai-tay"
    assert slugify("Phở Vui") == "pho-vui"


def test_slugify_maps_d_stroke_and_drops_punctuation():
    # đ does not decompose under NFD — roster._fold maps it by hand, and slugify
    # inherits that. "Đặng Văn Ngữ" must not become "ang-van-ngu".
    assert slugify("Bún cá Đặng Văn Ngữ") == "bun-ca-dang-van-ngu"
    assert slugify("Jacky - Mì Vịt Quay & Cơm Xá Xíu") == "jacky-mi-vit-quay-com-xa-xiu"
