/** What an agent is made of (plan Phase 13.2).
 *
 * The question the other four tabs could not answer. Three sections, in the order an
 * operator asks them:
 *
 *  1. **Tools** — every pack the code registers, with its tools, and for the space you
 *     pick, which of them that agent's profile turned on.
 *  2. **Pipeline and plugins** — the stages that run, and what the registry offers.
 *  3. **Prompt** — the system prompt, rules and skills the space actually resolves to,
 *     each traced back to the `kn_sources` row it came from.
 *  4. **Collections** — the one component a person creates *here* rather than choosing
 *     from code, because its schema generates tools (plan Phase 13.4).
 *
 * Two honesty rules run through it, both from the review of the plan:
 *
 * - **"As configured", not "on".** `/spaces/{id}/resolved` returns the spec, not a tool
 *   manifest. For a pack whose tools depend on the turn — `os_admin` (the agent's
 *   capability verbs), `delegation` (its `delegates_to`), `collections` (the business's
 *   definitions) — the profile genuinely cannot say what the model will be handed, so
 *   those are labelled *per turn* instead of badged on or off.
 * - **Provenance can be stale.** A version is a snapshot; the source row behind it may
 *   have been edited since. Where the bodies differ the label says so, because a
 *   provenance line that silently shows today's source next to last week's snapshot is
 *   worse than none.
 */
"use client";
import { useCallback, useEffect, useState } from "react";
import * as admin from "@/lib/admin-api";
import { Collections } from "./collections";
import type { Live } from "./overview";
import { Badge, Field, Notice, Pre, Section, box, btn, message, when } from "./ui";

type Resolved = { spec: any; pipeline: any; resolution: any };

/** What a profile says about one pack's tools: `{[tool]: {enabled?, description?}}`. */
function overridesFor(spec: any, packId: string): Record<string, any> | null {
  const ref = (spec?.tool_packs ?? []).find((t: any) => t.pack === packId);
  return ref ? (ref.tools ?? {}) : null;
}

function ToolRow({ tool, over }: { tool: admin.CatalogueTool; over: Record<string, any> | null }) {
  const [open, setOpen] = useState(false);
  const ov = over?.[tool.name];
  const off = ov?.enabled === false;
  const renamed = typeof ov?.description === "string" && ov.description.length > 0;
  return (
    <li className="border-t border-[var(--border)] first:border-t-0">
      <button
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-xs hover:bg-[var(--surface-2)]"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className={off ? "text-[var(--text-secondary)] line-through" : "text-[var(--text)]"}>
          {tool.name}
        </span>
        {tool.money ? <Badge tone="warn">money</Badge> : null}
        {over === null ? null : off ? <Badge>off</Badge> : <Badge tone="live">on</Badge>}
        {renamed ? <Badge tone="warn">description overridden</Badge> : null}
        <span className="ml-auto truncate text-[11px] text-[var(--text-secondary)]">
          {(ov?.description || tool.description).slice(0, 80)}
        </span>
      </button>
      {open ? (
        <div className="space-y-1 px-2 pb-2">
          <p className="text-[11px] leading-relaxed text-[var(--text-secondary)]">
            {ov?.description || tool.description}
          </p>
          <Pre>{JSON.stringify(tool.schema, null, 1)}</Pre>
        </div>
      ) : null}
    </li>
  );
}

