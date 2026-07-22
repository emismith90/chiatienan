"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { parseInviteToken } from "@/lib/invite";

const inputClass =
  "w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-2 text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] focus:outline-none focus-visible:ring-2 ring-[var(--accent-primary)] transition-all duration-150";

/** Join-by-link entry for when there's no token in the URL — e.g. the installed
 * PWA opens at `/`. Paste the shared invite link (or just its code) and we hand
 * off to the existing token-based join flow at /join/<token>. */
export default function JoinByLink() {
  const router = useRouter();
  const [value, setValue] = useState("");
  const [err, setErr] = useState("");

  function submit() {
    const token = parseInviteToken(value);
    if (!token) {
      setErr("Paste a valid invite link or code.");
      return;
    }
    router.push(`/join/${token}`);
  }

  return (
    <main className="min-h-dvh pt-safe pb-safe bg-[var(--bg-base)] flex items-center justify-center p-4">
      <div className="bg-[var(--bg-surface)] rounded-lg border border-[var(--border)] shadow-md max-w-md w-full p-6 space-y-4">
        <div>
          <h1 className="text-lg font-semibold text-[var(--text-primary)]">Join a room</h1>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Paste the invite link someone shared with you.
          </p>
        </div>

        <input
          aria-label="Invite link"
          placeholder="https://…/join/… or invite code"
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            if (err) setErr("");
          }}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          autoFocus
          className={inputClass}
        />

        {err && (
          <p role="alert" className="text-sm text-[var(--accent-text)]">
            {err}
          </p>
        )}

        <button
          type="button"
          onClick={submit}
          className="w-full rounded-md bg-[var(--accent-primary)] hover:bg-[var(--accent-hover)] text-white py-2 transition-all duration-150"
        >
          Continue
        </button>

        <p className="text-center text-sm text-[var(--text-secondary)]">
          No link?{" "}
          <a href="/create" className="text-[var(--accent-text)] hover:underline">
            Create a room
          </a>
        </p>
      </div>
    </main>
  );
}
