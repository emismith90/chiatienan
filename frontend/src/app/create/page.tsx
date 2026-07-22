"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import * as api from "@/lib/api";
import { getProfile, saveProfile } from "@/lib/rooms-store";
import { useSession } from "@/lib/session";

const inputClass =
  "w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-2 text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] focus:outline-none focus-visible:ring-2 ring-[var(--accent-primary)] transition-all duration-150";

export default function CreateRoom() {
  const router = useRouter();
  const { signIn } = useSession();
  const [f, setF] = useState({
    room_name: "",
    display_name: "",
    nickname: "",
    pin: "",
    bank_code: "",
    account_number: "",
    account_holder: "",
  });
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  // Prefill member fields from the client-saved profile (design 2026-07-22) —
  // in an effect, not the initializer, so SSR prerender never touches storage.
  useEffect(() => {
    const p = getProfile();
    setF((prev) => ({
      ...prev,
      display_name: p.display_name ?? "",
      nickname: p.nickname ?? "",
      pin: p.pin ?? "",
      bank_code: p.bank_code ?? "",
      account_number: p.account_number ?? "",
      account_holder: p.account_holder ?? "",
    }));
  }, []);

  function set<K extends keyof typeof f>(key: K, value: string) {
    setF((prev) => ({ ...prev, [key]: value }));
  }

  async function submit() {
    setErr("");
    setLoading(true);
    try {
      const res = await api.createRoom(f);
      const { room_name: _room, ...profile } = f;
      saveProfile(profile);
      signIn(res.token, res.room_id, res.room_name);
      router.push("/");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Something went wrong, please try again.");
    } finally {
      setLoading(false);
    }
  }

  const fields: { key: keyof typeof f; label: string; placeholder: string; type?: string }[] = [
    { key: "room_name", label: "Room name", placeholder: "Room name (e.g. Lunch crew)" },
    { key: "display_name", label: "Display name", placeholder: "Display name" },
    { key: "nickname", label: "Nickname", placeholder: "Nickname" },
    { key: "pin", label: "PIN", placeholder: "PIN", type: "password" },
    { key: "bank_code", label: "Bank code", placeholder: "Bank code (e.g. VCB)" },
    { key: "account_number", label: "Account number", placeholder: "Account number" },
    { key: "account_holder", label: "Account holder", placeholder: "Account holder" },
  ];

  return (
    <main className="min-h-dvh pt-safe pb-safe bg-[var(--bg-base)] flex items-center justify-center p-4">
      <div className="bg-[var(--bg-surface)] rounded-lg border border-[var(--border)] shadow-md max-w-md w-full p-6 space-y-4">
        <div>
          <h1 className="text-lg font-semibold text-[var(--text-primary)]">Create a room</h1>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Start a new group and invite people with a link.
          </p>
        </div>

        <div className="space-y-3">
          {fields.map(({ key, label, placeholder, type }) => (
            <input
              key={key}
              aria-label={label}
              placeholder={placeholder}
              type={type ?? "text"}
              inputMode={key === "pin" ? "numeric" : undefined}
              value={f[key]}
              onChange={(e) => set(key, e.target.value)}
              className={inputClass}
            />
          ))}
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
          {loading ? "Creating…" : "Create room"}
        </button>
      </div>
    </main>
  );
}
