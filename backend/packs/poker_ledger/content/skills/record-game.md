---
name: record-game
description: Record a game / a card night — "tối qua chơi", "ghi ván", "kết bàn": how much each person bought in, how much they cashed out.
---
# Record a card game

Use `propose_game` (it only PROPOSES — the table confirms on the card). One call for the whole game.

- Each player gets ONE line in `entries`: `member` (id from `find_members`), `buy_in` (total chips bought), `cash_out` (chips exchanged at the end). Integer VND ('500k' → 500000).
- Rake / tip for the table → `house`. If none, leave it empty.
- The table must balance: Σ buy_in = Σ cash_out + house. If the tool reports a mismatch (`error` with the difference) → ASK again: who recorded too little/too much, or whether the difference is the table's money. DO NOT adjust a number yourself to make it balance, DO NOT calculate profit/loss yourself.
- Day ('tối qua', 'thứ 6') → `day_word`, let the tool work out the date.
- Who won, who lost, who pays whom how much: the tool calculates and the card shows it; you don't repeat the numbers.
- A confirmed game recorded wrongly → `void_game` with `game_id`, then propose again.
