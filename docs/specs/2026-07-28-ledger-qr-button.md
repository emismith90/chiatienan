# QR button on ledger "You owe" rows → the VietQR, in a dialog

**Date:** 2026-07-28 · **Status:** shipped
**Amended:** 2026-08-16 — the QR opens in a modal instead of being posted to the
chat (see "Amendment" below).

## Problem

The only way to get a payment QR is asking the bot in chat ("@bot tui nợ bao nhiêu
xin qr") — an LLM turn for a fully deterministic answer. The ledger panel already
lists exactly what the caller owes per person; each unpaid line should offer the QR
directly.

## Decisions

- **Deterministic, no LLM.** New endpoint computes everything server-side; money
  never passes through the model (design D3).
- **Per-person aggregate.** A line's QR covers ALL outstanding meals the caller owes
  that creditor in the current `since_last` period (same nets as `period_transfers`),
  note built by `build_qr_note` naming the meals. Every unpaid line shows the button;
  lines sharing a creditor produce the same QR.

## Design

1. **`POST /api/rooms/{room_id}/qr-requests`** body `{to: <creditor member id>}`,
   `require_session` + `_check_room`:
   - period = `resolve_period("since_last", …)`; edges = `ledger.debt_breakdown`;
     amount = Σ outstanding of caller→creditor edges. `0 → 409 "nothing outstanding"`.
   - note = `build_qr_note(caller name, pair meals, fallback "Chia tien an d/m")`;
     `make_qr_url(payee, amount, note)`; `QRError → 409` with its message (the
     button surfaces a missing bank setup).
   - Returns the payload the dialog draws: `amount`, `note`, `qr_url`, `from`
     (incl. the caller's own `bank_code`, for the pay actions), `to` (name +
     account details), `period`, and `meals` — the per-meal breakdown behind the
     total.
2. **Frontend** — `api.requestQr(roomId, to)`; `OweRow` gains a `QR` pill next to
   "Mark paid" on unpaid/partial rows (both the ledger panel and the in-chat
   statement card share `OweRow`). Success opens `QrDialog`; failures stay on the
   row as the server's own 409 detail.

## Amendment (2026-08-16): dialog, not a chat card

Originally the endpoint posted a bot message carrying a `settlement`-shaped
attachment, and the chat rendered the QR. Replaced by a modal on the row:

- Checking what you owe one person is a private lookup — the room does not need
  a bot card each time somebody taps QR.
- The thread accumulated one live QR card per tap, each of which then had to be
  retired by `annotate_settled_transfers` once the debt was paid.
- The dialog answers where the question was asked, and can show the payee's
  account details and the meal-by-meal breakdown behind the total — which a
  settlement card has no room for.

Consequences: the endpoint writes nothing and posts nothing, so it no longer
takes `chat._agent_lock` (the QR opens immediately instead of queueing behind an
agent turn) and publishes no `message` event. `QrDialog` reuses `PanelDialog`
(backdrop/Esc close) and `PayActions` (open bank app · save QR · copy chips),
which real settlement cards still use unchanged.

## Testing

- Backend (`test_qr_request.py`): aggregate over two meals to one creditor plus
  its `meals` breakdown; 409 when nothing outstanding; 409 QRError when the payee
  lacks bank details; nothing posted to the chat; paid meals drop out of the
  amount and a fully-paid pair returns 409.
- Frontend (`statement-card.test.tsx`): QR button renders on unpaid owe rows and
  opens the dialog with the QR image, total, payee details and breakdown; the
  dialog closes; the server's reason shows on the row when the QR cannot be
  built; no button on paid rows.

## Out of scope

Per-meal QR scope; inline QR rendering in the panel itself.
