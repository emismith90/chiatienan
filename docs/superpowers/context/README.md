# Context briefs

Known follow-up work that has **not** been planned yet. One file per piece of work, each written so a fresh session can pick it up cold: what exists today with file anchors, every store involved, the constraints, the options with a recommendation, and the traps.

These are not plans. A session should read the brief, decide the scope with the operator, and then write a plan in `../plans/`.

| Brief | Came from |
|---|---|
| [`2026-08-14-place-slug-rename.md`](2026-08-14-place-slug-rename.md) | Knowledge UI (PR #46) left `slug` immutable on purpose. Renaming it needs a migration across five stores, two of which are not the database. |
| [`2026-08-14-stale-observations.md`](2026-08-14-stale-observations.md) | Same PR flags notes past 180 days and never prunes them. The brief argues for leaving it alone and says what would have to be true to change that. |
