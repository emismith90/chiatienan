# Production conversation review — 27 active days, 458 messages

**Date:** 2026-09-06 · **Source:** the live room, read through the HTTPS export API
(`/internal/debug/conversation.csv`, `/tables/meals.csv`, `/tables/members.csv`, `/logs`).
**Outcome: findings only, no code in this commit.**

Read against **production, which runs `main`** (head `5066d85`, 2026-08-26). The Agent OS /
headless-CMS work on `claude/headless-cms-pi-harness-nn18pb` (Phases 1–9) **has never been
deployed**, so nothing below is a report on the CMS. It is the input to it: the question this
note answers is *which of the room's real problems the content plane could fix by editing a
source, and which of them it cannot reach at all.*

Members are anonymised **A–G** (rule: no real names in docs). Quotes are the users' own
Vietnamese, with names substituted.

| label | member id | role in the quotes below |
|---|---|---|
| A | 4 | the room's heaviest user, usually the payer |
| B | 5 | |
| C | 6 | the other frequent payer |
| D | 7 | |
| E | 8 | |
| F | 9 | |
| G | 11 | joined late, `default_participant:false` since 2026-08-04 |

## What the room actually is

Four rooms exist; three are empty test rooms. **Room 3 is the product**: 7 members,
458 messages, 27 active days (2026-07-22 → 2026-09-05), 26 meals written (23 live, 3 voided —
8,229,960đ of member shares on the live ones) and 31 confirmed payment drafts. Every message
came from the web PWA (`source: web`).

| kind | count |
|---|---|
| bot replies | 217 |
| user messages | 180 |
| `expense_draft` cards | 26 (24 committed, 2 cancelled) |
| `payment_draft` cards | 31 (31 committed, 0 cancelled) |
| `memo_draft` cards | 3 (2 committed, 1 cancelled) |
| `context_reset` | 1 |

**The draft-card mechanic itself is the healthiest part of the app.** 60 cards, 3 cancelled.
When a card appears with the right numbers on it, people press Confirm and move on. Almost
every finding below is about the turns that never reached a correct card, or about what
happens *after* the ledger is right.

Where a count is split, it is split at **2026-08-15** — the skills and prompt were last
edited on 2026-08-14 (`55f1141`, `bdab61b`, `fa11581`), so "post" is the behaviour of the
code running today: 57 bot replies, 40 user messages.

---

## S1 — "Why is this number what it is?" cannot be answered. *(open, worst)*

**12 user attempts across 5 days. Zero answered.** Every one got a balance dump.

The clearest run, 2026-08-17, four attempts in seven minutes:

```
22:40  C: sao ngày 13-08 [C] phải trả [F]
22:41  BOT: C — owes and is owed: You owe nobody, and nobody owes you.
22:42  C: sao ngày 13-08 [C] phải trả [F]                    (verbatim resend)
22:43  BOT: C — owes and is owed: You owe nobody, and nobody owes you.
22:44  C: sao ngày 13-08 [C] phải trả [F]. Ghi detail tính toán nợ hidden đằng sau đó xem nào
22:45  BOT: Summary through 2026-08-17: 14 meals, 47 payments across 15 days — details below.
22:47  C: sao ngày 13-08 [C] phải trả [F]  /  Phoenix
22:47  BOT: C — owes and is owed: You owe nobody, and nobody owes you.
```

The next morning F asked the same class of question about the same meal
(`anh [C] có ăn bún cá đâu` — "C never ate that"), got a period summary, and **a human had
to answer it by hand**: A wrote three messages explaining that the row was a logged transfer,
not a debt. Earlier instances: 2026-07-22 `sao [C] chỉ nợ tôi 14k`, 2026-07-27 `sao [C] lại nợ
[F] 107k` (twice), 2026-07-24 `anh [C] đã trả tôi 75 đếu đâu`.

