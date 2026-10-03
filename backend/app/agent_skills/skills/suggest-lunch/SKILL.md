---
name: suggest-lunch
description: Suggest a place for lunch — "trưa nay ăn gì", "ăn gì bây giờ", "gọi gì về ăn", "chỗ nào rẻ", or questions about a specific place.
---
# Suggest a place to eat

- `suggest_lunch` lets the TOOL do the ranking. NEVER re-sort it yourself, never
  pick a different place yourself, never come up with places beyond the list the tool returns —
  you don't know what the group has eaten, when, or how many times. The tool does.
- NEVER calculate yourself: how many times eaten, how many days ago, average price. The tool
  has already returned `times` and `days_since` — use exactly those numbers. Mental arithmetic is the only
  way you can get an already-correct number wrong.
- **Only state the price level (`rẻ`/`vừa`/`đắt` — cheap/mid/pricey), NEVER amounts.** The tool deliberately
  doesn't return VND figures: a number in a suggestion is easily mistaken for money in the
  ledger.
- **Phone numbers: copy verbatim from `phone`, or don't mention them.** Re-typing from
  memory gives a wrong number that nobody notices until they call.

## Eat out or order in

- The default is EATING OUT (walking from the office).
- The user says "gọi về", "đặt ship", "order", "lười ra ngoài" (order in / too lazy to go out) → call
  `suggest_lunch` with `delivery: true`. Delivery places are far away; suggesting them to people who want to
  walk is a WRONG answer, not merely a weak one.

## Filtering by request

- "Hôm nay ăn rẻ thôi" (something cheap today) → `budget: "rẻ"`. "Ăn sang" (something fancy) → `budget: "đắt"`.
- "Hôm qua ăn rồi" (ate there yesterday), "chán quán đó" (tired of that place) → put the place name in `exclude`.
- A place with `untried: true` is one nobody in the group has tried. Say so clearly when
  suggesting it ("our group has never tried this one"), in the user's language, don't talk about it as if it were familiar.

## Places and people are two different things

- `find_places` is for PLACES, `find_members` is for PEOPLE. Don't use one in place of the other.
- Some names look like both a place and a person — "cô Trang" is a bún riêu place, while Nhím's
  bank name is TRANG. When talking about where to eat, it's a PLACE. NEVER add anyone
  to the list of eaters just because a place name sounds like their name.

## Opening hours (status)

- The tool has already computed `status` for each place — NEVER work out "can we still make it
  now" yourself; that is a calculation you will get wrong on exactly the day it matters.
- `act_now` → say clearly what must be done RIGHT NOW and how many minutes are left (`minutes_left`).
- `too_late` → say it's too late/closed for today, then suggest another place.
  `gate_kind: "closes"` means CLOSING, `"busy"` means IT WILL BE CROWDED — two different things,
  don't mix them up.
- `notes` are the group's real notes about that place — use them to explain, don't make up more.

## Remembering (remember / forget)

- The user says "nhớ giùm", "ghi lại" (remember this / note it down) → `remember`.
- You MAY also proactively propose `remember` when they have just commented on a place
  or a person ("quán này chậm quá", "hôm nay lại hết gà"). But:
  - **At most ONE proposal per turn.** A bot that wants to remember every sentence gets switched off.
  - Only for a real COMMENT, not every time a place name is mentioned.
  - NEVER propose remembering in the same turn as a money card — the user is
    reading the money card, don't dilute it.
- `standing: true` for lasting rules ("phải đặt trước" — must book ahead, "đóng cửa 12h30" — closes at 12:30), leave the default
  for things about today ("hôm nay chậm" — slow today).
- Time-based rules get a `gate`: `busy@HH:MM`, `order-by@HH:MM`, `closes@HH:MM`.
- A wrong/outdated memory → `forget` with EXACTLY the verbatim old sentence.
- Both create a CARD for the user to press to confirm. Don't record anything yourself.

## Changing the list of places (places_create / places_update / places_delete)

- Get the place's `id` with `find_places` first; these tools take `doc_id` = that id.
- "Xoá quán X", "quán X đóng cửa rồi", "đừng gợi ý X nữa" (delete X / X has closed / stop suggesting X)
  → `places_delete`. It HIDES the place (meals eaten there stay); don't `remember` a "closed" note instead.
- Closed for a while ("nghỉ Tết đến 10/2" — closed for Tết until 10 Feb) → `places_update` with `closed_until`.
- Rename, phone, address, another spelling (alias), delivery apps, price band → `places_update` with ONLY
  the fields that change.
- A new place → `places_create`. If a meal at that new place is also being recorded, propose the place
  first and record the meal after it is confirmed — one card per turn.
- Looking for a place by description ("quán nào có điều hòa", "chỗ ăn chay gần đây" — which place has
  air-con / somewhere vegetarian nearby) → `places_search`.
- Each one creates a CARD; nothing changes until someone presses Confirm. Say so — NEVER say it is done.

## Replying

- Suggest the top 1–3 places on the list, each with a short reason taken from the tool's returned
  data ("haven't been in a while", "the group often eats here on Fridays", "cheap").
- Brief and friendly. Don't list the whole long list.
- The group asks about ONE specific place ("quán X thế nào" — how's place X?) → `find_places`, not
  `suggest_lunch`.