function Packs({ cat, resolved }: { cat: admin.Catalogue; resolved: Resolved | null }) {
  const spec = resolved?.spec ?? null;
  return (
    <Section
      title="Packs and tools"
      hint="Every pack the code registers. Pick a space above to see what its profile turned on."
    >
      <div className="space-y-2">
        {cat.packs.map((pack) => {
          const over = spec ? overridesFor(spec, pack.id) : null;
          return (
            <div key={pack.id} className="rounded-lg border border-[var(--border)]">
              <div className="flex flex-wrap items-center gap-2 px-2 py-1.5">
                <span className="text-xs font-medium text-[var(--text)]">{pack.id}</span>
                <span className="text-[11px] text-[var(--text-secondary)]">v{pack.version}</span>
                {pack.handles_money ? <Badge tone="warn">handles money</Badge> : null}
                {pack.framework_managed ? <Badge>framework</Badge> : null}
                {pack.dynamic ? <Badge>per turn</Badge> : null}
                {spec && !pack.dynamic ? (
                  over ? <Badge tone="live">enabled</Badge> : <Badge>not in this profile</Badge>
                ) : null}
                <span className="ml-auto text-[11px] text-[var(--text-secondary)]">
                  {pack.tools.length || pack.tool_names.length} tools
                </span>
              </div>
              {pack.error ? (
                <div className="px-2 pb-2">
                  <Notice tone="warn">This pack could not be described: {pack.error}</Notice>
                </div>
              ) : null}
              {pack.dynamic ? (
                <p className="px-2 pb-1.5 text-[11px] text-[var(--text-secondary)]">
                  {pack.framework_managed
                    ? "The kernel adds this pack itself when a turn needs it — a profile must not list it."
                    : "Which of these a turn gets is decided per turn, not by the profile."}
                </p>
              ) : null}
              <ul>
                {pack.tools.map((t) => (
                  <ToolRow key={t.name} tool={t} over={pack.dynamic ? null : over} />
                ))}
                {pack.tools.length === 0 ? (
                  <li className="border-t border-[var(--border)] px-2 py-1.5 text-[11px] text-[var(--text-secondary)]">
                    {pack.tool_names.length
                      ? `Named, but only describable during a turn: ${pack.tool_names.join(", ")}`
                      : "Its tools are built from the database during a turn."}
                  </li>
                ) : null}
              </ul>
            </div>
          );
        })}
      </div>
    </Section>
  );
}

