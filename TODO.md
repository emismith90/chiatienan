# TODO

- **Generic draft card in the frontend — done (2026-09-06).** Any `*_draft` kind with no
  card of its own (a poker `game_draft`) renders through
  `frontend/src/components/chat/draft-card.tsx`: the kind as a title, the payload's
  fields as a summary (the kernel's `type`/`status`/`turn_id`/`raw_input`/`logged_by`
  hidden), and Confirm / Cancel on the generic draft routes. `use-room.ts` replaces the
  status flip in place for `kind.endsWith("_draft")`, which also fixes the memo card's
  buttons after Confirm. A list of objects is spelled out row by row (per-player buy-in
  and cash-out, each debt edge), because Confirm writes those numbers to the ledger and
  a person has to be able to check them. Remaining: a card designed for the poker draft —
  the field list carries no units (a money row and a count look alike) and no member
  names beyond what the payload itself holds.

- **Balance redesign** (short name, balance center alignment)

- **Blame feature.** Now chat/action run on trust, no way to verify. A false claim is not reversible/fixable → figure out a way to "blame" and exclude the transaction.

- **Alias** — multi alias, primary alias.

- **Bug when visualizing drafting payment.** Individual cost should account for number of guests.

- **Itemized split + guests.** `propose_meal(items=…)` currently refuses a meal that
  has both per-item shares and cash-paying guests, because guest heads are placeholder
  negative ids in `split_with_guests` while items only name members — prorating across
  both needs guest items too. Refused loudly rather than silently reweighting; wire it
  up when someone actually hits it.

- **Nicer loading** at load for the chat — currently flickers and scrolls.

- **Randomizer** — randomly pick 1 person from the group (e.g. who pays / who fetches lunch).

- **Agent engine export/import — done (Agent OS Phase 9).** A published profile exports as a
  Pi package (`GET /api/admin/profiles/{id}/export`: `skills/<slug>/SKILL.md`, `prompts/*.md`,
  `AGENTS.md`, `.pi/settings.json`, `kernos.json`) and imports into a business as sources plus a
  draft that the publish gates review (`POST /api/admin/businesses/{id}/import[?replace=true]`).
  Remaining: a UI for upload (the admin API is the only surface), and the split of `kernos`
  into its own repository when a second real host exists (design §12.1).
