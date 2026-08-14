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


import pytest
from app import places
from app.db import Database
from app.models import Place, Room


@pytest.fixture()
def db():
    d = Database("sqlite:///:memory:")
    d.create_all()
    return d


def _seed(db, rows):
    with db.session() as s:
        # flush the room before its children — the repo's fixture convention
        # (test_member_statement.py:13); without a relationship() the unit of
        # work has no ordering dependency and can insert places first.
        s.add(Room(id=1, name="test", invite_token="t"))
        s.flush()
        for name, aliases in rows:
            s.add(Place(room_id=1, slug=places.slugify(name), name=name, aliases=aliases))
    return 1


def test_exact_and_folded_tiers(db):
    _seed(db, [("Cơm gà đảo, cơm rang Thịnh Lơ", ["thịnh lơ", "thinh lo"])])
    with db.session() as s:
        p, tier = places.resolve_one(s, 1, "Thịnh Lơ")
        assert (p.slug, tier) == ("com-ga-dao-com-rang-thinh-lo", "exact")
        p, tier = places.resolve_one(s, 1, "thinh lo")
        assert (p.slug, tier) == ("com-ga-dao-com-rang-thinh-lo", "exact")


def test_place_prefix_is_stripped_as_a_fallback(db):
    _seed(db, [("Quán Bé Bự - Khoai Tây", ["bé bự"])])
    with db.session() as s:
        p, tier = places.resolve_one(s, 1, "quán bé bự")
        assert p is not None and tier in ("folded", "prefix")


def test_multi_word_matches_tokens_in_any_order(db):
    _seed(db, [("Bún chả rửa xe Nam Đồng", [])])
    with db.session() as s:
        p, tier = places.resolve_one(s, 1, "rửa xe bún chả")
        assert p is not None and tier == "tokens"


def test_two_places_sharing_a_token_stay_ambiguous(db):
    _seed(db, [("Bánh cuốn Bà Hoành", []), ("Bánh cuốn Bà Xuân", [])])
    with db.session() as s:
        p, tier = places.resolve_one(s, 1, "bánh cuốn")
        assert p is None and tier == "ambiguous"


def test_exact_hit_is_never_widened_into_a_token_sweep(db):
    _seed(db, [("Bún mọc", []), ("Bún mọc Hàng Lược", [])])
    with db.session() as s:
        p, tier = places.resolve_one(s, 1, "bún mọc")
        assert (p.slug, tier) == ("bun-moc", "exact")


def test_unknown_text_resolves_to_nothing(db):
    _seed(db, [("Phở Vui", [])])
    with db.session() as s:
        assert places.resolve_one(s, 1, "sushi") == (None, "none")


def test_resolve_returns_roster_shaped_dict(db):
    _seed(db, [("Phở Vui", ["vui"]), ("Bánh cuốn Bà Hoành", []), ("Bánh cuốn Bà Xuân", [])])
    with db.session() as s:
        res = places.resolve(s, 1, names=["vui", "sushi", "bánh cuốn"])
    assert [m["slug"] for m in res["matched"]] == ["pho-vui"]
    assert res["unresolved"] == ["sushi"]
    assert res["ambiguous"][0]["name"] == "bánh cuốn"
    assert len(res["ambiguous"][0]["candidates"]) == 2
