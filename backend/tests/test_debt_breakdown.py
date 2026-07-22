from datetime import date
from app.money import DebtEdge, build_debt_edges, apply_payments_fifo


def _meal(meal_id, payer, shares, day, dish="x"):
    return {"meal_id": meal_id, "payer_id": payer, "dish": dish,
            "occurred_on": date(2026, 7, day), "shares": shares}


def test_build_edges_skips_payer_and_zero():
    # meal #2: Linh(6) paid, 5 share 61k each
    edges = build_debt_edges([_meal(2, 6, {4: 61000, 6: 61000, 7: 61000, 8: 61000, 9: 61000}, 21)])
    pairs = {(e.debtor, e.creditor): e.amount for e in edges}
    assert (6, 6) not in pairs                 # payer owes nobody
    assert pairs == {(4, 6): 61000, (7, 6): 61000, (8, 6): 61000, (9, 6): 61000}


def test_fifo_marks_paid_oldest_first():
    edges = build_debt_edges([
        _meal(2, 6, {9: 61000}, 21),   # Giang(9) owes Linh(6) 61k (older)
        _meal(3, 6, {9: 40000}, 22),   # Giang owes Linh 40k (newer)
    ])
    out = apply_payments_fifo(edges, [{"from": 9, "to": 6, "amount": 61000}])
    by_meal = {e.meal_id: e for e in out}
    assert by_meal[2].status == "paid" and by_meal[2].outstanding == 0
    assert by_meal[3].status == "unpaid" and by_meal[3].outstanding == 40000


def test_fifo_partial():
    edges = build_debt_edges([_meal(2, 6, {9: 61000}, 21)])
    out = apply_payments_fifo(edges, [{"from": 9, "to": 6, "amount": 20000}])
    assert out[0].status == "partial" and out[0].paid == 20000 and out[0].outstanding == 41000


def test_fifo_overpayment_floors_at_zero():
    edges = build_debt_edges([_meal(2, 6, {9: 61000}, 21)])
    out = apply_payments_fifo(edges, [{"from": 9, "to": 6, "amount": 90000}])
    assert out[0].paid == 61000 and out[0].outstanding == 0   # leftover ignored, never negative


def test_meal_targeted_payment_marks_that_meal_not_oldest():
    edges = build_debt_edges([
        _meal(2, 6, {9: 61000}, 21),   # older
        _meal(5, 6, {9: 40000}, 24),   # newer
    ])
    out = apply_payments_fifo(edges, [{"from": 9, "to": 6, "amount": 40000, "meal_id": 5}])
    by = {e.meal_id: e for e in out}
    assert by[5].status == "paid" and by[2].status == "unpaid"   # targeted beats FIFO