function Pipeline({ plugins, resolved }: { plugins: admin.Plugin[]; resolved: Resolved | null }) {
  const [open, setOpen] = useState<string | null>(null);
  const running: any[] = resolved?.pipeline ?? [];
  return (
    <Section title="Pipeline and plugins" hint="The stages this space runs, then everything the registry offers.">
      {running.length ? (
        <ol className="divide-y divide-[var(--border)] rounded-lg border border-[var(--border)]">
          {running.map((entry, i) => (
            <li key={`${entry.stage}/${entry.plugin}/${i}`} className="flex items-center gap-2 px-2 py-1 text-xs">
              <Badge>{entry.stage}</Badge>
              <span className="text-[var(--text)]">{entry.plugin}</span>
              <span className="text-[11px] text-[var(--text-secondary)]">v{entry.version}</span>
              {Object.keys(entry.config ?? {}).length ? (
                <span className="ml-auto truncate text-[11px] text-[var(--text-secondary)]">
                  {JSON.stringify(entry.config)}
                </span>
              ) : null}
            </li>
          ))}
        </ol>
      ) : (
        <p className="text-xs text-[var(--text-secondary)]">Pick a space to see its pipeline.</p>
      )}

      <ul className="divide-y divide-[var(--border)] rounded-lg border border-[var(--border)]">
        {plugins.map((p) => {
          const key = `${p.id}@${p.version}`;
          return (
            <li key={key}>
              <button
                className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-xs hover:bg-[var(--surface-2)]"
                onClick={() => setOpen(open === key ? null : key)}
                aria-expanded={open === key}
              >
                <Badge>{p.stage}</Badge>
                <span className="text-[var(--text)]">{p.id}</span>
                <span className="text-[11px] text-[var(--text-secondary)]">v{p.version}</span>
                {p.handles_money ? <Badge tone="warn">money</Badge> : null}
              </button>
              {open === key ? (
                <div className="px-2 pb-2">
                  <Pre>{JSON.stringify(p.config_schema, null, 1)}</Pre>
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

/** The source row behind one resolved item, and whether it has moved on since. */
function provenance(sources: admin.Source[], kind: string, slug: string, body: string) {
  const src = sources.find((s) => s.kind === kind && s.slug === slug);
  if (!src) return { label: "no source — spec only", stale: false, src: null };
  return { label: `${src.kind}/${src.slug} · ${src.updated_by} · ${when(src.updated_at)}`,
           stale: src.body !== body, src };
}

function Prompt({ resolved, sources }: { resolved: Resolved | null; sources: admin.Source[] }) {
  if (!resolved) {
    return (
      <Section title="Prompt" hint="What this space's agent is actually told.">
        <p className="text-xs text-[var(--text-secondary)]">Pick a space to see its prompt.</p>
      </Section>
    );
  }
  const spec = resolved.spec ?? {};
  const rows: { heading: string; kind: string; slug: string; body: string }[] = [
    { heading: "system prompt", kind: "prompt", slug: "system", body: spec.prompt?.body ?? "" },
    ...(spec.rules ?? []).map((r: any) => ({ heading: `rule · ${r.slug}`, kind: "rule", slug: r.slug, body: r.content })),
    ...(spec.skills ?? []).map((s: any) => ({ heading: `skill · ${s.name}`, kind: "skill", slug: s.name, body: s.body })),
  ];
  return (
    <Section
      title="Prompt"
      hint="The published snapshot this space runs, and the source row each part came from."
    >
      <ul className="divide-y divide-[var(--border)] rounded-lg border border-[var(--border)]">
        {rows.map((row) => {
          const p = provenance(sources, row.kind, row.slug, row.body);
          return (
            <li key={`${row.kind}/${row.slug}`} className="space-y-1 px-2 py-1.5">
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="text-[var(--text)]">{row.heading}</span>
                <span className="text-[11px] text-[var(--text-secondary)]">{p.label}</span>
                {p.stale ? <Badge tone="warn">source changed since this version</Badge> : null}
              </div>
              <Pre>{row.body || "(empty)"}</Pre>
            </li>
          );
        })}
      </ul>
      {(spec.templates ?? []).length ? (
        <Notice tone="info">
          {spec.templates.length} prompt template(s) are stored on this profile. Nothing reads them
          during a turn yet — they travel with a package export only.
        </Notice>
      ) : null}
    </Section>
  );
}

export function Components({ live }: { live: Live }) {
  const [cat, setCat] = useState<admin.Catalogue | null>(null);
  const [plugins, setPlugins] = useState<admin.Plugin[]>([]);
  const [space, setSpace] = useState("");
  const [resolved, setResolved] = useState<Resolved | null>(null);
  const [sources, setSources] = useState<admin.Source[]>([]);
  const [businessId, setBusinessId] = useState<number | null>(live.businesses[0]?.id ?? null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([admin.catalogue(), admin.registry()])
      .then(([c, p]) => {
        setCat(c);
        setPlugins(p);
      })
      .catch((e) => setError(message(e)));
  }, []);

  const look = useCallback(async () => {
    const id = space.trim();
    if (!id) return;
    setError(null);
    try {
      const r = await admin.resolved(id);
      setResolved(r as Resolved);
      const bid = (r as any).resolution?.agent?.business_id ?? null;
      if (bid) setBusinessId(bid);
      setSources(bid ? await admin.sources(bid) : []);
    } catch (e) {
      setResolved(null);
      setSources([]);
      setError(message(e));
    }
  }, [space]);

  return (
    <div className="space-y-6">
      {error ? <Notice tone="error">{error}</Notice> : null}

      <div className="flex flex-wrap items-end gap-2">
        <Field label="Space id" hint="A room id. What it resolves to decides everything below.">
          <input
            className={`${box} max-w-[12rem]`}
            value={space}
            onChange={(e) => setSpace(e.target.value)}
            aria-label="space id"
          />
        </Field>
        <button className={btn} disabled={!space.trim()} onClick={() => void look()}>
          Look up
        </button>
        {resolved?.resolution?.agent ? (
          <p className="text-[11px] text-[var(--text-secondary)]">
            {resolved.resolution.agent.slug} · profile #{resolved.resolution.profile_id} · v
            {resolved.resolution.version_id} · {resolved.resolution.bound ? "bound" : "default agent"}
          </p>
        ) : null}
      </div>

      {live.profiles.some((p) => p.managed_by === "boot") ? (
        <Notice tone="info">
          Some profiles still track code: a deploy republishes them <strong>from code</strong>, so a
          source edited here reaches the bot only through a draft you publish.
        </Notice>
      ) : null}

      {cat === null ? (
        <p className="text-sm text-[var(--text-secondary)]">Loading the catalogue…</p>
      ) : (
        <>
          <Packs cat={cat} resolved={resolved} />
          <Pipeline plugins={plugins} resolved={resolved} />
          <Prompt resolved={resolved} sources={sources} />

          <div className="flex flex-wrap items-end gap-2">
            <Field label="Business" hint="Whose collections, below. A space you look up selects its own.">
              <select
                className={`${box} max-w-[16rem]`}
                value={businessId ?? ""}
                aria-label="collections business"
                onChange={(e) => setBusinessId(Number(e.target.value))}
              >
                {live.businesses.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.slug} — {b.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Collections businessId={businessId} />
        </>
      )}
    </div>
  );
}
