---
name: pick-random
description: Randomly pick one person in the group — "bốc thăm ai trả", "random một người", "chọn đại ai đi mua đồ ăn".
---
# Draw one person

- `pick_random` lets the TOOL draw a member at random. NEVER pick the person yourself — you cannot be truly random, and the result must be decided by the tool.
- The draw is among the room's saved **draw list**. People change it in the Lucky Draw dialog (🎰 in the header); you change it with `edit_draw_list` (`add` / `remove` take member_ids from `find_members`; no arguments = just view it). 'Bỏ An ra khỏi danh sách bốc thăm' / 'thêm Bình vào' (take An off the draw list / add Bình) → `edit_draw_list`. 'Ai đang trong danh sách?' (who is on the list?) → `edit_draw_list` with no arguments.
- The list is SAVED: a change stays for every later draw. There is no one-draw-only list. 'Chỉ trong A, B, C' / 'trừ An' (only among A, B, C / everyone except An) → edit the list, draw, and say clearly the list stays changed until someone changes it back.
- The draw list only affects draws. It does NOT exclude anyone from
  "the whole group" ('cả nhóm') when splitting money: `find_members all_active:true` always returns the whole room. If you want
  someone not to pay for a meal, leave them out of that meal's `participants`.
- "Bốc lại" (draw again) = `pick_random` again, no list change.
- Only draw when the user SAYS CLEARLY they want a draw: 'bốc thăm', 'random', 'roll', 'chọn đại',
  'ai rót trà'. "Hôm nay ai trả tiền?" / "ai trả tuần này?" (who paid today / this week?) are QUESTIONS ABOUT THE LEDGER (who has paid)
  → use `get_period_summary`/`settle_period`, NEVER draw. Drawing when
  people are only asking for information invents a payment obligation.
- What the draw is for ('trả tiền', 'đi mua đồ ăn') → pass it verbatim into `label`.
- The result card already shows the chosen person's name; reply briefly, DON'T re-type the name (re-typing is the only way to get a correct result wrong).
