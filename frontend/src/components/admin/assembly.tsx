/** Assembling a draft from the catalogue (plan Phase 13.3).
 *
 * Everything here is one `PATCH /profiles/{id}/versions/{v}` of a partial spec, so the
 * five publish gates stay the only thing that decides whether it may go live. The form
 * adds no authority; it makes the authority reachable.
 *
 * Three rules the store's behaviour forces on this form:
 *
 * 1. **Lists are always sent complete.** `deep_merge` merges dicts and *replaces* lists,
 *    so a partial `tool_packs` would silently drop every pack it omitted. Each control
 *    rebuilds the whole array, existing per-tool overrides included.
 * 2. **Per-tool overrides only for a pack whose tool set is static.** `apply_tool_overrides`
 *    refuses an override naming a tool the pack does not have *on this turn*, and for a
 *    dynamic pack that set is smaller than the one gate 1 validates against: an override
 *    for `cms_publish` passes the gate and then breaks every turn of an agent whose
 *    capabilities grant only `read`. So a dynamic pack can be enabled or not, and that is
 *    all.
 * 3. **A framework-managed pack is never offered.** The kernel adds it when the turn needs
 *    it; a profile that also lists it makes `compose_tools` raise on two sources of one
 *    tool name, which gate 1 does not catch.
 */
"use client";
import { useState } from "react";
import * as admin from "@/lib/admin-api";
import { Badge, Field, Notice, Section, box, btn } from "./ui";

type Patch = Record<string, any>;

const probeOf = (m: admin.Model) => (m.probe?.ok ? "probed" : "no passing probe");

