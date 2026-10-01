---
name: record-payment
description: Record one person paying another in cash — "A trả B", "A đã trả", "trả hết rồi".
---
# Record a cash payment

Use `propose_payment` (DO NOT use `propose_meal`). It only PROPOSES — the user confirms on the card.

- `from` = the payer (empty = the sender), `to` = the recipient.
- A sentence with NO subject ("đã trả rồi", "just paid 53k to A1", "trả xong") → the payer is
  THE SENDER. Leave `from` empty. NEVER infer someone else from the debt ledger —
  recording the wrong payer gets two people's debts wrong at once.
- A specific amount ('A trả B 100k') → pass `amount` (VND).
- NO amount ('A đã trả B', 'trả hết rồi') → LEAVE `amount` EMPTY; the tool computes exactly what A owes B (summed per meal). DON'T guess the amount yourself.
- If the tool returns `payment_ambiguous` (two people owe each other in BOTH DIRECTIONS): ASK the user — pay the full `gross` amount or only offset the difference `offset` — then call `propose_payment` again with `mode:"gross"` or `mode:"offset"` (don't type the amount yourself).
- `payment_settled` = really nothing owed any more → report that, create no card.
- `nothing_owed` = the payer doesn't owe the other person (it's the other way round) → explain, create no card.
- Several people paying in one sentence → call `propose_payment` ONCE FOR EACH person.
- "Tôi trả phần của tôi rồi" / "paid my part" / "đã trả hết" WITHOUT saying whom they paid:
  for everyone the sender currently owes, call `propose_payment` once for EACH of them
  (leave `amount` empty). DON'T ask "paid whom?" — draft cards can be edited and cancelled, so proposing
  all the items beats making the user list them again.
