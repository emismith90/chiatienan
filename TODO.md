# TODO

- **Generic draft card in the frontend (Phase 6 follow-up).** The backend now commits any
  registered draft kind through `POST /drafts/{id}/commit` (a poker `game_draft`, for
  one), but `message-list.tsx` renders unknown kinds as a blank human bubble and
  `use-room.ts` only replaces status flips for the two lunch kinds. Add a generic
  `DraftCard` fallback for any `*_draft` kind (body + attachment summary + Confirm/Cancel)
  and make the in-place replacement apply to `kind.endsWith("_draft")`.

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
