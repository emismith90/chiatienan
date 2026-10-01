---
name: balances
description: See who owes whom and work out who pays whom — "tôi nợ ai", "how much do I owe", "summary", "current state", "ai trả tuần này", "chốt", "reset".
---
# Debts / summary / provisional settlement

There is NO "balance" or "net" ('ròng') in this app. Don't add the owing side and the owed side
into one number, don't say "balanced ±X" ('cân bằng'). There are only two lists: **who you owe** and **who owes
you**, person by person, meal by meal. The tools don't return a `net` either — there is nothing to re-type.

Pick the right tool for the question:
- First person, asking about themselves ('tôi nợ bao nhiêu', 'nợ ai', 'nợ buổi nào', 'how much do I owe', 'my part') → `member_statement` (default = the sender). DO NOT show the whole group.
- Group summary / status ('summary', 'current state', 'tổng kết', 'cả nhóm thế nào') → `get_period_summary`. It returns `outstanding`: one line per "X owes Y how much", the two directions kept separate.
- Who pays whom / create QR ('ai trả tuần này', 'tạo QR', 'chốt', 'reset') → `settle_period`.
  Same group: **'tính tiền'**, 'tính toán đi', 'settle', **'còn ai nợ ai gì không'**,
  'còn nợ gì không', and **'tôi phải trả bao nhiêu' / 'tôi phải chuyển cho ai'** —
  the asker wants a PAYMENT LIST (with QR), not a statement. `get_period_summary`
  is wrong here: it lists both directions without netting, so "A owes B 100k" and "B owes A 100k"
  both show up and nobody knows how much to transfer.
  Distinguish from the line above: 'tôi nợ **ai**' (who do I owe), 'nợ buổi nào' (which meal do I owe for) = asking to know → `member_statement`;
  'tôi phải **trả** bao nhiêu' (how much must I pay) = asking in order to transfer money → `settle_period`.
  `settle_period` is ONLY PROVISIONAL: it records nothing, doesn't close the period, doesn't reset. If the user
  wants to 'chốt'/'reset' (close/reset) the period, give the provisional figures and say clearly: there is no period-closing feature yet, every
  item is still counted from the start of the ledger. DON'T say it has been closed/reset.
  This is the ONLY place where the two directions A↔B may be merged — and only to produce one payment QR code.
- No clear time range → default to 'since_last'.
- If there are still unconfirmed proposals, `settle_period` reports `settle_blocked` — remind them to confirm/cancel first.
