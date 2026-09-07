/** The CMS itself: sources, then versions (plan Phase 12.2).
 *
 * The two halves are in the order the content plane works in, because getting it the
 * wrong way round is the classic mistake here:
 *
 *  1. **Sources are upstream.** The skills and rules live in `kn_sources`. Editing one
 *     changes nothing on its own.
 *  2. **A draft snapshots them.** `New draft` pulls the business's *current* sources into
 *     a new version — which is also why a spec-only edit is reverted by the next
 *     snapshotting draft.
 *  3. **Publish makes it what the bot runs**, through the five gates.
 *
 * Only `prompt.body` is editable as spec here, because it is the one part of the prompt
 * with no source behind it. Models, caps, pipeline, packs and builtin tools are shown
 * read-only: they are not weekly work, and a wrong keystroke there breaks the bot in a
 * way no form can explain.
 */
"use client";
import { useCallback, useEffect, useState } from "react";
import * as admin from "@/lib/admin-api";
import type { Live } from "./overview";
import { Badge, Field, Notice, Pre, Section, box, btn, btnPrimary, message, when } from "./ui";

const tone = (status: string) =>
  status === "published" ? "live" : status === "draft" ? "warn" : "plain";

export function Content({ live, reload }: { live: Live; reload: () => Promise<void> }) {
  const [businessId, setBusinessId] = useState<number | null>(live.businesses[0]?.id ?? null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // sources
  const [sources, setSources] = useState<admin.Source[]>([]);
  const [openSource, setOpenSource] = useState<admin.Source | null>(null);
  const [body, setBody] = useState("");
  const [title, setTitle] = useState("");
  const [frontmatter, setFrontmatter] = useState("{}");

  // versions
  const [profileId, setProfileId] = useState<number | null>(null);
  const [versions, setVersions] = useState<admin.Version[]>([]);
  const [openVersion, setOpenVersion] = useState<number | null>(null);
  const [diff, setDiff] = useState<admin.Diff | null>(null);
  const [promptBody, setPromptBody] = useState("");
  const [note, setNote] = useState("");
  const [override, setOverride] = useState("");

  const profiles = live.profiles.filter((p) => p.business_id === businessId);
  const profile = profiles.find((p) => p.id === profileId) ?? null;

  const loadSources = useCallback(async (id: number) => {
    setSources(await admin.sources(id));
  }, []);

  const loadVersions = useCallback(async (id: number) => {
    const rows = await admin.versions(id);
    setVersions([...rows].sort((a, b) => b.version - a.version));
  }, []);

  useEffect(() => {
    if (businessId === null) return;
    setOpenSource(null);
    setProfileId(profiles[0]?.id ?? null);
    loadSources(businessId).catch((e) => setError(message(e)));
    // `profiles` is derived from businessId; adding it would loop on every render
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [businessId, loadSources]);

  useEffect(() => {
    setOpenVersion(null);
    setDiff(null);
    if (profileId === null) {
      setVersions([]);
      return;
    }
    loadVersions(profileId).catch((e) => setError(message(e)));
  }, [profileId, loadVersions]);

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

  function pickSource(s: admin.Source) {
    setOpenSource(s);
    setTitle(s.title);
    setBody(s.body);
    setFrontmatter(JSON.stringify(s.frontmatter ?? {}, null, 1));
  }

  async function saveSource() {
    if (!openSource || businessId === null) return;
    let fm: Record<string, any>;
    try {
      fm = JSON.parse(frontmatter || "{}");
    } catch {
      setError("The frontmatter is not valid JSON.");
      return;
    }
    const ok = await run(async () => {
      await admin.putSource(businessId, openSource.kind, openSource.slug, { title, body, frontmatter: fm }, openSource.etag);
      await loadSources(businessId);
    });
    if (ok) setOpenSource(null);
  }

  async function openDiff(v: number) {
    if (openVersion === v) {
      setOpenVersion(null);
      return;
    }
    setOpenVersion(v);
    setDiff(null);
    if (profileId === null) return;
    try {
      const [d, full] = await Promise.all([admin.versionDiff(profileId, v), admin.version(profileId, v)]);
      setDiff(d);
      setPromptBody(full.spec?.prompt?.body ?? "");
    } catch (e) {
      setError(message(e));
    }
  }

  async function newDraft(fromVersion?: number) {
    if (profileId === null) return;
    const made = await run(async () => {
      const d = await admin.createDraft(profileId, { from_version: fromVersion, note: note.trim() || undefined });
      await loadVersions(profileId);
      setOpenVersion(null);
      return d;
    });
    if (made) setNote("");
  }

  async function savePrompt(v: number) {
    if (profileId === null) return;
    await run(async () => {
      await admin.patchDraft(profileId, v, { prompt: { body: promptBody } });
      await loadVersions(profileId);
      setDiff(await admin.versionDiff(profileId, v));
    });
  }

  async function publish(v: number) {
    if (profileId === null || !profile) return;
    if (
      profile.managed_by === "boot" &&
      !window.confirm(
        "This profile still tracks code: a deploy refreshes its prompt, skills and rules.\n\n" +
          "Publishing by hand ends that, permanently. Continue?",
      )
    )
      return;
    const ok = await run(async () => {
      await admin.publish(profileId, v, {
        note: note.trim() || undefined,
        override_reason: override.trim() || undefined,
      });
      await loadVersions(profileId);
      await reload();
    });
    if (ok) {
      setNote("");
      setOverride("");
    }
  }

  return (
    <div className="space-y-6">
      {error ? <Notice tone="error">{error}</Notice> : null}

      <div className="flex flex-wrap items-end gap-2">
        <Field label="Business">
          <select
            className={`${box} max-w-[16rem]`}
            value={businessId ?? ""}
            onChange={(e) => setBusinessId(Number(e.target.value))}
            aria-label="business"
          >
            {live.businesses.map((b) => (
              <option key={b.id} value={b.id}>
                {b.slug} — {b.name}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <Section
        title="Sources"
        hint="The prompt fragments, skills and rules. Editing one changes nothing until a new draft snapshots it and you publish."
      >
        <ul className="divide-y divide-[var(--border)] rounded-lg border border-[var(--border)]">
          {sources.map((s) => (
            <li key={`${s.kind}/${s.slug}`}>
              <button
                className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-xs hover:bg-[var(--surface-2)]"
                onClick={() => pickSource(s)}
              >
                <Badge>{s.kind}</Badge>
                <span className="text-[var(--text)]">{s.slug}</span>
                <span className="ml-auto text-[11px] text-[var(--text-secondary)]">
                  {s.updated_by} · {when(s.updated_at)}
                </span>
              </button>
            </li>
          ))}
          {sources.length === 0 ? (
            <li className="px-2 py-1.5 text-xs text-[var(--text-secondary)]">No sources.</li>
          ) : null}
        </ul>

        {openSource ? (
          <div className="space-y-2 rounded-lg border border-[var(--border)] p-2">
            <p className="text-xs text-[var(--text-secondary)]">
              {openSource.kind}/{openSource.slug}
            </p>
            <Field label="Title">
              <input className={box} value={title} onChange={(e) => setTitle(e.target.value)} aria-label="title" />
            </Field>
            <Field label="Body">
              <textarea
                className={`${box} h-40 font-mono`}
                value={body}
                onChange={(e) => setBody(e.target.value)}
                aria-label="body"
              />
            </Field>
            <Field label="Frontmatter" hint="JSON. A skill's description and a rule's tags live here.">
              <textarea
                className={`${box} h-20 font-mono`}
                value={frontmatter}
                onChange={(e) => setFrontmatter(e.target.value)}
                aria-label="frontmatter"
              />
            </Field>
            <div className="flex gap-2">
              <button className={btnPrimary} disabled={busy} onClick={() => void saveSource()}>
                Save source
              </button>
              <button className={btn} onClick={() => setOpenSource(null)}>
                Cancel
              </button>
            </div>
          </div>
        ) : null}
      </Section>

      <Section title="Versions" hint="Newest first. Expand one to see what it changed.">
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Profile">
            <select
              className={`${box} max-w-[16rem]`}
              value={profileId ?? ""}
              onChange={(e) => setProfileId(Number(e.target.value))}
              aria-label="profile"
            >
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} (#{p.id})
                </option>
              ))}
            </select>
          </Field>
          <Field label="Note" hint="Goes on the version you create or publish.">
            <input className={box} value={note} onChange={(e) => setNote(e.target.value)} aria-label="note" />
          </Field>
          <button className={btn} disabled={busy || profileId === null} onClick={() => void newDraft()}>
            New draft
          </button>
        </div>

        {profile?.managed_by === "boot" ? (
          <Notice tone="info">
            <strong>{profile.name}</strong> still tracks code — a deploy refreshes its prompt, skills
            and rules. The first publish from here ends that for good.
          </Notice>
        ) : null}

        <ul className="divide-y divide-[var(--border)] rounded-lg border border-[var(--border)]">
          {versions.map((v) => (
            <li key={v.id}>
              <button
                className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-xs hover:bg-[var(--surface-2)]"
                onClick={() => void openDiff(v.version)}
                aria-expanded={openVersion === v.version}
              >
                <span className="text-[var(--text)]">v{v.version}</span>
                <Badge tone={tone(v.status)}>{v.status}</Badge>
                <span className="text-[var(--text-secondary)]">{v.actor}</span>
                {v.note ? <span className="text-[var(--text-secondary)]">· {v.note}</span> : null}
                <span className="ml-auto text-[11px] text-[var(--text-secondary)]">
                  {when(v.published_at ?? v.created_at)}
                </span>
              </button>

              {openVersion === v.version ? (
                <div className="space-y-2 border-t border-[var(--border)] p-2">
                  {diff === null ? (
                    <p className="text-xs text-[var(--text-secondary)]">Loading…</p>
                  ) : (
                    <>
                      <p className="text-xs text-[var(--text-secondary)]">
                        {diff.against === null
                          ? "The first version — there is nothing before it."
                          : `Changed against v${diff.against}: ${diff.paths.join(", ") || "nothing"}`}
                      </p>
                      {diff.diff ? <Pre>{diff.diff}</Pre> : null}
                    </>
                  )}

                  {v.status === "draft" ? (
                    <>
                      <Field label="Prompt body" hint="The one part of the prompt with no source behind it.">
                        <textarea
                          className={`${box} h-32 font-mono`}
                          value={promptBody}
                          onChange={(e) => setPromptBody(e.target.value)}
                          aria-label="prompt body"
                        />
                      </Field>
                      <Field
                        label="Override reason"
                        hint="Only needed when the money-safety gate refuses a risky builtin tool."
                      >
                        <input
                          className={box}
                          value={override}
                          onChange={(e) => setOverride(e.target.value)}
                          aria-label="override reason"
                        />
                      </Field>
                      <div className="flex flex-wrap gap-2">
                        <button className={btn} disabled={busy} onClick={() => void savePrompt(v.version)}>
                          Save draft
                        </button>
                        <button className={btnPrimary} disabled={busy} onClick={() => void publish(v.version)}>
                          Publish v{v.version}
                        </button>
                        <button
                          className={btn}
                          disabled={busy}
                          onClick={() =>
                            window.confirm(`Retire draft v${v.version}?`) &&
                            void run(async () => {
                              await admin.retire(profileId!, v.version);
                              await loadVersions(profileId!);
                              setOpenVersion(null);
                            })
                          }
                        >
                          Retire
                        </button>
                      </div>
                    </>
                  ) : (
                    <button className={btn} disabled={busy} onClick={() => void newDraft(v.version)}>
                      New draft from v{v.version}
                    </button>
                  )}
                </div>
              ) : null}
            </li>
          ))}
          {versions.length === 0 ? (
            <li className="px-2 py-1.5 text-xs text-[var(--text-secondary)]">No versions.</li>
          ) : null}
        </ul>
      </Section>
    </div>
  );
}
