"""A canonical Vietnamese message per golden meal case.

`tests/golden/meals.py` carries **no messages** — its cases are draft payloads
addressed by 1-based member index, fed straight to `drafts.create_draft`. For an
LLM replay there has to be something for the model to read, so each case gets a
message authored to match its payload semantics exactly:

  * the sender is always the case's `payer`, so "tôi trả" is truthful;
  * a participant list that excludes the payer says so out loud (`G3`);
  * an `adjustments` entry appears as something one person ordered extra (`G4`);
  * `guests` are named as `khách`, the word the room actually uses for a
    non-member who pays cash;
  * `G12`'s metadata (`dish` / `initiator` / `note`) is all stated in the text,
    because that case exists to prove the metadata round-trips.

Member names match the index mapping in `meals.py`'s own docstring
(An=1, Bình=2, Cường=3, Dung=4), so `find_members` has real names to resolve.

Keyed by case id, and `corpus.load("meals")` raises on a missing key: adding a
golden case forces adding a message here rather than silently shrinking the
corpus.
"""

MESSAGES = {
    # Even split across the whole group.
    "G1": "@bot tôi trả 400k cả nhóm",
    # Bình pays; An is left out.
    "G2": "@bot tôi trả 300k, An không ăn",
    # An pays for a meal he did not eat.
    "G3": "@bot tôi trả 200k, Bình với Cường ăn, tôi không ăn",
    # Bình ordered 50k more than An.
    "G4": "@bot tôi trả 250k, tôi với Bình ăn, Bình gọi thêm 50k",
    # 100k over three people — the odd 1đ stays on the payer.
    "G5": "@bot tôi trả 100k, tôi với Bình và Cường ăn",
    # One guest settles in cash, so only the three members are tracked.
    "G6": "@bot tôi trả 400k, tôi với Bình, Cường và khách Emi ăn",
    # Bình pays; two guests settle in cash.
    "G7": "@bot tôi trả 400k, tôi với Cường và hai khách X, Y ăn",
    # A guest plus a remainder that stays on the payer.
    "G8": "@bot tôi trả 100k, tôi với Bình và khách Z ăn",
    # Metadata round-trip: dish, initiator, note.
    "G12": "@bot tôi trả 200k ăn phở với Bình, Emi rủ đi, ghi chú An đổi ý",
}
