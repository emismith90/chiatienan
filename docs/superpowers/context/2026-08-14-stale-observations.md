# Context — what to do about stale observations

**Status:** deliberately unbuilt. This is a briefing for a fresh session. Read it before deciding anything; the current behaviour is a choice, not an omission.

**The position to argue against:** a dated observation past 180 days is flagged in the UI and left in the file forever. Nothing prunes it. That was deliberate — **a bot that deletes its own memory on a timer is a worse failure than a long file** — and any change here has to beat that, not just tidy up.

## How staleness works today

`observations.DEFAULT_SINCE_DAYS = 180` (`backend/app/observations.py:45`) and it is used in exactly two places:

- **`observations.for_subjects()`** (`observations.py:222`) — the read the *agent* does. Drops dated lines older than the cutoff; **standing rules (`when is None`) are never filtered**, which is the entire reason the two line types share a file but not a lifetime (design D4).
- **`knowledge.observation_rows()`** (`knowledge.py:115`) — the read the *panel* does. Sets `stale: true` on the same lines but returns them, so the UI can render them as present-but-ignored.

`NoteRow` then mutes the text and adds a "đã cũ, bot không đọc nữa" chip (`frontend/src/components/chat/note-row.tsx`). That sentence is the current feature: the file keeps the line, the room can see it is inert, and a human can delete it in one tap if they want to.

`count_since(room_id, subject, *, since)` (`observations.py:237`) takes an explicit date and is **independent of the window**. It is currently defined and tested but has no caller in `app/` — it exists so Phoenix can one day say "lần thứ 3 tháng này" from a Python count instead of eyeballing it. Anything that deletes lines will silently under-count that the day it *is* wired up.

## Why leaving it alone is defensible

- **`since_days` is a parameter, not a constant of nature.** `for_subjects` takes it. Raise the window — or pass a different one for a different question — and "stale" lines come back to life. Flagging is reversible; deletion is not.
- **The file is small.** A busy room writes a handful of notes a month, one short line each. There is no size problem to solve yet; measure before assuming there is.
- **Notes are evidence about people and businesses.** "Làm quá chậm, 1 tiếng mới có món" from last year is weak evidence, but it is not *nothing* — a human deciding whether to go back may want to see three of them across two years. Only the model is supposed to stop reading them.
- **The blame problem is unsolved.** `TODO.md` still carries "no way to verify a false claim". Auto-deletion would quietly resolve false notes by expiry, which looks like a fix and isn't.

## The actual open question

Not "should we prune" but: **is there a state between "flagged" and "gone" that carries its weight?** Candidates, roughly in order of how much I'd trust them:

1. **Nothing.** Revisit when a real room's file is long enough to be a problem. Cheapest, and the current answer.
2. **A UI-only sweep.** A "dọn ghi nhớ cũ" affordance on the Ghi nhớ tab that selects the stale lines and deletes them *on one explicit human action*, showing exactly what will go. No timer, no automation — the human is the policy. This is the smallest thing that could be called a feature, and it reuses the existing `DELETE .../observations/{id}` route.
3. **Promote, don't prune.** The interesting case is a stale note that keeps recurring — three "hết gà" notes over two years is really a standing rule ("hay hết gà cuối tuần"). Offer "biến thành quy tắc" on a stale note: it flips `standing`, the line stops ageing, and the old dated copies become deletable. Turns a cleanup chore into a memory-quality feature.
4. **Archive rather than delete.** Move stale lines to `observations.archive.md`, out of the parse path but on disk. Halfway house: keeps the bytes, loses the visibility that makes them useful. Adds a second file and a second writer to reason about — pick this only if (1) genuinely stops scaling.
5. **A timer.** Explicitly rejected above. If a future session wants it, the burden is to explain what a room gains that (2) or (3) does not give them.

## Constraints on any of these

- **Writes are line-preserving and locked.** `observations.replace_line` / `delete_line` and `async with chat._agent_lock:`. Never rebuild the file from `load()` — that eats comments and unparsable lines (the K6 bug fixed in PR #46).
- **`etag` guards concurrency.** A bulk delete needs to either send one etag and refuse on drift, or be re-derived server-side; N independent `DELETE`s from the client will 409 each other, because every delete moves the file's fingerprint. **A bulk sweep is a new endpoint, not a loop over the existing one.** This is the main design constraint on option (2).
- **Standing rules must never be touched by any of this.** They have no date, so `stale` is structurally `false` for them — keep it that way rather than adding an "old rule" notion.
- **Each write posts a room message** (`_knowledge_trail`). A sweep of twelve notes must post one summary line, not twelve.

## Where to look first

- `backend/app/observations.py` — the window, the two line types, `count_since`.
- `backend/app/knowledge.py:100-130` — where `stale` is computed.
- `frontend/src/components/chat/note-row.tsx` — how it reads today.
- `docs/superpowers/plans/2026-08-14-knowledge-memory-ui.md` — the "Risks & non-goals" section states this position; if you change the behaviour, update that too.
- `docs/superpowers/specs/2026-08-14-lunch-suggestion-memory-design.md` — D4 (two line types, one file) is the constraint underneath all of it.

**Before writing any code:** look at a real room's `observations.md` on the droplet (the `deploy-chiatienan` skill covers the debug/export API) and count the lines and how many are stale. If the answer is "nine lines, two stale", the correct outcome of that session is a note saying so and no code.
