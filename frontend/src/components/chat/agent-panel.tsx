"use client";
import { useCallback, useEffect, useState } from "react";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";

/**
 * The CMS of the agent this room runs (plan Phase 11).
 *
 * Every member can read all of it. Writing is gated on `can_edit`, which the
 * backend derives from the room's *binding* and not from who is asking — an
 * unbound room falls back to the shared default bot, and anyone can create a
 * room, so membership is not a permission (review F1). When `can_edit` is false
 * this is a reader with an explanation, not a disabled form with a mystery.
 *
 * Two things are shown that a config editor usually hides, because a person
 * cannot consent to what they have not been told:
 *  - `shared`: confirming here changes the bot in every room that has no agent
 *    of its own.
 *  - `managed_by === "human"`: someone has edited this profile, so it no longer
 *    picks up prompt/skill changes from a deploy (review F8).
 */

type Rule = { slug: string; content: string; tags: string[]; editable: boolean };
type Skill = { name: string; description: string; body: string };
type View = {
  agent: { slug: string; name: string; persona: Record<string, any> };
  profile: { id: number; name: string; business: string; managed_by: string };
  version: { id: number; version: number; actor: string; note: string | null; published_at: string | null } | null;
  editable: { prompt_body: string; prompt_append: string[]; skills: Skill[]; rules: Rule[] };
  readonly: {
    models: Record<string, any>;
    caps: Record<string, any>;
    builtin_tools: string[];
    tool_packs: string[];
    pipeline_stages: string[];
  };
  source_etags: Record<string, string>;
  can_edit: boolean;
  shared: boolean;
  scope: string[];
};
type Revision = {
  id: number; version: number; status: string; actor: string;
  note: string | null; created_at: string; published_at: string | null; paths: string[];
};

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");
/** "member:4" is the actor form the backend writes; show the number, not the prefix. */
const who = (actor: string) => (actor.startsWith("member:") ? `member ${actor.slice(7)}` : actor);

function Notice({ tone, children }: { tone: "warn" | "info"; children: React.ReactNode }) {
  const cls =
    tone === "warn"
      ? "border-amber-500/40 bg-amber-500/10 text-amber-200"
      : "border-[var(--border)] bg-[var(--surface-2)] text-[var(--text-secondary)]";
  return <p className={`rounded-lg border px-3 py-2 text-xs leading-relaxed ${cls}`}>{children}</p>;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium text-[var(--text-secondary)]">{label}</span>
      {hint ? <span className="block text-[11px] text-[var(--text-secondary)]">{hint}</span> : null}
      {children}
    </label>
  );
}

const boxCls =
  "w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm " +
  "text-[var(--text)] disabled:opacity-60";

