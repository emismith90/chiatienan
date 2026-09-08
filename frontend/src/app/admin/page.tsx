/** The operator's screen for the content plane (plan Phase 12.2).
 *
 * `/admin` is a public URL gated by one shared secret with full power over every
 * business, profile and version, so the credential rules live in `@/lib/admin-api`:
 * `sessionStorage` (not `localStorage`), its own client so the room bearer and the admin
 * password can never cross, never in a URL, never rendered. **Known and not fixed:**
 * `require_admin` has no rate limiting — that was already true of `curl`, but this page
 * makes the target obvious.
 *
 * Five tabs, in the order an operator needs them: what is live, what the code offers to
 * build an agent from, the CMS, the proposals waiting on a person, the log of what
 * happened.
 */
"use client";
import { useCallback, useEffect, useState } from "react";
import * as admin from "@/lib/admin-api";
import { ApiError } from "@/lib/api";
import { Audit } from "@/components/admin/audit";
import { Components } from "@/components/admin/components";
import { Content } from "@/components/admin/content";
import { Overview, type Live } from "@/components/admin/overview";
import { Proposals } from "@/components/admin/proposals";
import { Notice, box, btn, btnPrimary, message } from "@/components/admin/ui";

const TABS = [
  { id: "overview", label: "Live" },
  { id: "components", label: "Components" },
  { id: "content", label: "Content" },
  { id: "proposals", label: "Proposals" },
  { id: "audit", label: "Audit" },
] as const;

type Tab = (typeof TABS)[number]["id"];

function SignIn({ onDone }: { onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [actor, setActor] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await admin.signIn({ password, actor: actor.trim() || "admin" });
      setPassword("");
      onDone();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-dvh items-center justify-center bg-[var(--bg-base)] p-6">
      <form
        onSubmit={submit}
        className="w-full max-w-sm space-y-3 rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-5"
      >
        <h1 className="text-base font-semibold text-[var(--text)]">Agent OS — operator</h1>
        <p className="text-xs text-[var(--text-secondary)]">
          The admin password. It is kept for this tab only, and forgotten when the tab closes.
        </p>
        <input
          className={box}
          type="password"
          autoComplete="current-password"
          placeholder="admin password"
          aria-label="admin password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <input
          className={box}
          placeholder="your name (goes in the audit log)"
          aria-label="actor"
          value={actor}
          onChange={(e) => setActor(e.target.value)}
        />
        {error ? <Notice tone="error">{error}</Notice> : null}
        <button className={`${btnPrimary} w-full`} disabled={busy || !password}>
          {busy ? "Checking…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}

export default function AdminPage() {
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [live, setLive] = useState<Live | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const [businesses, profiles, agents, bindings] = await Promise.all([
        admin.businesses(),
        admin.profiles(),
        admin.agents(),
        admin.bindings(),
      ]);
      setLive({ businesses, profiles, agents, bindings });
      setError(null);
    } catch (e) {
      // A password that stopped working (rotated, or a stale tab) would otherwise leave
      // a signed-in page where every call fails. Send them back to the form.
      if (e instanceof ApiError && e.status === 401) {
        admin.clearCred();
        setSignedIn(false);
        return;
      }
      setError(message(e));
    }
  }, []);

  useEffect(() => {
    setSignedIn(admin.loadCred() !== null);
  }, []);

  useEffect(() => {
    if (signedIn) void reload();
  }, [signedIn, reload]);

  if (signedIn === null) return <main className="min-h-dvh bg-[var(--bg-base)]" />;
  if (!signedIn) return <SignIn onDone={() => setSignedIn(true)} />;

  return (
    <main className="mx-auto min-h-dvh max-w-4xl space-y-4 bg-[var(--bg-base)] p-4">
      <header className="flex items-center gap-2">
        <h1 className="text-sm font-semibold text-[var(--text)]">Agent OS</h1>
        <span className="text-xs text-[var(--text-secondary)]">
          as {admin.loadCred()?.actor ?? "admin"}
        </span>
        <button
          className={`${btn} ml-auto`}
          onClick={() => {
            admin.clearCred();
            setLive(null);
            setSignedIn(false);
          }}
        >
          Sign out
        </button>
      </header>

      <nav className="flex gap-1 border-b border-[var(--border)]">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            aria-current={tab === t.id}
            className={`px-3 py-1.5 text-xs ${
              tab === t.id
                ? "border-b-2 border-[var(--accent-primary)] text-[var(--text)]"
                : "text-[var(--text-secondary)]"
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {error ? <Notice tone="error">{error}</Notice> : null}

      {live === null ? (
        <p className="text-sm text-[var(--text-secondary)]">Loading…</p>
      ) : tab === "overview" ? (
        <Overview live={live} reload={reload} />
      ) : tab === "components" ? (
        <Components live={live} />
      ) : tab === "content" ? (
        <Content live={live} reload={reload} />
      ) : tab === "proposals" ? (
        <Proposals reload={reload} />
      ) : (
        <Audit />
      )}
    </main>
  );
}
