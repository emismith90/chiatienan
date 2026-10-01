---
name: poker-balances
description: Who owes whom after the games, provisional settlement and QR — "ai trả ai", "tôi nợ bao nhiêu", "chốt", "lịch sử ván".
---
# Debts / provisional settlement / history

- First person ('tôi nợ ai', 'tôi được bao nhiêu') → `member_statement` (default = the sender). Two lists: who you owe / who owes you, game by game — there is NO single "net" ('ròng') number.
- Who pays whom, create QR, 'chốt', 'tính tiền' → `settle_period`. It is ONLY PROVISIONAL and doesn't close the period; say so clearly if the user wants to 'reset'.
- Overall summary → `get_period_summary`; list of games with each person's profit/loss → `game_history`.
- If there is an unconfirmed game card, `settle_period` is blocked: ask the table to press Confirm on the card, or cancel it with `cancel_draft` if they say to drop it.
- Cash payments between people ('A trả B rồi') → `propose_payment` (leave `amount` empty so the tool computes exactly what A owes B).