export function AgentPanel({
  roomId, active, version, onSaved,
}: {
  roomId: number;
  active: boolean;
  /** bumped by `agent:changed` so a publish elsewhere refetches here too */
  version: number;
  onSaved: () => void;
}) {
  const [view, setView] = useState<View | null>(null);
  const [log, setLog] = useState<Revision[]>([]);
  const [draft, setDraft] = useState<View["editable"] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [diff, setDiff] = useState<string>("");

  const load = useCallback(async () => {
    try {
      const [v, l] = await Promise.all([api.roomAgent(roomId), api.roomAgentVersions(roomId)]);
      setView(v);
      setDraft(v.editable);
      setLog(l);
      setError(null);
    } catch (e: any) {
      setError(e?.message ?? "could not load");
    }
  }, [roomId]);

  useEffect(() => {
    if (active) void load();
  }, [active, version, load]);

  if (!active) return null;
  if (error && !view) return <p className="p-3 text-sm text-[var(--text-secondary)]">{error}</p>;
  if (!view || !draft) return <p className="p-3 text-sm text-[var(--text-secondary)]">Loading…</p>;

  const ro = view.readonly;
  const locked = !view.can_edit;

  async function save() {
    if (!view || !draft) return;
    setBusy(true);
    setError(null);
    try {
      await api.saveRoomAgent(roomId, {
        base_version_id: view.version?.id ?? null,
        note: note.trim() || null,
        prompt_body: draft.prompt_body,
        prompt_append: draft.prompt_append,
        skills: draft.skills.map((s) => ({ name: s.name, description: s.description, body: s.body })),
        // money rules go back exactly as they came; the backend refuses them anyway
        rules: draft.rules.map((r) => ({ slug: r.slug, content: r.content })),
        source_etags: view.source_etags,
      });
      setNote("");
      await load();
      onSaved();
    } catch (e: any) {
      setError(
        e instanceof ApiError && e.status === 409
          ? "Someone else changed the bot while you were editing. Reload to see their version first."
          : (e?.message ?? "could not save"),
      );
    } finally {
      setBusy(false);
    }
  }

  async function republish(v: number) {
    if (!window.confirm(`Put version ${v} back? This publishes it as a new version; nothing in the log is lost.`))
      return;
    setBusy(true);
    setError(null);
    try {
      await api.republishRoomAgent(roomId, v, `republished v${v}`);
      await load();
      onSaved();
    } catch (e: any) {
      setError(e?.message ?? "could not republish");
    } finally {
      setBusy(false);
    }
  }

  async function toggle(v: number) {
    if (open === v) {
      setOpen(null);
      return;
    }
    setOpen(v);
    setDiff("");
    try {
      const detail = await api.roomAgentVersion(roomId, v);
      setDiff(detail.diff || "(no change)");
    } catch (e: any) {
      setDiff(e?.message ?? "could not load the diff");
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3">
      <header className="space-y-1">
        <h2 className="text-sm font-semibold text-[var(--text)]">
          {view.agent.name} <span className="font-normal text-[var(--text-secondary)]">· v{view.version?.version ?? "—"}</span>
        </h2>
        <p className="text-xs text-[var(--text-secondary)]">
          {view.profile.business} / {view.profile.name}
          {view.version ? ` · last changed by ${who(view.version.actor)} on ${when(view.version.published_at)}` : null}
        </p>
      </header>

      {locked ? (
        <Notice tone="warn">
          This room runs the <strong>shared default bot</strong>, so it can be read here but not
          changed — an edit would change every other room too. Ask an operator to give this room its
          own binding first.
        </Notice>
      ) : null}
      {view.shared && !locked ? (
        <Notice tone="warn">
          Changes here affect <strong>every room that has not been given its own bot</strong>.
        </Notice>
      ) : null}
      {view.profile.managed_by === "human" ? (
        <Notice tone="info">
          This bot has been edited by hand, so it no longer picks up prompt or skill changes from a
          deploy. An operator can re-sync it.
        </Notice>
      ) : null}

      <section className="space-y-2">
        <Field label="System prompt" hint="What the bot is told before every turn.">
          <textarea
            className={`${boxCls} min-h-[9rem] font-mono text-xs`}
            value={draft.prompt_body}
            disabled={locked || busy}
            onChange={(e) => setDraft({ ...draft, prompt_body: e.target.value })}
          />
        </Field>

        <Field label="Extra instructions" hint="One per line, added after the prompt.">
          <textarea
            className={`${boxCls} min-h-[4rem]`}
            value={draft.prompt_append.join("\n")}
            disabled={locked || busy}
            onChange={(e) =>
              setDraft({ ...draft, prompt_append: e.target.value.split("\n").filter((x) => x.trim()) })
            }
          />
        </Field>

        {draft.skills.map((skill, i) => (
          <Field key={skill.name} label={`Skill · ${skill.name}`} hint={skill.description || undefined}>
            <textarea
              className={`${boxCls} min-h-[6rem] font-mono text-xs`}
              value={skill.body}
              disabled={locked || busy}
              onChange={(e) => {
                const skills = [...draft.skills];
                skills[i] = { ...skill, body: e.target.value };
                setDraft({ ...draft, skills });
              }}
            />
          </Field>
        ))}

        {draft.rules.map((rule, i) => (
          <Field
            key={rule.slug}
            label={`Rule · ${rule.slug}${rule.editable ? "" : " 🔒"}`}
            hint={rule.editable ? undefined : "A money rule. Only an operator can change this one."}
          >
            <textarea
              className={`${boxCls} min-h-[5rem] font-mono text-xs`}
              value={rule.content}
              disabled={locked || busy || !rule.editable}
              onChange={(e) => {
                const rules = [...draft.rules];
                rules[i] = { ...rule, content: e.target.value };
                setDraft({ ...draft, rules });
              }}
            />
          </Field>
        ))}
      </section>

      {!locked ? (
        <div className="space-y-2">
          <input
            className={boxCls}
            placeholder="What changed, and why?"
            value={note}
            disabled={busy}
            onChange={(e) => setNote(e.target.value)}
          />
          <button
            type="button"
            className="w-full rounded-lg bg-[var(--accent)] px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
            disabled={busy}
            onClick={() => void save()}
          >
            {busy ? "Publishing…" : "Publish"}
          </button>
        </div>
      ) : null}

      {error ? <Notice tone="warn">{error}</Notice> : null}

      <section className="space-y-1">
        <h3 className="text-xs font-semibold text-[var(--text-secondary)]">Not editable here</h3>
        <dl className="rounded-lg border border-[var(--border)] bg-[var(--surface-2)] p-2 text-xs text-[var(--text-secondary)]">
          {([
            ["Model", ro.models?.text ?? "—"],
            ["Limits", `${ro.caps?.max_tools ?? "—"} tools · ${ro.caps?.max_seconds ?? "—"}s`],
            ["Built-in tools", ro.builtin_tools.join(", ") || "none"],
            ["Tool packs", ro.tool_packs.join(", ") || "none"],
            ["Pipeline", ro.pipeline_stages.join(" → ") || "—"],
          ] as [string, string][]).map(([k, v]) => (
            <div key={k} className="flex justify-between gap-3 py-0.5">
              <dt>{k}</dt>
              <dd className="text-right text-[var(--text)]">{v}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="space-y-1">
        <h3 className="text-xs font-semibold text-[var(--text-secondary)]">History</h3>
        <ul className="space-y-1">
          {log.map((r) => (
            <li key={r.id} className="rounded-lg border border-[var(--border)] bg-[var(--surface-2)] p-2 text-xs">
              <div className="flex items-baseline justify-between gap-2">
                <button type="button" className="text-left text-[var(--text)]" onClick={() => void toggle(r.version)}>
                  <strong>v{r.version}</strong> {r.status === "published" ? "· live" : ""}{" "}
                  <span className="text-[var(--text-secondary)]">{r.note || "no note"}</span>
                </button>
                {!locked && r.status === "superseded" ? (
                  <button
                    type="button"
                    className="shrink-0 rounded border border-[var(--border)] px-2 py-0.5 text-[var(--text-secondary)] disabled:opacity-50"
                    disabled={busy}
                    onClick={() => void republish(r.version)}
                  >
                    Republish
                  </button>
                ) : null}
              </div>
              <p className="text-[var(--text-secondary)]">
                {who(r.actor)} · {when(r.published_at ?? r.created_at)}
                {r.paths.length ? ` · ${r.paths.join(", ")}` : ""}
              </p>
              {open === r.version ? (
                <pre className="mt-1 max-h-56 overflow-auto rounded bg-[var(--surface)] p-2 font-mono text-[11px] text-[var(--text-secondary)]">
                  {diff || "Loading…"}
                </pre>
              ) : null}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
