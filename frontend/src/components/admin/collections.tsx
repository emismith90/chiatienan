/** Collections: the one component a person creates in the CMS (plan Phase 13.4).
 *
 * Everything else on this screen chooses between things the code registers. A collection
 * is different: its JSON Schema *generates* tools — `<slug>_find`, `<slug>_upsert`,
 * `<slug>_delete`, `<slug>_search`; for an append-only journal `<slug>_find`, `<slug>_append`,
 * `<slug>_search` — for every profile that enables the `collections` pack. That is the
 * honest answer to "can the CMS add a tool": not by authoring code, but by declaring a
 * document type that a pack in code knows how to serve.
 *
 * It is also the only save on this screen that reaches a live agent **immediately** —
 * `CollectionsPack.tools` reads the definitions per turn, so there is no draft, no
 * publish, no gate and no probe between this form and what the model is handed next. The
 * form says so above the button rather than in a doc.
 *
 * The schema must stay inside the sidecar-safe subset (`kernos/data/schema.py`): the Pi
 * sidecar converts tool schemas to TypeBox with six keywords and throws on anything else
 * *while converting the whole manifest*, so one bad definition would take down every tool
 * of the business. The server checks it and its error already names the path and the
 * keyword, so it is shown as it comes.
 */
"use client";
import { useCallback, useEffect, useState } from "react";
import * as admin from "@/lib/admin-api";
import { Badge, Field, Notice, Section, box, btn, btnPrimary, message, when } from "./ui";

const BLANK = {
  slug: "",
  name: "",
  description: "",
  key: "",
  mode: "table" as admin.Collection["mode"],
  indexed: "",
  searchable: "",
  schema: JSON.stringify(
    { type: "object", properties: { name: { type: "string", description: "the key" } }, required: ["name"] },
    null,
    1,
  ),
};

