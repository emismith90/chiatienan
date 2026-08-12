# Cursor → Pi: what the harness actually measured

**Status: the port is complete, and the harness is now honest enough to be worth
reading.** A benchmark that reports only its wins is worse than no benchmark,
because it will be believed — so this file reports what was measured, what was
fixed, what is a judgement call, and what is still not measured at all.

Reproduce:

```bash
cd backend
python -m bench.run --corpus typical --engine pi --repeat 3 --out bench/results/pi-typical-r3.json
python -m bench.run --corpus prod    --engine pi --repeat 1 --out bench/results/pi-prod-r1.json
python -m bench.report bench/results/pi-typical-r3.json
python -m bench.report --compare bench/results/baseline-prod-cursor.json bench/results/pi-prod-r1.json
```

## The headline

| corpus | turns | `tool_selection` | `ledger_state` | `prose_quality` | latency p50 / p95 |
|---|---|---|---|---|---|
| `typical` (golden meals + week + bills + prod representatives), `--repeat 3` | 111 | **105/105 = 1.00** | **60/60 = 1.00** | n/a by construction | 6.7s / 37.2s |
| the same corpus again, after the last two fixes | 111 | **105/105 = 1.00** | **60/60 = 1.00** | n/a | 9.7s / 59.3s |
| the same corpus, first full run | 111 | 69/105 = 0.66 | 39/60 = 0.65 | — | 16.0s / 114.9s |
| `prod` (107 real turns), `--repeat 1` | 107 | 69/77 = 0.90 | n/a | **18/22 = 0.82** | — |

Zero errored turns in any of them. The `typical` result is **two consecutive
`--repeat 3` runs at 1.00**, the second one after the last two prompt changes
landed — 222 turns with no money grader failing, which is what makes it a rate
rather than a lucky sample. `prose_quality` is judged by reading every reply
against the rubric in `bench/graders.py` (`judge_model: "claude-opus-5 (agent)"`),
the same way the baseline's 26 were.

Eleven prod cases pass `tool_selection` on a **judged alternative tool** — a
read-only tool that answers the question as well as production's choice, each with
its reason in `bench/corpus/prod_judgements.py`. The report prints that count above
the rate, and three rules keep it from being a way to make numbers look good: only
read-only tools, a written reason per entry, and money arguments never widened.

### The eight prod cases that still fail, and what they are

A targeted re-run of all eight at the final prompt
(`bench/results/pi-prod-residuals.json`) recovers three; five remain, and none of
them is a money error the harness can call wrong with a straight face:

