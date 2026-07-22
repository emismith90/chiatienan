"""Declarative week-long scenario for the behavioral eval (deterministic +
opt-in LLM runners). Member keys a1..a5 are resolved to ids by the runner.
Amounts are integer VND. `day` is an ISO date; the runner freezes the clock to
noon ICT on that day. Payees (a1,a2,a4) get bank details so QR builds succeed.

`kind` values:
  meal_confirmed   — create draft + commit (a normal logged, confirmed meal)
  payment          — ledger.record_payment(from,to,amount)
  add_member       — add a new member (key)
  leave_pending    — create draft but DO NOT commit (stays an open proposal)
  confirm_pending  — commit the draft created by a named earlier step (`ref`)
  settle           — settle_period commit:false; expect transfers OR blocked
  settle_commit    — settle_period commit:true (reset/close the period)

`expect` keys: balances {key: vnd}, transfers [{from,to,amount}],
qr_payees [keys], blocked_pending (int count), empty (bool).
"""

MON = "2026-07-20"; TUE = "2026-07-21"; WED = "2026-07-22"
THU = "2026-07-23"; FRI = "2026-07-24"; NEXT_MON = "2026-07-27"

MEMBERS = [
    {"key": "a1", "display_name": "A1", "nickname": "a1",
     "bank": {"bank_code": "VCB", "account_number": "111", "account_holder": "A1"}},
    {"key": "a2", "display_name": "A2", "nickname": "a2",
     "bank": {"bank_code": "VCB", "account_number": "222", "account_holder": "A2"}},
    {"key": "a3", "display_name": "A3", "nickname": "a3"},
    {"key": "a4", "display_name": "A4", "nickname": "a4",
     "bank": {"bank_code": "VCB", "account_number": "444", "account_holder": "A4"}},
    # a5 is added mid-scenario (step 6), not seeded up front.
]

STEPS = [
    {"id": "s1", "day": MON, "actor": "a1", "kind": "meal_confirmed",
     "message": "@bot tôi trả 300k cả nhóm",
     "payer": "a1", "participants": ["a1", "a2", "a3", "a4"], "total": 300_000,
     "expect": {"balances": {"a1": 225_000, "a2": -75_000, "a3": -75_000, "a4": -75_000}}},

    {"id": "s2", "day": TUE, "actor": "a1", "kind": "meal_confirmed",
     "message": "@bot tôi trả 150k, a4 không ăn",
     "payer": "a1", "participants": ["a1", "a2", "a3"], "total": 150_000,
     "expect": {"balances": {"a1": 325_000, "a2": -125_000, "a3": -125_000, "a4": -75_000}}},

    {"id": "s3", "day": TUE, "actor": "a1", "kind": "payment",
     "message": "@bot tôi nhận 125k từ a2",
     "from": "a2", "to": "a1", "amount": 125_000,
     "expect": {"balances": {"a1": 200_000, "a2": 0, "a3": -125_000, "a4": -75_000}}},

    {"id": "s4", "day": WED, "actor": "a2", "kind": "meal_confirmed",
     "message": "@bot tôi trả 500k, cả nhóm 4 người + 1 khách",
     "payer": "a2", "participants": ["a1", "a2", "a3", "a4"], "total": 500_000,
     "guests": ["guest1"],
     "expect": {"balances": {"a1": 100_000, "a2": 300_000, "a3": -225_000, "a4": -175_000}}},

    {"id": "s5", "day": WED, "actor": "a3", "kind": "settle",
     "message": "@bot tôi phải trả bao nhiêu",
     # Per-payer attribution: each debtor repays whoever fronted the meal they
     # ate (a1 fronted s1+s2, a2 fronted s4), not a minimised creditor.
     "expect": {"transfers": [{"from": "a1", "to": "a2", "amount": 100_000},
                              {"from": "a3", "to": "a1", "amount": 125_000},
                              {"from": "a3", "to": "a2", "amount": 100_000},
                              {"from": "a4", "to": "a1", "amount": 75_000},
                              {"from": "a4", "to": "a2", "amount": 100_000}],
                "qr_payees": ["a1", "a2"]}},

    {"id": "s6", "day": THU, "actor": "a4", "kind": "add_member",
     "message": "@bot thêm thành viên a5", "new_member": "a5"},

    {"id": "s7", "day": THU, "actor": "a4", "kind": "leave_pending",
     "message": "@bot tôi trả 400k, a2 không ăn",
     "payer": "a4", "participants": ["a1", "a3", "a4", "a5"], "total": 400_000},

    {"id": "s8", "day": FRI, "actor": "a5", "kind": "settle",
     "message": "@bot tính tiền",
     "expect": {"blocked_pending": 1}},

    {"id": "s9a", "day": FRI, "actor": "a1", "kind": "confirm_pending", "ref": "s7"},
    {"id": "s9b", "day": FRI, "actor": "a1", "kind": "leave_pending",
     "message": "@bot tôi trả 300k cho cả nhóm",
     "payer": "a1", "participants": ["a1", "a2", "a3", "a4", "a5"], "total": 300_000},

    {"id": "s10a", "day": FRI, "actor": "a5", "kind": "confirm_pending", "ref": "s9b"},
    {"id": "s10b", "day": FRI, "actor": "a5", "kind": "settle",
     "message": "@bot tính tiền",
     "expect": {"transfers": [{"from": "a1", "to": "a2", "amount": 40_000},
                              {"from": "a4", "to": "a1", "amount": 35_000},
                              {"from": "a3", "to": "a1", "amount": 185_000},
                              {"from": "a3", "to": "a2", "amount": 100_000},
                              {"from": "a3", "to": "a4", "amount": 100_000},
                              {"from": "a4", "to": "a2", "amount": 100_000},
                              {"from": "a5", "to": "a1", "amount": 60_000},
                              {"from": "a5", "to": "a4", "amount": 100_000}],
                "qr_payees": ["a1", "a2", "a4"]}},

    {"id": "s11", "day": FRI, "actor": "a1", "kind": "settle_commit",
     "message": "@bot trả đủ rồi, reset balance"},

    {"id": "s12", "day": NEXT_MON, "actor": "a1", "kind": "settle",
     "message": "@bot còn ai nợ ai gì không",
     "expect": {"empty": True}},
]