export function Collections({ businessId }: { businessId: number | null }) {
  const [rows, setRows] = useState<admin.Collection[]>([]);
  const [form, setForm] = useState({ ...BLANK });
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (id: number) => {
    setRows(await admin.collections(id));
  }, []);

  useEffect(() => {
    setOpen(null);
    setForm({ ...BLANK });
    if (businessId === null) return;
    load(businessId).catch((e) => setError(message(e)));
  }, [businessId, load]);

  async function run(what: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await what();
    } catch (e) {
      setError(message(e));
      return false;
    } finally {
      setBusy(false);
    }
    return true;
  }

  function edit(row: admin.Collection) {
    setOpen(row.slug);
    setForm({
      slug: row.slug,
      name: row.name,
      description: row.description,
      key: row.key,
      mode: row.mode ?? "table",
      indexed: row.indexed.join(", "),
      searchable: (row.searchable ?? []).join(", "),
      schema: JSON.stringify(row.schema, null, 1),
    });
  }

  async function save() {
    if (businessId === null) return;
    let schema: any;
    try {
      schema = JSON.parse(form.schema);
    } catch {
      setError("The schema is not valid JSON.");
      return;
    }
    const names = generated(form.slug, form.mode).join(", ");
    const journal = form.mode === "journal";
    const searchable = list(form.searchable);
    if (
      !window.confirm(
        `Save ${form.slug}?\n\n` +
          `Every agent of this business that has the collections pack gets ${names} on its ` +
          "next turn — there is no draft and no publish between this and the live bot.",
      )
    )
      return;
    const done = await run(async () => {
      await admin.putCollection(businessId, form.slug, {
        name: form.name || form.slug,
        schema,
        key: journal ? "" : form.key,
        mode: form.mode,
        indexed: list(form.indexed),
        searchable: searchable.length ? searchable : null,
        description: form.description,
      });
      await load(businessId);
    });
    if (done) {
      setOpen(null);
      setForm({ ...BLANK });
    }
  }

  async function remove(row: admin.Collection) {
    if (businessId === null) return;
    if (!window.confirm(`Delete the ${row.slug} definition, and the tools it generates?`)) return;
    const done = await run(async () => {
      await admin.deleteCollection(businessId, row.slug);
      await load(businessId);
    });
    if (done) setOpen(null);
  }

  return (
    <Section
      title="Collections"
      hint="A document type defined here, not in code. Its schema generates the tools to find, write and search it."
    >
      {error ? <Notice tone="error">{error}</Notice> : null}

      <ul className="divide-y divide-[var(--border)] rounded-lg border border-[var(--border)]">
        {rows.map((row) => (
          <li key={row.slug} className="px-2 py-1.5">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <button className="text-[var(--text)] hover:underline" onClick={() => edit(row)}>
                {row.slug}
              </button>
              <span className="text-[var(--text-secondary)]">{row.name}</span>
              <span className="text-[11px] text-[var(--text-secondary)]">
                {row.mode === "journal" ? "journal (append-only)" : `key: ${row.key}`}
              </span>
              <span className="ml-auto text-[11px] text-[var(--text-secondary)]">{when(row.updated_at)}</span>
            </div>
            <div className="flex flex-wrap gap-1 pt-1">
              {generated(row.slug, row.mode).map((n) => (
                <Badge key={n}>{n}</Badge>
              ))}
            </div>
          </li>
        ))}
        {rows.length === 0 ? (
          <li className="px-2 py-1.5 text-xs text-[var(--text-secondary)]">
            No collections. An agent of this business has no generated tools.
          </li>
        ) : null}
      </ul>

      <div className="space-y-2 rounded-lg border border-[var(--border)] p-2">
        <p className="text-xs text-[var(--text-secondary)]">
          {open ? `Editing ${open}` : "New collection"}
        </p>
        <div className="flex flex-wrap gap-2">
          <Field label="Slug" hint="lowercase; `<slug>_upsert` must stay under 64 characters">
            <input
              className={`${box} max-w-[12rem]`}
              value={form.slug}
              disabled={open !== null}
              aria-label="collection slug"
              onChange={(e) => setForm({ ...form, slug: e.target.value })}
            />
          </Field>
          <Field label="Name">
            <input
              className={`${box} max-w-[12rem]`}
              value={form.name}
              aria-label="collection name"
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </Field>
          <Field label="Mode" hint="a journal's entries are numbered by the server and never changed or deleted">
            <select
              className={`${box} max-w-[10rem]`}
              value={form.mode}
              disabled={open !== null}
              aria-label="collection mode"
              onChange={(e) => setForm({ ...form, mode: e.target.value as admin.Collection["mode"] })}
            >
              <option value="table">table</option>
              <option value="journal">journal</option>
            </select>
          </Field>
          <Field label="Key" hint="the required string property that identifies a document">
            <input
              className={`${box} max-w-[10rem]`}
              value={form.mode === "journal" ? "" : form.key}
              disabled={form.mode === "journal"}
              aria-label="collection key"
              onChange={(e) => setForm({ ...form, key: e.target.value })}
            />
          </Field>
          <Field label="Indexed" hint="comma-separated; what find may filter by">
            <input
              className={`${box} max-w-[12rem]`}
              value={form.indexed}
              aria-label="collection indexed"
              onChange={(e) => setForm({ ...form, indexed: e.target.value })}
            />
          </Field>
          <Field label="Searchable" hint="comma-separated string fields search reads; empty = all of them">
            <input
              className={`${box} max-w-[12rem]`}
              value={form.searchable}
              aria-label="collection searchable"
              onChange={(e) => setForm({ ...form, searchable: e.target.value })}
            />
          </Field>
        </div>
        <Field label="Description" hint="The model reads this in the tool description.">
          <input
            className={box}
            value={form.description}
            aria-label="collection description"
            onChange={(e) => setForm({ ...form, description: e.target.value })}
          />
        </Field>
        <Field
          label="Schema"
          hint="JSON, and only type / properties / required / items / description / enum — the sidecar converts nothing else."
        >
          <textarea
            className={`${box} h-40 font-mono`}
            value={form.schema}
            aria-label="collection schema"
            onChange={(e) => setForm({ ...form, schema: e.target.value })}
          />
        </Field>
        <Notice tone="warn">
          Saving changes the tools of every agent in this business on its next turn. There is no
          draft and no publish gate between this form and the live bot.
        </Notice>
        <div className="flex gap-2">
          <button
            className={btnPrimary}
            disabled={busy || businessId === null || !form.slug || (form.mode !== "journal" && !form.key)}
            onClick={() => void save()}
          >
            Save collection
          </button>
          {open ? (
            <>
              <button
                className={btn}
                disabled={busy}
                onClick={() => void remove(rows.find((r) => r.slug === open)!)}
              >
                Delete
              </button>
              <button
                className={btn}
                onClick={() => {
                  setOpen(null);
                  setForm({ ...BLANK });
                }}
              >
                Cancel
              </button>
            </>
          ) : null}
        </div>
      </div>
    </Section>
  );
}

/** What `kernos.data.store.generated_tool_names` will build from a slug. */
function generated(slug: string, mode: admin.Collection["mode"] = "table"): string[] {
  if (!slug) return [];
  return mode === "journal"
    ? [`${slug}_find`, `${slug}_append`, `${slug}_search`]
    : [`${slug}_find`, `${slug}_upsert`, `${slug}_delete`, `${slug}_search`];
}

function list(csv: string): string[] {
  return csv.split(",").map((s) => s.trim()).filter(Boolean);
}
