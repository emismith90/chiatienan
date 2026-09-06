"""The poker business's golden cases (design §7.2 "eval"): three recorded games with
hand-derived nets and edges, a settle with QR payees, and an ambiguous table that must
be asked about — shipped by the pack as content (``ToolPack.eval_cases``), imported
as suite ``poker_ledger-golden``, and pinned by ``tests/test_poker_pack.py``.

Hand derivation (member-id order p1 < p2 < p3 < p4; losers allocated in that order,
each loss split across winners in proportion to their remaining wins):

  g1 (MON)  p1 500k→800k  p2 500k→400k  p3 500k→300k         nets +300k / −100k / −200k
            edges p2→p1 100k, p3→p1 200k
  g2 (TUE)  p1 400k→200k  p2 400k→550k  p3 400k→500k  p4 400k→350k
            nets −200k / +150k / +100k / −50k
            p1's 200k over (150k, 100k) → p2 120k, p3 80k; remaining 30k, 20k
            p4's 50k over (30k, 20k)   → p2 30k, p3 20k       (both sides exact)
  g3 (WED)  p1 300k→500k  p2 300k→50k  house 50k               nets +200k / −250k; p2 bears the
            house (50k, paid at the table) → edge p2→p1 200k
  pay (THU) p3 → p1 200k                                       clears p3→p1 (g1)
  settle    p2→p1 100k+200k vs p1→p2 120k → p2→p1 180k; p1→p3 80k; p4→p2 30k; p4→p3 20k
"""
MON, TUE, WED, THU, FRI = "2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"

MEMBERS = [
    {"key": "p1", "display_name": "P1", "nickname": "p1", "bank": {"bank_code": "VCB", "account_number": "111", "account_holder": "P1"}},
    {"key": "p2", "display_name": "P2", "nickname": "p2", "bank": {"bank_code": "VCB", "account_number": "222", "account_holder": "P2"}},
    {"key": "p3", "display_name": "P3", "nickname": "p3", "bank": {"bank_code": "VCB", "account_number": "333", "account_holder": "P3"}},
    {"key": "p4", "display_name": "P4", "nickname": "p4"},
]

G1 = [{"member": "p1", "buy_in": 500_000, "cash_out": 800_000}, {"member": "p2", "buy_in": 500_000, "cash_out": 400_000},
      {"member": "p3", "buy_in": 500_000, "cash_out": 300_000}]
G2 = [{"member": "p1", "buy_in": 400_000, "cash_out": 200_000}, {"member": "p2", "buy_in": 400_000, "cash_out": 550_000},
      {"member": "p3", "buy_in": 400_000, "cash_out": 500_000}, {"member": "p4", "buy_in": 400_000, "cash_out": 350_000}]
G3 = [{"member": "p1", "buy_in": 300_000, "cash_out": 500_000}, {"member": "p2", "buy_in": 300_000, "cash_out": 50_000}]

STEP_G1 = {"id": "g1", "kind": "game_recorded", "day": MON, "actor": "p1", "entries": G1}
STEP_G2 = {"id": "g2", "kind": "game_recorded", "day": TUE, "actor": "p1", "entries": G2}
STEP_G3 = {"id": "g3", "kind": "game_recorded", "day": WED, "actor": "p1", "entries": G3, "house": 50_000}
STEP_PAY = {"id": "pay", "kind": "payment", "day": THU, "actor": "p3", "from": "p3", "to": "p1", "amount": 200_000}


def _case(id_, day, message, prior, expect, actor="p1"):
    return {"id": id_, "source": "poker", "day": day, "actor": actor, "members": MEMBERS, "prior_steps": prior,
            "message": message, "history": "", "images": [], "had_images": False, "expect": expect,
            "tags": ["poker", "golden"], "review": False}


CASES = [
    _case("PG1", MON, "@bot tối nay P1 mua 500k ra 800k, P2 mua 500k ra 400k, P3 mua 500k ra 300k", [],
          {"tools": ["propose_game"], "args": {"propose_game": {"entries": G1}},
           "nets": {"p1": 300_000, "p2": -100_000, "p3": -200_000}, "pot": 1_500_000}),
    _case("PG2", TUE, "@bot ván tối nay: P1 400k ra 200k, P2 400k ra 550k, P3 400k ra 500k, P4 400k ra 350k", [STEP_G1],
          {"tools": ["propose_game"], "args": {"propose_game": {"entries": G2}},
           "nets": {"p1": -200_000, "p2": 150_000, "p3": 100_000, "p4": -50_000}, "pot": 1_600_000}),
    _case("PG3", WED, "@bot P1 mua 300k ra 500k, P2 mua 300k ra 50k, bàn giữ 50k tip", [STEP_G1, STEP_G2],
          {"tools": ["propose_game"], "args": {"propose_game": {"entries": G3, "house": 50_000}},
           "nets": {"p1": 200_000, "p2": -250_000}, "pot": 600_000}),
    _case("PS1", FRI, "@bot ai trả ai, tạo QR", [STEP_G1, STEP_G2, STEP_G3, STEP_PAY],
          {"tools": ["settle_period"],
           "transfers": [{"from": "p2", "to": "p1", "amount": 180_000}, {"from": "p1", "to": "p3", "amount": 80_000},
                         {"from": "p4", "to": "p2", "amount": 30_000}, {"from": "p4", "to": "p3", "amount": 20_000}],
           "qr_payees": ["p1", "p2", "p3"]}),
    _case("PA1", MON, "@bot tối nay P1 mua 500k ra 900k, P2 mua 500k ra 200k", [],
          {"tools": [], "forbidden_tools": ["propose_game"]}),
]
