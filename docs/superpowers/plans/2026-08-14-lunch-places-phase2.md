# Phoenix Lunch Suggestion — Phase 2: Stats & `suggest_lunch`

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make "trưa nay ăn gì?" answerable — rank the room's places in Python from the ledger Phase 1 linked, and hand the model a decided list to explain.

**Architecture:** `places.stats()` derives per-place counts, recency, weekday rhythm and a price band from `meals`. `suggest_lunch` filters (walkable, open, budget) and ranks entirely in Python; the model never counts, averages or re-orders. A Vietnamese skill file teaches the tool loop.

**Tech Stack:** Python 3.12, SQLAlchemy (SQLite/WAL), pytest.

**Spec:** [`docs/superpowers/specs/2026-08-14-lunch-suggestion-memory-design.md`](../specs/2026-08-14-lunch-suggestion-memory-design.md)

**Phase 2 of 3.** Phase 1 (place identity, matcher, linking, backfill) is merged. Phase 3 adds the observations file, clock gates (`busy@`/`order-by@`/`closes@`), `remember`/`forget` and the memo card — `suggest_lunch` gains its `status`/`minutes_left` fields there.

## Global Constraints

- **D1 — numbers in Python, prose in the model.** Every count, interval, average and ordering is computed server-side. The model receives a decided list. Same authority rule as `pick_random`.
- **D5 — bands, never VND.** `suggest_lunch` returns `rẻ`/`vừa`/`đắt`. No raw amount is handed to the model in a suggestion context, so a suggestion can never be mistaken for a ledger figure.
- **`avg_per_head = total_amount ÷ len(shares)`.** `Meal.total_amount` is `tracked_total` (members only — `split_with_guests`, `money.py:341` already dropped the guest heads). Dividing by `len(shares) + len(guests)` understates per-head cost by the guest fraction.
- **Voided meals never count** (`Meal.voided`).
- **D14/D16/D11 filters:** `chưa-thử` places are demoted not hidden; non-`walkable` places are excluded from walk-out suggestions; `closed_until` in the future and `active=False` are excluded outright.
- **No frontend.** Phase 2 answers in plain chat text, like `pick_random`.
- **Tests:** TDD. Run from `backend/` with the venv active.

## File Structure

- `backend/app/places.py` — **modify**: add `stats`, `resolve_best`.
- `backend/app/tools.py` — **modify**: add `suggest_lunch`.
- `backend/app/agent_skills/skills/suggest-lunch/SKILL.md` — **create**.
- `backend/app/prompt.py` — **modify**: one routing line.
- `backend/tests/` — **create**: `test_places_stats.py`, `test_suggest_lunch.py`.

---

### Task 1: Ledger-derived stats

**Files:**
- Modify: `backend/app/places.py`
- Test: `backend/tests/test_places_stats.py`

**Interfaces:**
- Produces: `places.stats(session, room_id, *, window_days=120, today=None) -> dict[int, dict]`, keyed by place id, each value `{"times", "last_on", "days_since", "weekday_counts", "avg_per_head", "band"}`.

- [ ] **Step 1–5:** failing test → implement → pass → commit (see code below).

Bands are **tertiles across the room's own places**, not absolute VND, so they stay correct as prices drift. A place with neither history nor `price_hint` gets `band=None` and is never excluded by a budget filter — an unknown band cannot be ruled out.

---

### Task 2: `resolve_best` — D15's tie-break

**Files:**
- Modify: `backend/app/places.py`
- Test: `backend/tests/test_places_stats.py`

**Interfaces:**
- Produces: `places.resolve_best(session, room_id, text) -> tuple[Place | None, str]`.

Order candidates by (1) not `chưa-thử`, (2) meal count. Top wins outright if it beats the runner-up; a genuine tie returns `(None, "ambiguous")`. **Linking still uses `resolve_one`** — a wrong suggestion costs nothing, a wrong backfill link moves money history.

---

### Task 3: `suggest_lunch`

**Files:**
- Modify: `backend/app/tools.py`
- Test: `backend/tests/test_suggest_lunch.py`

**Interfaces:**
- Produces: tool `suggest_lunch(budget?, mood?, exclude?, delivery?)` → `{"ok", "candidates": [{"place_id", "name", "band", "days_since", "times", "phone", "tags", "untried"}], "mode"}`.

Ranking, entirely in Python: exclude closed/inactive; filter by mode (walk-out vs delivery) and `budget`; drop `exclude`; score = recency penalty + weekday affinity + `chưa-thử` demotion; ties broken by `random.choice` so the *tool* decides, mirroring `pick_random`.

---

### Task 4: The skill

**Files:**
- Create: `backend/app/agent_skills/skills/suggest-lunch/SKILL.md`
- Modify: `backend/app/prompt.py`
- Test: `backend/tests/test_suggest_lunch.py` (skill-loading assertion)

Vietnamese, matching `pick-random/SKILL.md`'s voice. Load-bearing rules: never re-rank; never compute counts or averages; speak in bands not VND; never retype a phone number (D10); `find_places` for places and `find_members` for people, never one for the other (D18).
