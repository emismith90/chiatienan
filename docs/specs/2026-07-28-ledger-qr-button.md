# QR button on ledger "You owe" rows → bot posts the VietQR

**Date:** 2026-07-28 · **Status:** approved (mechanism + scope confirmed by operator)

## Problem

The only way to get a payment QR is asking the bot in chat ("@bot tui nợ bao nhiêu
xin qr") — an LLM turn for a fully deterministic answer. The ledger panel already
lists exactly what the caller owes per person; each unpaid line should offer the QR
directly.

## Decisions

- **Deterministic bot post, no LLM.** New endpoint computes everything server-side
  and posts a bot message; money never passes through the model (design D3).
- **Per-person aggregate.** A line's QR covers ALL outstanding meals the caller owes
  that creditor in the current `since_last` period (same nets as `period_transfers`),
  note built by `build_qr_note` naming the meals. Every unpaid line shows the button;
  lines sharing a creditor produce the same card.

## Design

1. **`POST /api/rooms/{room_id}/qr-requests`** body `{to: <creditor member id>}`,
   `require_session` + `_check_room`, serialized under `chat._agent_lock`
   (mirrors `quick_pay`):
   - period = `resolve_period("since_last", …)`; edges = `ledger.debt_breakdown`;
     amount = Σ outstanding of caller→creditor edges. `0 → 409 "nothing outstanding"`.
   - note = `build_qr_note(caller name, pair meals, fallback "Chia tien an d/m")`;
     `make_qr_url(payee, amount, note)`; `QRError → 409` with its message (no chat
     spam for a missing bank setup — the button surfaces it).
   - Posts a bot message with the **existing `settlement` attachment shape**
     (`type: "settlement"`, `transfers: [{from_id, from_name, to_id, to_name, amount,
     note, qr_url}]`, period) — so the chat renders the existing card, and
     `annotate_settled_transfers` retires the QR automatically once paid.
   - Deterministic body: `📱 QR chuyển khoản <payer> → <payee>: <amount>đ`.
   - `hub.publish` `message` (no `ledger:changed` — nothing changed).
2. **Frontend** — `api.requestQr(roomId, to)`; `OweRow` gains a `QR` pill next to
   "Mark paid" on unpaid/partial rows (both the ledger panel and the in-chat
   statement card share `OweRow`). Busy/error states mirror the pay button; the
   card itself arrives in chat via SSE.

## Testing

- Backend (`test_qr_request.py`, fixtures from `test_quick_pay.py`): aggregate over
  two meals to one creditor; 409 when nothing outstanding; 409 QRError when payee
  lacks bank details; posted card has settlement shape + correct amount/qr_url;
  paying afterwards makes `annotate_settled_transfers` mark it `settled`.
- Frontend (`statement-card.test.tsx` + new cases): QR button renders on unpaid owe
  rows, calls `api.requestQr` with the creditor id, hidden on paid rows.

## Out of scope

Per-meal QR scope; inline QR rendering in the panel; retiring older duplicate QR
cards beyond the existing settled annotation.