**Root cause.** `balances/SKILL.md` routes first-person questions to `member_statement`,
group questions to `get_period_summary`, "who pays whom" to `settle_period`. All three answer
*what the balance is now*. **There is no tool that answers "where did this number come
from"** — no per-edge provenance, no "this 58,333đ is your share of meal #13 on 13-08, which
you have already settled". So the router does the only thing it can: it re-answers a question
nobody asked. Note the last reply above is even *correct* — C owed nothing — which is why no
validator, no `capped` flag and no error ever fired.

This is also the **blame/verification gap already in `TODO.md`** arriving as a support load:
people cannot audit the ledger, so they argue with each other in the room instead.

**Not fixable from the CMS.** A skill can only route to tools that exist. This needs a new
read-only tool (`explain_debt` / `member_timeline` — the ledger already stores every edge by
ref; `ledger_core` keys debt edges by ref on the branch), and *then* a skill route for
`sao / tại sao / why / đâu / giải thích`, which is a CMS edit.

## S2 — 3 of 26 meals (12%) had to be voided *(open)*

| meal | what happened |
|---|---|
| #15 / #16 | the same 280,000đ meal recorded **twice, two minutes apart** (13:52, 13:54, 2026-08-17). #15 voided. |
| #19 | `[E]: [A] trả tiền 3 đĩa bánh cuốn 120k nữa` → recorded **420,000đ**, the total of meal #18 logged 47 minutes earlier. Voided; re-logged as #20 with the right 120,000đ. |
| #22 / #23 | `[D]: ghi bữa nay bún riêu 245k [A], [E] tôi, [F]` → #22 with **A** as payer, then #23 with **D** as payer. Same 245,000đ. #22 voided. |

Two distinct causes, and only one is a prompt problem:

* **#19 is context bleed** — the previous meal's total survived into the next proposal. A
  money number the model carried instead of the tool computing it. `money-safety.mdc` forbids
  the model *computing* money; it does not forbid it *re-using a number from history* as an
  argument. A rule could tighten that, but the durable fix is a tool-side guard.
* **#15/#16 and #22/#23 are the absence of a duplicate guard.** `propose_meal` will happily
  write a second meal with the same total, same participants, same day. A "you already logged
  245,000đ for these four people today — same meal?" check belongs in the tool, not the
  prompt. #22/#23 also shows payer ambiguity: `[E] tôi, [F]` — "tôi" is the *sender*, and the
  first proposal guessed the wrong payer.

