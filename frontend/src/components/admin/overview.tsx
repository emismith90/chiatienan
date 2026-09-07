/** What is live, and the two switches that change it (plan Phase 12.2).
 *
 * The two switches are the ones [Operations §2](../../../../docs/superpowers/plans/2026-09-06-deploy-runbook.md)
 * still asks an operator to run as `curl`:
 *
 *  - **a binding** is what lets a space's own members edit its agent (review F1: room
 *    membership is not permission, because anyone can create a room and an unbound room
 *    resolves to the shared default agent);
 *  - **`delegates_to`** is what makes a sub-agent reachable — it adds an `ask_<slug>`
 *    tool to the manager's manifest, which is a real change to what the model sees.
 *
 * Both say so on screen before you flip them.
 */
"use client";
import { useState } from "react";
import * as admin from "@/lib/admin-api";
import { Badge, Notice, Pre, Section, box, btn, btnPrimary, message, when } from "./ui";

export type Live = {
  businesses: admin.Business[];
  profiles: admin.Profile[];
  agents: admin.Agent[];
  bindings: admin.Binding[];
};

const th = "px-2 py-1 text-left text-[11px] font-medium uppercase text-[var(--text-secondary)]";
const td = "px-2 py-1 align-top text-xs text-[var(--text)]";