| case | what happens | verdict |
|---|---|---|
| `p93` "sai mẹ nội A4 chuyển khoản r" | asked which entry was wrong; now reads the ledger first | **fixed** in the re-run |
| `p152` "hôm nay ai trả tiền" | drew a *random* payer for a question about who already paid | **fixed** — the draw skill now says an information question is never a draw |
| `p160` "grab food hôm nay à" | asked what they meant; now answers from the summary | **fixed** in the re-run |
| `p78` "I paid 335k, A4 A6, A1, và G3" | left the payer out of `participants` | genuinely ambiguous — a list of other people may or may not include the payer, and assuming it does would charge people who did not eat |
| `p95` "vẫn sai, trả t5 t6 mà" | **recorded the payment** the user was insisting on; prod re-printed a settlement | arguably better than the recorded answer, and unwidenable: a judgement may never accept a money-writing tool |
| `p140` "xin lại thẻ nháp" | correct prose answer (the card's buttons are the only way) with no tool call | right answer, no verifiable tool — left failing rather than judged |
| `p242` "I paid bún đậu mẹt 250k" | split over 7 members where prod split over 5 | `default_participant` history is unrecoverable: two members were opted out later in the log, and the flag has no timeline |
| `p281` "đã trả chị A2" | paid A2, as the sanitized message says; prod paid A5 | pseudonymization artifact — prod resolved a real name the corpus has replaced |

**Almost none of that first gap was the engine.** Chasing the failures one case at
a time found **nine defects in the harness** against **ten in the engine and
prompt**: the benchmark was asking the model questions production never asks, then
recording the answers as failures.

## The harness was wrong about this much

| # | what was wrong | how it showed up |
|---|---|---|
| H1 | `bench.run` never passed `sender_name`, though `chat.py:489` does | `G4` asked *"bạn là ai trong nhóm nhỉ?"* and proposed nothing, on all three repeats |
| H2 | prod cases replayed **without their conversation** | `p120`'s entire message is "@bot log"; `p129` expects 27,000đ that appears nowhere in its own text |
| H3 | prod cases replayed against an **empty ledger** | `p20` "@bot paid my part" → *"bạn không nợ ai"*, graded as failing to propose a payment |
| H4 | the **roster** was today's, on every date | `p12` "chia 5 trừ A2" is ambiguous in a room of 7; that day it had 6 |
| H5 | a card posted after a **Confirm** was paired to the message above it | `p266`, a joke asking for a lottery number, expected `settle_period` |
| H6 | the grader compared **argument shapes**, not the money recorded | `p120` passed `items` + `discount_split="equal"`; the tool prorated them into exactly the expected shares |
| H7 | `moneyguard` did not count the **history** as backing an amount | `p102`/`p104` flagged for quoting a total the room had stated a message earlier |
| H8 | three prod expectations **cannot be graded at all** and said nothing about it | `p100`/`p260` read their numbers off a stripped bill photo; `p278`'s recorded card belongs to another exchange |
| H9 | the prose judge was handed **production's replies** to grade as Pi's | 20 of them, verbatim, narration and all — `--corpus` supplies bodies for the *baseline*, whose own were stripped, and it was overriding the record instead of filling in for it |

**When a benchmark and a model disagree, suspect the benchmark first.** Every one
of H1–H8 looked exactly like a model failure in the results table, and two of them
(H1, H3) looked like timidity when the model was in fact being sensible about a
room that made no sense.

## And the engine was wrong about this much

| # | what was wrong | how it showed up |
|---|---|---|
| E1 | the prompt named the sender but not their `member_id` | H1's other half — a name is not actionable when every tool takes ids |
| E2 | **images never reached the model** — Anthropic's nested block vs pi's flat `ImageContent` | `B1`–`B3`: no tools, no text, no error, every repeat |
| E3 | `items` invented when nobody said who ate what | `B2` charged one member 80,308đ of a bill that splits to 87,000đ |
| E4 | "tính tiền" answered with a statement or a summary | `s5`, `s8`, `s10b`, `s12` — both directions listed uncancelled, so nothing to transfer |
| E5 | `keyword="explicit"` with no dates **raised**, and the `ValueError` reached the model | three wasted round trips in one run |
| E6 | "trừ An" with nobody else named → asked who ate; "X rủ đi" counted X as an eater; "+ 1 khách" dropped `guests` | `G2` 1/3, `G12` split a 200,000đ meal three ways, `s4` spread a guest's share over four members |
| E7 | a sentence with no subject took its payer from the ledger instead of the sender | `p228` "just paid 53k to A1" booked A6's payment as A7's |
| E8 | "A4 ngồi ngoài" called `update_member(default_participant=false)` | one round of sitting out became a permanent change to every draw and every "cả nhóm" split |
| E9 | `update_member` was called with a **name** where `target` takes an id, and the tool's "No member found" was repeated to the user as fact | `p72` told someone a member of the room was not in it |
| E10 | "hôm nay ai trả tiền" answered with a **draw** | `p152` picked a random person to pay for a question about who already had |

Each engine fix was verified on the case that produced it before the next full run,
and every one of them lives in the prompt, a skill, or a tool description — no
arithmetic moved anywhere near the model (design D3).

## The `bash` trade-off, corrected

An earlier version of this report concluded that enabling
`PI_BUILTIN_TOOLS=read,write,bash` "broke the arithmetic-heavy cases" and that
ranking the tools in the prompt fixed it. The measurement was real
(`pi-typical-iter1.json`: `tool_selection` 17/35, `ledger_state` 7/20 with `bash`
enabled) and **the conclusion was wrong**: `G4` then failed **3/3** under
`--repeat 3` with the ranked prompt, which is the caveat that report recorded and
this one closes. The cause was E1/H1 — the model did not know who "tôi" was, and
the five most arithmetic-heavy cases are exactly the ones where not knowing that
is fatal. With the sender's id in the prompt, those five pass and stop reaching for
`bash` at all.

The ranking stays: it is correct on its own terms, and money still must never be
computed outside a tool. It simply was not what recovered those cases.

## Verified, not assumed

* **Both models probed against the real tool schemas** (`bench/probe_models.py`,
  not a catalogue's `supported_parameters`): `~deepseek/deepseek-v4-flash-latest`
  3/3 schemas; `qwen/qwen3-vl-30b-a3b-instruct` 3/3 plus reading `154000` and all
  three dish names off the committed bill PNG.
* **The seeded prod worlds hold production's own money.** For the 67 cases where
  the preceding commit card carries a `balances` list, **65 match to the đồng**. The
  two that differ (`p167`, `p169`) are off by exactly payment #15 — 54,500đ, written
  at 20:14:05, 23 seconds before the turn at 20:14:28 — which the card being
  compared against predates. The world is right there; the card is stale.
* **`/internal/bridge-smoke`** against the real process:
  `{"ok": true, "elapsed_s": 1.1, "messages_seen": 1, "text": "pong"}`.
* **749 backend tests + 65 sidecar tests green.** Every fix above has one.

## Two privacy holes, found by reading the output

The corpus is gitignored, `sanitize` is CI-tested, and **a human reads the output** —
and the third line of defence is the one that found both of these.

1. **A decomposed name is a different string.** One production body arrived NFD:
   "Nhím" as `N h i ◌́ m`. The member table holds the composed form, so the pattern
   matched nothing and a member's nickname went through untouched. Both sides
   normalize to NFC now.
2. **A real message was in the committed baseline.** `bodies_included: false`
   dropped `message` and `final_text` and left `result.raw_input` — the same
   sentence, stored on a draft so the card can show what it was built from. It is
   stripped now, and `verify` scans the committed baseline for every real name on
   word boundaries, case-insensitively, over normalized text.

Also added: `extra_aliases.txt` (gitignored) for names the member table cannot
supply — the room calls one member by a given name that is not their display name,
nickname or alias, and the same gap made `p148` unanswerable. And the review list
now prints **all** candidates rather than the top 20, because a real name is rare
and the frequent entries are sentence-initial words: one of these sat at rank ~25.

**Note for the repo owner:** that name was in `baseline-prod-cursor.json` in the
earlier commits of this branch. HEAD is clean and the branch is unmerged, so the
history can be rewritten on request.

## Still not measured

* **`docker compose build`** — no Docker daemon in this environment (`docker info`
  fails). `npm ci --omit=dev --ignore-scripts` was verified against the committed
  lockfile; the NodeSource install line is what to watch on the first real build.
* **`prose_quality` on the golden corpora.** n/a there by construction: a draft
  turn's prose is never posted (`chat.py` renders the card), so there is nothing a
  reader would see. Prose coverage comes from the prod corpus.
* **Any second engine.** Cursor is gone from the tree, so the baseline is a
  recorded log rather than a re-run, and `bench.report --compare` prints a warning
  saying its `tool_selection` is 1.0 by construction.
* **`--repeat 3` on the prod corpus.** 107 cases × 3 is ~2 hours of provider time
  and the prod side of the comparison cannot be re-rolled anyway.