export function Assembly({
  spec,
  cat,
  models,
  disabled,
  onPatch,
}: {
  spec: any;
  cat: admin.Catalogue;
  models: admin.Model[];
  disabled: boolean;
  onPatch: (patch: Patch) => void;
}) {
  const [openPack, setOpenPack] = useState<string | null>(null);

  const refs: any[] = spec?.tool_packs ?? [];
  const builtins: string[] = spec?.builtin_tools ?? [];
  const offered = cat.packs.filter((p) => !p.framework_managed);
  const enabled = new Set(refs.map((r) => r.pack));
  const moneyProfile =
    Boolean(spec?.meta?.handles_money) ||
    offered.some((p) => enabled.has(p.id) && p.handles_money);
  const risky = builtins.filter((b) => cat.risky_builtin_tools.includes(b));

  function togglePack(id: string, on: boolean) {
    onPatch({
      tool_packs: on
        ? [...refs, { pack: id, tools: {} }]
        : refs.filter((r) => r.pack !== id),
    });
  }

  /** One tool's override, with every other pack and override carried through untouched. */
  function setOverride(packId: string, tool: string, patch: { enabled?: boolean; description?: string }) {
    onPatch({
      tool_packs: refs.map((r) => {
        if (r.pack !== packId) return r;
        const tools = { ...(r.tools ?? {}) };
        const next = { ...(tools[tool] ?? {}), ...patch };
        // an override equal to the default is noise in the diff; drop it
        if (next.enabled !== false && !next.description) delete tools[tool];
        else tools[tool] = next;
        return { ...r, tools };
      }),
    });
  }

  return (
    <div className="space-y-4">
      <Section title="Packs" hint="The tools this agent has. A pack is code; which ones are on is content.">
        <ul className="divide-y divide-[var(--border)] rounded-lg border border-[var(--border)]">
          {offered.map((pack) => {
            const on = enabled.has(pack.id);
            const ref = refs.find((r) => r.pack === pack.id);
            return (
              <li key={pack.id} className="px-2 py-1.5">
                <div className="flex flex-wrap items-center gap-2">
                  <label className="flex items-center gap-2 text-xs text-[var(--text)]">
                    <input
                      type="checkbox"
                      checked={on}
                      disabled={disabled}
                      aria-label={pack.id}
                      onChange={(e) => togglePack(pack.id, e.target.checked)}
                    />
                    {pack.id}
                  </label>
                  <span className="text-[11px] text-[var(--text-secondary)]">v{pack.version}</span>
                  {pack.handles_money ? <Badge tone="warn">handles money</Badge> : null}
                  {pack.dynamic ? <Badge>per turn</Badge> : null}
                  {on && !pack.dynamic ? (
                    <button
                      className={`${btn} ml-auto`}
                      onClick={() => setOpenPack(openPack === pack.id ? null : pack.id)}
                      aria-expanded={openPack === pack.id}
                    >
                      {pack.tools.length} tools
                    </button>
                  ) : null}
                </div>

                {on && pack.dynamic ? (
                  <p className="pt-1 text-[11px] text-[var(--text-secondary)]">
                    Which tools a turn gets depends on the agent, so they cannot be overridden
                    one by one here — an override for a tool the turn does not have breaks it.
                  </p>
                ) : null}

                {openPack === pack.id && on && !pack.dynamic ? (
                  <ul className="mt-1 space-y-1 rounded-lg border border-[var(--border)] p-1.5">
                    {pack.tools.map((tool) => {
                      const ov = (ref?.tools ?? {})[tool.name] ?? {};
                      return (
                        <li key={tool.name} className="space-y-1">
                          <label className="flex items-center gap-2 text-xs text-[var(--text)]">
                            <input
                              type="checkbox"
                              checked={ov.enabled !== false}
                              disabled={disabled}
                              aria-label={`${pack.id}.${tool.name}`}
                              onChange={(e) => setOverride(pack.id, tool.name, { enabled: e.target.checked })}
                            />
                            {tool.name}
                            {tool.money ? <Badge tone="warn">money</Badge> : null}
                          </label>
                          <input
                            className={`${box} text-[11px]`}
                            placeholder={tool.description}
                            value={ov.description ?? ""}
                            disabled={disabled}
                            aria-label={`${pack.id}.${tool.name} description`}
                            onChange={(e) => setOverride(pack.id, tool.name, { description: e.target.value })}
                          />
                        </li>
                      );
                    })}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
      </Section>

      <Section
        title="Builtin tools"
        hint="The harness's own tools. Empty is what makes money safety structural rather than a request in the prompt."
      >
        <div className="flex flex-wrap gap-3">
          {cat.builtin_tools.map((name) => (
            <label key={name} className="flex items-center gap-1.5 text-xs text-[var(--text)]">
              <input
                type="checkbox"
                checked={builtins.includes(name)}
                disabled={disabled}
                aria-label={`builtin ${name}`}
                onChange={(e) =>
                  onPatch({
                    builtin_tools: e.target.checked
                      ? [...builtins, name]
                      : builtins.filter((b) => b !== name),
                  })
                }
              />
              {name}
              {cat.risky_builtin_tools.includes(name) ? <Badge tone="warn">risky</Badge> : null}
            </label>
          ))}
        </div>
        {moneyProfile && risky.length ? (
          <Notice tone="warn">
            This profile handles money and enables {risky.join(", ")}, so gate 2 will ask for an
            override reason below. That is already true of the seeded bot — with <code>bash</code> the
            model <em>can</em> do arithmetic, and the reply validators are then the only thing
            catching a number no tool produced.
          </Notice>
        ) : null}
      </Section>

      <Section title="Model and caps" hint="Gate 3 refuses a model with no recent passing probe.">
        <div className="flex flex-wrap gap-2">
          <Field label="Text model">
            <select
              className={`${box} max-w-[16rem]`}
              value={spec?.models?.text ?? ""}
              disabled={disabled}
              aria-label="text model"
              onChange={(e) => onPatch({ models: { text: e.target.value } })}
            >
              {models.map((m) => (
                <option key={m.model_id} value={m.model_id}>
                  {m.model_id} — {probeOf(m)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Vision model">
            <select
              className={`${box} max-w-[16rem]`}
              value={spec?.models?.vision ?? ""}
              disabled={disabled}
              aria-label="vision model"
              onChange={(e) => onPatch({ models: { vision: e.target.value || null } })}
            >
              <option value="">none — a turn with a photo fails loudly</option>
              {models.map((m) => (
                <option key={m.model_id} value={m.model_id}>
                  {m.model_id} — {probeOf(m)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Max tools">
            <input
              className={`${box} max-w-[7rem]`}
              type="number"
              min={1}
              value={spec?.caps?.max_tools ?? 40}
              disabled={disabled}
              aria-label="max tools"
              onChange={(e) => onPatch({ caps: { max_tools: Number(e.target.value) } })}
            />
          </Field>
          <Field label="Max seconds">
            <input
              className={`${box} max-w-[7rem]`}
              type="number"
              min={1}
              value={spec?.caps?.max_seconds ?? 120}
              disabled={disabled}
              aria-label="max seconds"
              onChange={(e) => onPatch({ caps: { max_seconds: Number(e.target.value) } })}
            />
          </Field>
        </div>
        {models.length <= 1 ? (
          <p className="text-[11px] text-[var(--text-secondary)]">
            Only the models boot seeded from the environment are in the catalogue — there is no
            route that adds one, and the probe runs in dev. Changing the model is still a
            deployment decision.
          </p>
        ) : null}
      </Section>
    </div>
  );
}

/** Creating and deleting a source, which the Content tab could only edit (Phase 13.3). */
export function NewSource({
  kinds,
  disabled,
  onCreate,
}: {
  kinds: string[];
  disabled: boolean;
  onCreate: (kind: string, slug: string) => void;
}) {
  const [kind, setKind] = useState("skill");
  const [slug, setSlug] = useState("");
  // the shape `kernos.content.store.SOURCE_SLUG_RE` enforces: it becomes a
  // `/virtual/<slug>` context file the engine reads, not just a row key
  const ok = /^[a-z0-9][a-z0-9._-]{0,79}$/.test(slug);
  const pinned = kind === "prompt";

  return (
    <div className="flex flex-wrap items-end gap-2 rounded-lg border border-[var(--border)] p-2">
      <Field label="Kind">
        <select
          className={`${box} max-w-[9rem]`}
          value={kind}
          aria-label="new source kind"
          onChange={(e) => {
            setKind(e.target.value);
            if (e.target.value === "prompt") setSlug("system");
          }}
        >
          {kinds.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Slug" hint="lowercase, digits, dot, underscore, hyphen">
        <input
          className={`${box} max-w-[14rem]`}
          value={slug}
          disabled={pinned}
          aria-label="new source slug"
          onChange={(e) => setSlug(e.target.value)}
        />
      </Field>
      <button className={btn} disabled={disabled || !ok} onClick={() => onCreate(kind, slug)}>
        Add source
      </button>
      {slug && !ok ? (
        <p className="text-[11px] text-amber-300">
          A slug is lowercase letters, digits, <code>.</code> <code>_</code> or <code>-</code>.
        </p>
      ) : null}
      {pinned ? (
        <p className="text-[11px] text-[var(--text-secondary)]">
          Only <code>prompt/system</code> is read — a prompt source under any other slug is ignored.
        </p>
      ) : null}
      {kind === "template" ? (
        <p className="text-[11px] text-[var(--text-secondary)]">
          Templates are stored and exported, but nothing reads them during a turn yet.
        </p>
      ) : null}
    </div>
  );
}
