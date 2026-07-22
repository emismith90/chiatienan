"use client";
import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import * as api from "@/lib/api";
import { useSession } from "@/lib/session";
import { getProfile, listRooms, saveProfile } from "@/lib/rooms-store";

type Member = { display_name: string; nickname: string; claimed: boolean };
type Room = { room_id: number; name: string; members?: Member[] };
type Mode = "create" | "login";

const inputClass =
  "w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-2 text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] focus:outline-none focus-visible:ring-2 ring-[var(--accent-primary)] transition-all duration-150";

function LockClosedIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden className="h-4 w-4 shrink-0">
      <path
        fillRule="evenodd"
        d="M10 1a4.5 4.5 0 0 0-4.5 4.5V9H5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6a2 2 0 0 0-2-2h-.5V5.5A4.5 4.5 0 0 0 10 1Zm3 8V5.5a3 3 0 1 0-6 0V9h6Z"
        clipRule="evenodd"
      />
    </svg>
  );
}

function LockOpenIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden className="h-4 w-4 shrink-0">
      <path d="M14.5 1A4.5 4.5 0 0 0 10 5.5V9H5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6a2 2 0 0 0-2-2h-3.5V5.5a3 3 0 1 1 6 0 .75.75 0 0 0 1.5 0A4.5 4.5 0 0 0 14.5 1Z" />
    </svg>
  );
}

export default function Join() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const { signIn, switchRoom } = useSession();
  const [room, setRoom] = useState<Room | null>(null);
  // Default to sign-in/claim: rooms are usually created by an admin who has
  // already added the members, so most people arriving here are claiming an
  // existing account rather than creating a new one.
  const [mode, setMode] = useState<Mode>("login");
  const pinRef = useRef<HTMLInputElement>(null);
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
      .then((r: Room) => {
        // Already a member on this device? Just make it the active room.
        if (listRooms().some((s) => s.roomId === r.room_id)) {
          switchRoom(r.room_id);
          router.replace("/");
          return;
        }
        setRoom(r);
      })
      .catch(() => setNotFound(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // Prefill from the client-saved profile so joining another room is one tap.
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

  /** Pick a person from the roster: prefill their nickname and jump to the PIN
   * field. For an unclaimed account the PIN they enter becomes their PIN. */
  function selectMember(m: Member) {
    setErr("");
    set("nickname", m.nickname);
    requestAnimationFrame(() => pinRef.current?.focus());
  }

  const selected = room?.members?.find((m) => m.nickname === f.nickname);
  const isClaim = mode === "login" && selected != null && !selected.claimed;

  async function submit() {
    setErr("");
    setLoading(true);
    try {
      const res =
        mode === "create"
          ? await api.createAccount(token, f)
          : await api.identify(token, { nickname: f.nickname, pin: f.pin });
      // Save-back: the local profile always holds the latest values used.
      // (f's keys are exactly the SavedProfile fields on this page.)
      if (mode === "create") {
        saveProfile(f);
      } else {
        saveProfile({ nickname: f.nickname, pin: f.pin });
      }
      signIn(res.token, res.room_id, room?.name ?? "");
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
          {mode === "login" && room.members && room.members.length > 0 && (
            <div className="space-y-2">
              <p className="text-xs font-medium uppercase tracking-wide text-[var(--text-secondary)]">
                People in this room
              </p>
              <div className="flex flex-col gap-1.5">
                {room.members.map((m) => {
                  const active = f.nickname === m.nickname;
                  return (
                    <button
                      key={m.nickname}
                      type="button"
                      onClick={() => selectMember(m)}
                      aria-label={m.claimed ? `Sign in as ${m.display_name}` : `Claim ${m.display_name}`}
                      className={`flex items-center justify-between gap-2 rounded-md border px-3 py-2 text-left text-sm transition-colors duration-150 focus:outline-none focus-visible:ring-2 ring-[var(--accent-primary)] ${
                        active
                          ? "border-[var(--accent-primary)] bg-[var(--bg-base)]"
                          : "border-[var(--border)] hover:bg-[var(--bg-base)]"
                      } ${m.claimed ? "text-[var(--text-secondary)]" : "text-[var(--text-primary)]"}`}
                    >
                      <span className="flex min-w-0 items-center gap-2">
                        <span className={m.claimed ? "text-[var(--text-secondary)]" : "text-[var(--accent-text)]"}>
                          {m.claimed ? <LockClosedIcon /> : <LockOpenIcon />}
                        </span>
                        <span className="truncate font-medium">{m.display_name}</span>
                        <span className="truncate text-[var(--text-secondary)]">@{m.nickname}</span>
                      </span>
                      {!m.claimed && (
                        <span className="shrink-0 text-xs font-medium text-[var(--accent-text)]">claim</span>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
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
            ref={pinRef}
            aria-label="PIN"
            placeholder={isClaim ? "Set a PIN to claim this account" : "PIN"}
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
          {loading
            ? "Processing…"
            : mode === "create"
              ? "Create & join"
              : isClaim
                ? "Claim & sign in"
                : "Sign in"}
        </button>
      </div>
    </main>
  );
}
