---
name: record-meal
description: Record a group meal — "840k cả nhóm trừ An", "bún bò 300k 5 người", everyone-pays-for-what-they-ate ('ai ăn nấy trả') from a bill, with guests, with adjustments.
---
# Record a meal

1. `find_members` to identify the payer + participants (`all_active:true` for 'cả nhóm').
   - `all_active:true` returns **ALL** active members of the room, filtering no one.
     Use it for Vietnamese ('cả nhóm', 'cả team', 'mọi người') and English
     ('everyone', 'all', 'for all', 'the whole group') alike.
   - To leave someone out of this meal, drop their id from `participants` — only when the user
     SAYS SO CLEARLY. Nobody is excluded "by default": the number of people in `participants` must match
     what the user sees on the member bar.
   - Pass names EXACTLY as the user wrote them ("anh Hưng", "chị Nhím"). The tool strips
     "anh/chị/em" itself, strips diacritics, and looks up both real names and **bank account holder names** —
     many people in the group only have their real name there ("Hưng" = account holder "Le Hoang Hung").
   - **An `unresolved` / `ambiguous` result MUST NOT be ignored** (see the section below).
   - «tôi»/«mình»/«tớ» (I/me) = THE SENDER. Their `member_id` is already in the system prompt,
     so there's no need to look it up and **NEVER ask "who are you" ('bạn là ai')**. "Tôi trả" (I paid) → leave `payer` empty
     (the tool takes the sender) or pass exactly that id.
   - "Tôi với Bình ăn" (Bình and I ate) = `participants` contains BOTH ids. The sender is also an eater —
     don't drop them from `participants` just because they are the payer.
2. `propose_meal` with payer, participants (ids), total (bill total), and `items` OR `adjustments`,
   plus guests/dish/initiator/note if any.
   - 'trừ An' (except An) = An is NOT in participants.
   - Only saying "trừ An" / "An không ăn" (An didn't eat) without saying who ate → default to **THE WHOLE GROUP except An**
     (`find_members all_active:true` then drop An). DON'T ask "so who ate?".
   - Not saying who ate at all ("I paid 1107k chả cá ông già") → default to **THE WHOLE GROUP**
     (`all_active:true`), propose right away, and say clearly in the reply that you assumed the whole
     group so they can fix it on the card if wrong. Draft cards are editable; asking means every turn has to
     ask twice.
   - 'An trả nhưng không ăn' (An paid but didn't eat) = An is the payer but not in participants.
   - 'Bình +50k' = adjustment {member: <Bình's id>, amount: 50000}.
   - "X rủ đi" / "X rủ" (X invited us) = `initiator` (the organiser), NOT an eater: only add X
     to `participants` when the user says X also ate.
   - Someone from outside the group ate too ("+ 1 khách", "2 đứa bạn nữa", "có khách") → pass
     `guests` (names if known, otherwise "guest 1", "guest 2" — 'khách 1', 'khách 2' in Vietnamese). Guests reduce each person's share
     but are NOT charged; leaving out `guests` splits wrongly for everyone.
   - `propose_meal` ONLY PROPOSES — the user confirms on the draft card.

## Eaters that `find_members` can't find

Saying in your reply that you "treat X as a guest" RECORDS NOTHING — the draft card only has what you
pass to `propose_meal`. Missing one head means everyone else pays more than they really should,
and nothing on the card shows that someone is missing. For each `unresolved` name, choose ONE:

- Someone outside the group → put that name in `guests` (guests count as a head but are not charged).
- Suspect it's a member written under a different name → `find_members` again with another name (real name,
  bank name, nickname) before concluding it's a guest.
- A new member → `add_member` then put the id in `participants`.

An `ambiguous` name (two people match, e.g. "Trang") → ASK the user which one, don't pick at random.
`propose_meal` will return an error if a name that failed lookup is in neither `participants` nor `guests`.
- Edit/delete: `void_meal` to delete; to edit, void then `propose_meal` again.
- Date: if the user names a day explicitly ('thứ 2', 'hôm qua', '20/7'), pass it verbatim into `day_word` of `propose_meal` — the tool works out the date (VN time), NEVER infer the date yourself. No day mentioned → leave it empty (defaults to today).

## Everyone pays for what they ate ('ai ăn nấy trả', recorded per dish) — use `items`

When the user says who ate what ("emi ăn bò, nhím gà, linh với kun cơm tấm"), or asks
"ghi theo từng người được không" (can you record it per person?) → **use `items`**, DO NOT split evenly and DO NOT stuff that information
into `note`.

**Only use `items` when you KNOW who ate which dish** — the user said it, or the bill has a name
next to each dish. If the bill lists many dishes but has NO names, and the user only says who ate together
("tôi với Bình và Cường ăn") → **SPLIT EVENLY**, drop `items`. Assigning dishes to people yourself is making things up: it changes
what each person has to pay, and nobody can spot it because the numbers still look plausible.

- Each participant gets exactly ONE line `{member, amount, label}`; `amount` is **the price on the bill**.
- `member` is **the id of the person who ate that dish**. A name written on the bill (or in the message) must
  go through `find_members` to get the id first — being able to read a name on the image does NOT mean you know the id.
  NEVER pile every dish onto one person and leave the names in `label`: that charges the whole bill
  to one person. `participants` must also include all of those people.
- One line "2x cơm tấm 138.000đ" for Linh and Kun → 69.000đ each.
- **Σ items need not equal `total`.** Discounts / delivery fees / service fees are normal —
  the tool splits the difference itself. DON'T calculate the "post-discount amount" yourself, don't make the
  user calculate it for you, and don't give up because Σ items > total.
- How the difference is split: `discount_split="proportional"` (default, proportional to dish prices) or
  `discount_split="equal"` (everyone gets the same deduction/surcharge). The user says "chia đều phần giảm",
  "mỗi người trừ như nhau", "chia đều delta" (split the discount evenly) → use `equal`. The tool calculates, not you.
- `total` is always the amount **actually paid** (stated by the user, or the final total line on the bill).
- Per-dish recording with individual guests (guests) is not supported yet — in that case split evenly.

## Bill images

- A bill image in this turn's context (including one the user pasted in the message right before
  `@phoenix`) is usable — **read it straight away**, don't ask again for what the image already shows.
- Reading the image does NOT replace identifying the eaters: you still have to `find_members`
  (`all_active:true` when the user says "cả nhóm"/"cả team"/"mọi người"/"everyone"/"all") before
  `propose_meal`. Skip that step and `participants` is just the sender — the whole bill
  is charged to one person, and the number still looks "right" so nobody notices.
- From the image: the total actually paid → `total`; each line's price → `items` (remember to multiply by quantity, and a
  struck-through price is the original price — take the price that applies).
- In the conversation history, `[image: N]` means that message had images. If you need an image that this turn
  can't see, ask the user to resend it **once** — don't ask again after that.

## Asking back — at most once

Only ask when something that CANNOT be inferred is missing: the payer is a third person whose name can't be
looked up, or the total when there is no bill.

- «tôi trả» (I paid) is NEVER missing information: who the sender is is already in the prompt.
  Asking back "who are you in the group" ('bạn là ai trong nhóm') is a bug — just propose with the sender as the payer.

- Enough to propose → call `propose_meal` right away. Draft cards are editable, so proposing beats asking.
- DO NOT ask again for information the user already gave in an earlier message in this turn/history.
- DO NOT ask for per-dish prices when there is a bill — read them from the bill.
- DO NOT ask the same question twice. If you asked last time and something is still missing, choose the most reasonable option,
  propose, and say clearly what you assumed.
