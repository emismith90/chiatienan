"use client";
import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import * as api from "@/lib/api";
import { useSession } from "@/lib/session";

type Room = { room_id: number; name: string };
type Mode = "create" | "login";

const inputClass =
  "w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-2 text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] focus:outline-none focus-visible:ring-2 ring-[var(--accent-primary)] transition-all duration-150";

export default function Join() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const { signIn } = useSession();
  const [room, setRoom] = useState<Room | null>(null);
  const [mode, setMode] = useState<Mode>("create");
  const [f, setF] = useState({
    display_name: "",
    nickname: "",
    pin: "",
    bank_code: "",
    account_number: "",
    account_holder: "",
  });
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    api
      .roomInfo(token)
      .then((r: Room) => setRoom(r))
      .catch(() => setNotFound(true));
  }, [token]);

  function set<K extends keyof typeof f>(key: K, value: string) {
    setF((prev) => ({ ...prev, [key]: value }));
  }

  async function submit() {
    setErr("");
    setLoading(true);
    try {
      const res =
        mode === "create"
          ? await api.createAccount(token, f)
          : await api.identify(token, { nickname: f.nickname, pin: f.pin });
      signIn(res.token, res.room_id);
      router.push("/");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Something went wrong, please try again.");
    } finally {
      setLoading(false);
    }
  }

  if (notFound) {
    return (
      <main className="min-h-dvh pt-safe pb-safe bg-[var(--bg-base)] flex items-center justify-center p-4">
        <div className="bg-[var(--bg-surface)] rounded-lg border border-[var(--border)] shadow-md max-w-md w-full p-6 text-center">
          <p className="text-[var(--text-primary)] font-medium">Invalid link.</p>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Please double-check the shared link.
          </p>
        </div>
      </main>
    );
  }

  if (!room) return null;

  return (
    <main className="min-h-dvh pt-safe pb-safe bg-[var(--bg-base)] flex items-center justify-center p-4">
      <div className="bg-[var(--bg-surface)] rounded-lg border border-[var(--border)] shadow-md max-w-md w-full p-6 space-y-4">
        <div>
          <h1 className="text-lg font-semibold text-[var(--text-primary)]">
            Join &ldquo;{room.name}&rdquo;
          </h1>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Split rent, utilities, and groceries &mdash; fair for everyone.
          </p>
        </div>

        <div className="flex gap-4 border-b border-[var(--border)] text-sm">
          <button
            type="button"
            onClick={() => setMode("create")}
            className={`-mb-px pb-2 font-medium transition-all duration-150 ${
              mode === "create"
                ? "text-[var(--accent-text)] border-b-2 border-[var(--accent-primary)]"
                : "text-[var(--text-secondary)]"
            }`}
          >
            Create account
          </button>
          <button
            type="button"
            onClick={() => setMode("login")}
            className={`-mb-px pb-2 font-medium transition-all duration-150 ${
              mode === "login"
                ? "text-[var(--accent-text)] border-b-2 border-[var(--accent-primary)]"
                : "text-[var(--text-secondary)]"
            }`}
          >
            Already joined / sign in
          </button>
        </div>

        <div className="space-y-3">
          {mode === "create" && (
            <input
              aria-label="Display name"
              placeholder="Display name"
              value={f.display_name}
              onChange={(e) => set("display_name", e.target.value)}
              className={inputClass}
            />
          )}
          <input
            aria-label="Nickname"
            placeholder="Nickname"
            value={f.nickname}
            onChange={(e) => set("nickname", e.target.value)}
            className={inputClass}
          />
          <input
            aria-label="PIN"
            placeholder="PIN"
            type="password"
            inputMode="numeric"
            value={f.pin}
            onChange={(e) => set("pin", e.target.value)}
            className={inputClass}
          />
          {mode === "create" && (
            <>
              <input
                aria-label="Bank code"
                placeholder="Bank code (e.g. VCB)"
                value={f.bank_code}
                onChange={(e) => set("bank_code", e.target.value)}
                className={inputClass}
              />
              <input
                aria-label="Account number"
                placeholder="Account number"
                value={f.account_number}
                onChange={(e) => set("account_number", e.target.value)}
                className={inputClass}
              />
              <input
                aria-label="Account holder"
                placeholder="Account holder"
                value={f.account_holder}
                onChange={(e) => set("account_holder", e.target.value)}
                className={inputClass}
              />
            </>
          )}
        </div>

        {err && (
          <p role="alert" className="text-sm text-[var(--accent-text)]">
            {err}
          </p>
        )}

        <button
          type="button"
          onClick={submit}
          disabled={loading}
          className="w-full rounded-md bg-[var(--accent-primary)] hover:bg-[var(--accent-hover)] text-white py-2 transition-all duration-150 disabled:opacity-50"
        >
          {loading ? "Processing…" : mode === "create" ? "Create & join" : "Sign in"}
        </button>
      </div>
    </main>
  );
}
