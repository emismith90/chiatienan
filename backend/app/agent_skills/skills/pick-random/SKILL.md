---
name: pick-random
description: Randomly pick one person in the group — "bốc thăm ai trả", "random một người", "chọn đại ai đi mua đồ ăn".
---
# Draw one person

- `pick_random` lets the TOOL draw a member at random. NEVER pick the person yourself — you cannot be truly random, and the result must be decided by the tool.
- The draw is among the group's "default_participant" members (regular participants) — it takes no per-draw include/exclude list. If the user asks to 'leave someone out' ('trừ ai đó') or 'only among A, B, C' ('chỉ trong A, B, C') for ONE draw, explain that this can't be done per draw — to permanently exclude someone from DRAWS, use `update_member` with `default_participant:false`.
- `default_participant:false` **only affects `pick_random`**. It does NOT exclude anyone from
  "the whole group" ('cả nhóm') when splitting money: `find_members all_active:true` always returns the whole room. If you want
  someone not to pay for a meal, leave them out of that meal's `participants`.
- "Tôi ngồi ngoài" / "lượt này không tính tôi" / "bốc lại" (I'm sitting out / don't count me this round / draw again) = DRAW AGAIN (`pick_random`) —
  that concerns ONE round. NEVER call `update_member` to change
  `default_participant`: that is a lasting change for EVERY future draw, and the user
  didn't ask for it. Only change it when they say clearly that *from now on* they don't want to be drawn.
- Only draw when the user SAYS CLEARLY they want a draw: 'bốc thăm', 'random', 'roll', 'chọn đại',
  'ai rót trà'. "Hôm nay ai trả tiền?" / "ai trả tuần này?" (who paid today / this week?) are QUESTIONS ABOUT THE LEDGER (who has paid)
  → use `get_period_summary`/`settle_period`, NEVER draw. Drawing when
  people are only asking for information invents a payment obligation.
- What the draw is for ('trả tiền', 'đi mua đồ ăn') → pass it verbatim into `label`.
- The result card already shows the chosen person's name; reply briefly, DON'T re-type the name (re-typing is the only way to get a correct result wrong).