**Partly CMS-fixable** (a `record-meal` rule: "tôi/mình = the sender; never carry a total from
an earlier meal"). The duplicate guard is code.

## S3 — the confirm dead-end *(open)*

A draft that is neither confirmed nor cancelled blocks `settle_period`, and there is no way
out of it from the chat. 2026-07-27, **14 minutes, 3 users, 5 requests**:

```
17:19  F: tôi đang nợ ai bao nhiêu, xin qr luôn
17:20  BOT: Có 1 đề xuất chưa xác nhận — xác nhận hoặc huỷ trước khi chốt.  • #101 …
17:23  A: #101: A trả 324,000đ (6 người)? registered ?????
17:23  BOT: (identical)
17:23  A: xác nhận
17:23  BOT: Mình **không xác nhận qua chat** được — cần bấm Xác nhận trên thẻ nháp.
17:24  A: rồi mà? Đã ghi #6 — Grab Food: …            ← the meal WAS already committed
17:24  BOT: (identical)
17:25  F: xác nhận qua chat đi
17:26  BOT: (identical refusal)
17:28  D: có cách nào cho phép xác nhận qua chat không?
17:29  D: tôi cho phép thì sao
```

Still present in the current code, in a different shape — 2026-08-19: the "not recorded"
guard (`fa11581`) fired twice in a row, the user typed `confirm` into the chat (nothing
happens), then `/clear`, then retried and it worked.

Three things compound: the card can scroll out of view and there is no way to bring it back;
`settle_blocked` names a draft id the user cannot navigate to; and the refusal is a wall, not
a route. **Not CMS-fixable** — the draft routes and the room UI own this.

## S4 — the bot repeats itself instead of noticing it is stuck *(open)*

**15 pairs of consecutive byte-identical bot replies** (12 pre-08-15, 3 post — 5% of current
replies). Every S1 quote above is one. So is 2026-07-27 11:14/11:16, where F said the QR's
transfer memo was wrong (`sai mẹ nội dung chuyển khoản r`, then `vẫn sai, trả t5 t6 mà`) and
got the same two-line settlement back twice; and 2026-07-27 20:07, where C asked for the same
list reformatted as bullets and got the previous answer byte for byte.

The turn has the previous reply in `history`. Nothing tells it that repeating it is a failure
signal. **CMS-fixable, and cheap** — one prompt rule: *if your previous reply was the same
tool's output and the user asked again, do not re-run the tool; say what you cannot do and
name the alternative.*

## S5 — half-finished i18n: English cards, Vietnamese prose *(open)*

`ad8b707` (2026-08-14, "put the backend's user-facing strings in English") moved the
deterministic card bodies to English. Because almost every successful turn now *ends* in one
of those bodies, that one commit flipped the whole room:

**56 of the 57 bot replies since 2026-08-15 are English.** In the same window **32 of 40 user
messages are Vietnamese.** The single Vietnamese reply left is the memo confirmation, which is
model prose:

```
Recorded #22 — bún riêu: A paid 245,000đ total • A 61,250đ, E 61,250đ, D 61,250đ, F 61,250đ
💸 F paid A 61,250đ
🎲 Picked (rót trà): **E** — from 5 people.
Đã đề xuất ghi nhớ: "nay ăn ngự uyển bị thịt rang mắm tép cũ". Bạn có thể xác nhận trên thẻ…
```

Nobody in the room asked for this and nobody can undo it from the product. It is the sharpest
example of the gap the CMS was built to close, and of what the CMS as built still cannot
reach:

* `ProfileSpec.persona.language` exists, and `BindingOverrides.language` can set it **per
  space** — so the *model's* language is already a CMS field.
* The card strings are Python literals in `packs/lunch_ledger/render.py`,
  `packs/ledger_tools/render.py` (`f"Recorded #{…}: {payer} paid …"`, `f"{name} — owes and is
  owed:"`). **No source, no template, not editable, not exportable.** A publish cannot change
  them; an export omits them; a second business inherits English whether it wants it or not.

**The optimisation.** `ProfileSpec.templates` (`PromptTemplate`, kind `template|builtin`)
already exists and is already carried through export/import as `prompts/*.md`. Promoting the
pack render bodies to named templates resolved from the profile — with the Python literal as
the fallback when no template is published — makes the room's *deterministic* voice a CMS
edit, in the same place as its prompt, under the same publish gates. That is the single
highest-leverage change the content plane could take on next, and this room is the proof it
is needed.

## S6 — internal reasoning used to leak into the room *(fixed — recorded as evidence)*

15 of 160 replies before 2026-08-15 opened with the model narrating itself to the room:
`Người dùng muốn xem tóm tắt số dư — mình sẽ làm theo skill chốt kỳ.`,
`Mình đọc quy trình ghi bữa rồi xử lý…`. **0 of 57 since.** The 08-13/08-14 prompt work closed
it. Keep it as a regression case, not an open item.

## S7 — a provider error reached the room verbatim, twice, with no fallback *(open)*

2026-08-04 13:52 and 14:06, two different users:

```
⚠️ Model Blocked  This model has been blocked by your team admin settings.
```

Both turns were lost; the work was redone by hand five hours later. `ProfileSpec.models` has
`text`, `vision`, `thinking` — **one model each, no fallback list**. `Retry` retries the same
model. A provider-side block or a 402 therefore ends the turn with the provider's own English
sentence in a Vietnamese room.

**Needs a new CMS lever**, and it is a small one: `models.fallbacks: list[str]` (each entry
still gated by the model catalogue's probe, so gate 3 keeps its teeth), plus one
user-facing sentence that does not quote the provider.

## S8 — two turns produced no reply at all *(open)*

2026-08-14 14:47 and 16:28 both stored the literal body `(không có phản hồi)`. One of them was
G asking `I should pay the same amount. Please recalculate my share` — a real request,
silently dropped, and G then paid the wrong amount by hand. That literal is no longer in
`main`, so the fallback text has changed, but a turn that yields nothing still needs a visible
"that failed, say it again" rather than a bubble.

## S9 — 3% of user messages were retyped only to add the @mention *(open)*

6 of 180. Twice the lost message was a **one-character answer to the bot's own question** —
the bot had just written "Chọn 1 hoặc 2", the user sent `1`, nothing happened, the user sent
`@bot 1`. The others are ordinary ledger writes (`tôi đã trả tiền [A]`, `hôm nay ai trả
tiền`, `chọn người rót trà khác đi`) sent bare, then retyped in full.

Cheap fix, and it is a host rule rather than a prompt one: when the bot's last message in the
room asked a question, the next human message should reach it without the mention.

## S10 — smaller things, with their evidence

* **Duplicate QR cards.** Three byte-identical QR cards inside one minute (2026-07-31 12:40),
  two more on 2026-07-28. No user action between them.
* **A one-token-per-line render.** 2026-07-27 17:29, two consecutive replies were stored with
  every word on its own line (`Không\n\n—\n\nhiện\n\n**\n\nkhông\n\n…`) — ~200 lines for two
  sentences. Deltas joined with `\n\n` instead of `""`. Seen once, not since; worth a look at
  the delta-accumulation path rather than a fix on spec.
* **Formatting requests cannot be honoured.** `viết cụ thể từng ngày format cho thành gạch
  đầu dòng đi` got the previous answer verbatim, because the body was a deterministic card,
  not model prose. That is the money-safety trade working as designed — but the reply should
  say so, not repeat itself (see S4).
* **The itemised-split fight is over.** 2026-07-27 cost 65 minutes and 11 turns: a Grab bill
  where the item prices (414,200đ) exceeded what was paid (324,200đ), the model correctly
  refused to compute the proration, the user escalated (`ủa sao không tính được, viết python
  ra mà tính` → `execute it via bash?` → `I allow it explicitly`), and the guard eventually
  yielded to explicit consent. `propose_meal` now takes `items` + `discount_split`, and
  `packs/lunch_ledger/eval.py` carries this exact scenario as a case. Closed; noted because
  it is the best example in the log of a guardrail that was right and still cost an hour —
  the fix was a tool, never a firmer prompt.
* **The randomiser rules contradicted themselves** on 2026-07-29 (excluded a person at 13:08,
  refused to exclude anyone at 13:36) and users distrusted the draws (`Vô lý vãi. Phải tính
  random chứ`). `pick-random/SKILL.md` has since been hardened on exactly this point. Watch,
  do not act.

---

## The finding that matters most for the CMS: the steward brief would have caught almost none of this

`kernos.osadmin.STEWARD_BRIEF` step 1 is `cms_get_turns(limit=30, only_flagged=true)`, and
`cms_get_turns` defines:

```python
flagged = bool(s.get("verdicts")) or bool(s.get("capped")) or bool(s.get("error"))
```

Run that over these 27 days and it returns **S7, and possibly S8. Nothing else.**

Everything else in this note is a turn that ran clean: valid tool calls, no moneyguard
verdict, no cap, no exception. The four `You owe nobody` replies were *correct*. Meal #19's
420,000đ came out of the tool, so the number was backed. The duplicate meals were two
well-formed `propose_meal` calls. The English card bodies are the render layer doing exactly
what it was told. A self-review that only reads flagged traces is reviewing the crashes of a
system whose problem is that it does not crash.

**What the signal actually looks like** — and all of it is deterministic, computable in
Python, no model needed, no number the model could launder:

| detector | hits in this room |
|---|---|
| consecutive byte-identical bot replies | 15 |
| a user resending a near-identical message within N minutes | 14 |
| a user message retyped only to add the mention | 6 |
| a meal voided within 24h of being written | 3 |
| a draft cancelled rather than confirmed | 3 |
| a reply whose language ≠ the language the space writes in | 56 of 57 since 08-15 |
| a turn that produced no text | 2 |

That table *is* the review. Handing it to the agent — as a tool, alongside the traces it
already reads — is the difference between a steward that can see this room and one that
reports "nothing flagged, all good" for 27 days.

## What to do, in order

1. **`cms_get_friction`** (or a conversation-level flag on `cms_get_turns`): the detectors
   above, computed host-side from the space's own messages, returned as counts plus example
   ids. Read-only, `evidence = False` like the rest of `os_admin`, so no number it returns can
   back a claim. **This is the prerequisite for the "ask the agent to review itself" flow** —
   without it that flow reviews the wrong evidence.
2. **`explain_debt` / `member_timeline`** (S1) and a `propose_meal` duplicate guard (S2). Both
   are ledger tools, both are code, both are the two that cost real money and real arguments.
3. **Card bodies as `templates` sources** (S5) — the content plane's own gap, and the one that
   makes "the CMS owns the agent's voice" true rather than half-true.
4. **`models.fallbacks`** (S7), and the confirm dead-end (S3).
5. **One prompt rule for S4**, which is a CMS edit and needs none of the above — the smallest
   possible first proposal for a steward to make, and a good end-to-end test of the flow.

Nothing in 1–5 is blocked by the branch: they are all changes to code or content that the
Phase 1–9 framework already has a place for.

---

## Appendix — "ask in chat, get a draft card + diff back": what is already built

The flow wanted is: a person says *review yourself* in the room → the agent reads its own
recent work → it proposes one change to its own configuration → the room shows a **draft card
with the diff** → someone presses Confirm and the change is published under the gates.

Four of the six pieces exist on this branch (Phase 8, `kernos/osadmin.py`):

| piece | status |
|---|---|
| capability gate (`capabilities.cms`, `self_change_scope`, blacklisted fields, money-tagged rules refused) | **built** |
| the review tools — `cms_get_turns`, `cms_get_turn_trace`, `cms_get_eval_results`, `cms_log` | **built** (but see the finding above: they read the wrong evidence for this room) |
| drafting — `cms_draft_change` already returns a **unified diff** and `changed_paths` | **built** |
| proposing — `cms_propose_publish` creates a proposal carrying `diff.paths` + `diff.unified`, `source_changes`, the eval run id, and `base_version_id` | **built** |
| approval — `Kernel.approve_proposal` / `reject_proposal`, all gates | **built**, admin API only |
| the shipped brief (`STEWARD_BRIEF`, `GET /api/admin/steward/brief`) | **built**, as text an operator installs |

Two are missing, and they are exactly the two the request names:

1. **A trigger from the room.** The brief is text at an admin endpoint; nothing turns
   `@bot review yourself` into a run of it. The natural shape on this branch is a `sub` agent
   (`ask_steward`, Phase 7 delegation) whose profile carries the brief as its prompt and
   `capabilities.cms = ["read", "draft"]` — draft and propose, never publish. Delegation
   already merges a sub's result as `from_agent` and never lets its text back a number.
2. **The card.** `OsAdminPack.render` currently returns a plain line —
   `📋 Proposal #7 opened for v4: … A person approves it at /api/admin/proposals/7` — and
   there is no room card, so approval leaves the product entirely. What is needed is a draft
   kind (`cms_change_draft`) whose payload carries `proposal_id`, `rationale`, `paths` and the
   unified diff; a `CmsDraftCard` in the frontend that renders the diff monospaced with +/−
   colouring (the generic `DraftCard` lists fields and would print a diff as one long string);
   and Confirm/Cancel on the generic draft routes wired to `approve_proposal` /
   `reject_proposal` **with the pressing human as the actor** — the gates already refuse an
   `agent:` actor, and that is the property the card must not launder.

Sequencing note: item 1 of *What to do, in order* (`cms_get_friction`) comes before both. A
steward wired to today's `only_flagged` traces would have reported "nothing flagged" for every
one of these 27 days, and the first draft card the room ever saw would have been a proposal
nobody needed.