export function Overview({ live, reload }: { live: Live; reload: () => Promise<void> }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [space, setSpace] = useState("");
  const [agentId, setAgentId] = useState<number | "">("");
  const [resolved, setResolved] = useState<string>("");

  const managers = live.agents.filter((a) => a.role === "manager");
  const business = (id: number) => live.businesses.find((b) => b.id === id)?.slug ?? `#${id}`;
  const agentName = (id: number) => live.agents.find((a) => a.id === id)?.slug ?? `#${id}`;

  async function run(what: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await what();
      await reload();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  async function bind() {
    if (agentId === "" || !space.trim()) return;
    const id = Number(agentId);
    if (
      !window.confirm(
        `Bind space ${space.trim()} to ${agentName(id)}?\n\n` +
          "Everyone in that space will then be able to edit its bot's prompt, skills and " +
          "non-money rules from the Bot tab.",
      )
    )
      return;
    await run(() => admin.bind(space.trim(), id));
    setSpace("");
  }

  async function toggleDelegate(manager: admin.Agent, subId: number) {
    const on = manager.delegates_to.includes(subId);
    const next = on ? manager.delegates_to.filter((i) => i !== subId) : [...manager.delegates_to, subId];
    if (
      !on &&
      !window.confirm(
        `Give ${manager.slug} an ask_${agentName(subId)} tool?\n\n` +
          "This changes the tool manifest the model sees. Run the benchmark and compare " +
          "before leaving it on.",
      )
    )
      return;
    await run(() => admin.patchAgent(manager.id, { delegates_to: next }));
  }

  async function resolve(spaceId: string) {
    setError(null);
    setResolved("loading…");
    try {
      const r = await admin.resolved(spaceId);
      setResolved(JSON.stringify(r.spec, null, 1));
    } catch (e) {
      setResolved(message(e));
    }
  }

  return (
    <div className="space-y-6">
      {error ? <Notice tone="error">{error}</Notice> : null}

      <Section title="Businesses">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className={th}>id</th>
              <th className={th}>slug</th>
              <th className={th}>name</th>
              <th className={th}>tool packs</th>
            </tr>
          </thead>
          <tbody>
            {live.businesses.map((b) => (
              <tr key={b.id} className="border-t border-[var(--border)]">
                <td className={td}>{b.id}</td>
                <td className={td}>{b.slug}</td>
                <td className={td}>{b.name}</td>
                <td className={td}>{b.tool_packs.join(", ") || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section
        title="Profiles"
        hint="managed_by: boot means a deploy still refreshes this profile's prompt, skills and rules from code. The first human publish flips it to human, for good."
      >
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className={th}>id</th>
              <th className={th}>business</th>
              <th className={th}>name</th>
              <th className={th}>managed by</th>
              <th className={th}>published</th>
            </tr>
          </thead>
          <tbody>
            {live.profiles.map((p) => (
              <tr key={p.id} className="border-t border-[var(--border)]">
                <td className={td}>{p.id}</td>
                <td className={td}>{business(p.business_id)}</td>
                <td className={td}>{p.name}</td>
                <td className={td}>
                  <Badge tone={p.managed_by === "boot" ? "live" : "warn"}>{p.managed_by}</Badge>
                </td>
                <td className={td}>{p.published_version_id ?? "— none"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section title="Agents" hint="Tick a sub-agent to give its manager an ask_<slug> tool.">
        <div className="space-y-2">
          {live.agents.map((a) => {
            const subs = live.agents.filter((s) => s.role === "sub" && s.business_id === a.business_id);
            return (
              <div key={a.id} className="rounded-lg border border-[var(--border)] p-2">
                <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--text)]">
                  <strong>{a.slug}</strong>
                  <Badge>{a.role}</Badge>
                  {a.is_default ? <Badge tone="live">default</Badge> : null}
                  <span className="text-[var(--text-secondary)]">
                    {business(a.business_id)} · profile {a.profile_id}
                    {Object.keys(a.capabilities).length
                      ? ` · caps ${JSON.stringify(a.capabilities)}`
                      : ""}
                  </span>
                </div>
                {a.role === "manager" && subs.length ? (
                  <div className="mt-2 flex flex-wrap gap-3">
                    {subs.map((s) => (
                      <label key={s.id} className="flex items-center gap-1.5 text-xs text-[var(--text)]">
                        <input
                          type="checkbox"
                          disabled={busy}
                          checked={a.delegates_to.includes(s.id)}
                          onChange={() => void toggleDelegate(a, s.id)}
                        />
                        ask_{s.slug}
                      </label>
                    ))}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      </Section>

      <Section
        title="Bindings"
        hint="A space id is the room id here. A space with no binding runs its business's default agent and is read-only in the Bot tab."
      >
        {live.bindings.length === 0 ? (
          <Notice tone="info">No space is bound, so every room's Bot tab is a reader.</Notice>
        ) : (
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <th className={th}>space</th>
                <th className={th}>agent</th>
                <th className={th}>since</th>
                <th className={th} />
              </tr>
            </thead>
            <tbody>
              {live.bindings.map((b) => (
                <tr key={b.space_id} className="border-t border-[var(--border)]">
                  <td className={td}>{b.space_id}</td>
                  <td className={td}>{agentName(b.agent_id)}</td>
                  <td className={td}>{when(b.updated_at)}</td>
                  <td className={td}>
                    <div className="flex gap-2">
                      <button className={btn} onClick={() => void resolve(b.space_id)}>
                        What it runs
                      </button>
                      <button
                        className={btn}
                        disabled={busy}
                        onClick={() =>
                          window.confirm(`Unbind space ${b.space_id}? Its members lose the ability to edit.`) &&
                          void run(() => admin.unbind(b.space_id))
                        }
                      >
                        Unbind
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div className="flex flex-wrap items-end gap-2">
          <input
            className={`${box} max-w-[10rem]`}
            placeholder="space id (room id)"
            value={space}
            onChange={(e) => setSpace(e.target.value)}
            aria-label="space id"
          />
          <select
            className={`${box} max-w-[14rem]`}
            value={agentId}
            onChange={(e) => setAgentId(e.target.value === "" ? "" : Number(e.target.value))}
            aria-label="agent"
          >
            <option value="">choose a manager…</option>
            {managers.map((a) => (
              <option key={a.id} value={a.id}>
                {a.slug} ({business(a.business_id)})
              </option>
            ))}
          </select>
          <button className={btnPrimary} disabled={busy || !space.trim() || agentId === ""} onClick={() => void bind()}>
            Bind
          </button>
        </div>
      </Section>

      {resolved ? <Pre>{resolved}</Pre> : null}
    </div>
  );
}
