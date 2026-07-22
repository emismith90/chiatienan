"use client";
import { useEffect, useRef, useState } from "react";
import * as api from "@/lib/api";
import { useSession } from "@/lib/session";
import { ThemeToggle } from "@/lib/theme";
import { useRoom } from "@/hooks/use-room";
import { useOnline } from "@/hooks/use-online";
import { InstallButton } from "@/components/install-button";
import { MessageList } from "./message-list";
import { Composer } from "./composer";
import { AgentTimeline } from "./agent-timeline";
import { RoomSwitcher } from "./room-switcher";

interface Member {
  id: number;
  display_name: string;
  nickname?: string | null;
  claimed?: boolean;
  has_bank?: boolean;
  bank_code?: string | null;
  account_number?: string | null;
  account_holder?: string | null;
}

/** Copy text to the clipboard, falling back to a hidden-textarea + execCommand
 * for browsers/contexts where the async Clipboard API isn't available. */
async function copyText(text: string): Promise<void> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
  } catch {
    // fall through to the legacy path
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  try {
    document.execCommand("copy");
  } finally {
    document.body.removeChild(ta);
  }
}

/** Header action: copy a join link (`<origin>/join/<token>`) to the clipboard
 * so any member can invite others. Briefly confirms with "Copied!". */
function InviteButton({ roomId }: { roomId: number }) {
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  async function onClick() {
    if (busy) return;
    setBusy(true);
    try {
      const { invite_token } = await api.getInvite(roomId);
      await copyText(`${window.location.origin}/join/${invite_token}`);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Network/permission failure: leave the label unchanged.
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      aria-label="Copy invite link"
      className="shrink-0 whitespace-nowrap rounded-lg border border-[var(--border)] px-2.5 py-1.5 text-sm text-[var(--text-secondary)] shadow-sm transition-colors duration-150 hover:bg-[var(--bg-base)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)] disabled:opacity-60 sm:px-3"
    >
      {copied ? "Copied!" : "Invite"}
    </button>
  );
}

function MemberChips({
  members,
  selfId,
  onSelect,
}: {
  members: Member[];
  selfId: number | null;
  onSelect: (m: Member) => void;
}) {
  if (members.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {members.map((m) => {
        const isSelf = m.id === selfId;
        return (
          <button
            key={m.id}
            type="button"
            onClick={() => onSelect(m)}
            title={`${m.display_name} — tap for info`}
            className={`inline-flex min-h-8 items-center gap-1 rounded-full border px-3 py-1.5 text-xs transition-colors duration-150 hover:bg-[var(--bg-surface)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)] ${
              isSelf
                ? "border-[var(--accent-primary)] text-[var(--text-primary)] ring-1 ring-[var(--accent-primary)]"
                : "border-[var(--border)] text-[var(--text-secondary)]"
            } bg-[var(--bg-base)]`}
          >
            <span
              aria-hidden
              className="flex h-4 w-4 items-center justify-center rounded-full bg-[var(--accent-primary)] text-[10px] font-medium text-white"
            >
              {(m.nickname || m.display_name || "?").charAt(0).toUpperCase()}
            </span>
            {m.nickname || m.display_name}
            {isSelf && <span className="text-[10px] font-medium text-[var(--accent-text)]">You</span>}
          </button>
        );
      })}
    </div>
  );
}

function MemberInfoDialog({
  member,
  selfId,
  onClose,
}: {
  member: Member;
  selfId: number | null;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const isSelf = member.id === selfId;
  const status = isSelf ? "This is you" : member.claimed ? "Joined" : "Not joined yet";

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Member: ${member.display_name}`}
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-xs rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-5 shadow-xl"
      >
        <div className="flex items-center gap-3">
          <span
            aria-hidden
            className="flex h-10 w-10 items-center justify-center rounded-full bg-[var(--accent-primary)] text-base font-semibold text-white"
          >
            {(member.nickname || member.display_name || "?").charAt(0).toUpperCase()}
          </span>
          <div className="min-w-0">
            <p className="truncate font-semibold text-[var(--text-primary)]">{member.display_name}</p>
            <p className="truncate text-sm text-[var(--text-secondary)]">@{member.nickname}</p>
          </div>
        </div>
        <dl className="mt-4 space-y-2 text-sm">
          <div className="flex items-center justify-between gap-2">
            <dt className="text-[var(--text-secondary)]">Status</dt>
            <dd className="font-medium text-[var(--text-primary)]">{status}</dd>
          </div>
          {member.has_bank ? (
            <>
              <div className="flex items-center justify-between gap-2">
                <dt className="text-[var(--text-secondary)]">Bank</dt>
                <dd className="font-medium text-[var(--text-primary)]">{member.bank_code}</dd>
              </div>
              <div className="flex items-center justify-between gap-2">
                <dt className="text-[var(--text-secondary)]">Account</dt>
                <dd className="select-all font-mono font-medium text-[var(--text-primary)]">
                  {member.account_number}
                </dd>
              </div>
              <div className="flex items-center justify-between gap-2">
                <dt className="shrink-0 text-[var(--text-secondary)]">Holder</dt>
                <dd className="truncate font-medium text-[var(--text-primary)]">{member.account_holder}</dd>
              </div>
            </>
          ) : (
            <div className="flex items-center justify-between gap-2">
              <dt className="text-[var(--text-secondary)]">Payment</dt>
              <dd className="text-[var(--text-secondary)]">No bank details yet</dd>
            </div>
          )}
        </dl>
        <button
          type="button"
          onClick={onClose}
          className="mt-5 w-full rounded-md border border-[var(--border)] py-2 text-sm text-[var(--text-secondary)] transition-colors duration-150 hover:bg-[var(--bg-base)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)]"
        >
          Close
        </button>
      </div>
    </div>
  );
}

const dialogInputClass =
  "w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] focus:outline-none focus-visible:ring-2 ring-[var(--accent-primary)] transition-all duration-150";

/** Editable "you" popup — the merged Profile. Tapping your own chip opens this;
 * edit your display name + bank details, save, or sign out. Seeded from the
 * roster data already in hand (no extra fetch). */
function ProfileDialog({
  member,
  onClose,
  onSaved,
}: {
  member: Member;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { signOut } = useSession();
  const [f, setF] = useState({
    display_name: member.display_name || "",
    bank_code: member.bank_code || "",
    account_number: member.account_number || "",
    account_holder: member.account_holder || "",
  });
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function set<K extends keyof typeof f>(key: K, value: string) {
    setSaved(false);
    setF((prev) => ({ ...prev, [key]: value }));
  }

  async function save() {
    setErr("");
    setSaved(false);
    setSaving(true);
    try {
      await api.updateMe(f);
      setSaved(true);
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't save, please try again.");
    } finally {
      setSaving(false);
    }
  }

  const fields: { key: keyof typeof f; label: string; placeholder: string }[] = [
    { key: "display_name", label: "Display name", placeholder: "Display name" },
    { key: "bank_code", label: "Bank code", placeholder: "Bank code (e.g. VCB)" },
    { key: "account_number", label: "Account number", placeholder: "Account number" },
    { key: "account_holder", label: "Account holder", placeholder: "Account holder" },
  ];

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Your profile"
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-sm rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-5 shadow-xl"
      >
        <div className="flex items-center gap-3">
          <span
            aria-hidden
            className="flex h-10 w-10 items-center justify-center rounded-full bg-[var(--accent-primary)] text-base font-semibold text-white"
          >
            {(member.nickname || member.display_name || "?").charAt(0).toUpperCase()}
          </span>
          <div className="min-w-0">
            <p className="truncate font-semibold text-[var(--text-primary)]">{member.display_name}</p>
            <p className="truncate text-sm text-[var(--text-secondary)]">
              @{member.nickname} · This is you
            </p>
          </div>
        </div>

        <div className="mt-4 space-y-3">
          {fields.map(({ key, label, placeholder }) => (
            <div key={key}>
              <label htmlFor={`pf-${key}`} className="mb-1 block text-xs text-[var(--text-secondary)]">
                {label}
              </label>
              <input
                id={`pf-${key}`}
                placeholder={placeholder}
                value={f[key]}
                onChange={(e) => set(key, e.target.value)}
                className={dialogInputClass}
              />
            </div>
          ))}
        </div>

        {err && (
          <p role="alert" className="mt-3 text-sm text-[var(--accent-text)]">
            {err}
          </p>
        )}
        {saved && !err && <p className="mt-3 text-sm text-[var(--text-secondary)]">Saved</p>}

        <div className="mt-5 space-y-2">
          <button
            type="button"
            onClick={save}
            disabled={saving}
            className="w-full rounded-md bg-[var(--accent-primary)] py-2 text-sm font-medium text-white transition-colors duration-150 hover:bg-[var(--accent-hover)] disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save"}
          </button>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 rounded-md border border-[var(--border)] py-2 text-sm text-[var(--text-secondary)] transition-colors duration-150 hover:bg-[var(--bg-base)]"
            >
              Close
            </button>
            <button
              type="button"
              onClick={signOut}
              className="flex-1 rounded-md border border-[var(--border)] py-2 text-sm text-[var(--text-primary)] transition-colors duration-150 hover:bg-[var(--bg-base)]"
            >
              Sign out
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function RoomView({ roomId }: { roomId: number }) {
  const { messages, typing, timelines, activeTurn, send } = useRoom(roomId);
  const { memberId } = useSession();
  const online = useOnline();
  const [members, setMembers] = useState<Member[]>([]);
  const [selectedMember, setSelectedMember] = useState<Member | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    api
      .getMembers(roomId)
      .then((m: Member[]) => live && setMembers(m))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [roomId]);

  // Auto-scroll to the newest message / typing indicator.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, typing]);

  return (
    <main className="flex h-dvh flex-col bg-[var(--bg-base)]">
      <header className="pt-safe border-b border-[var(--border)] bg-[var(--bg-surface)]">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-3 px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2">
              <RoomSwitcher />
              {!online && (
                <span
                  role="status"
                  className="inline-flex shrink-0 items-center gap-1 rounded-full border border-[var(--border)] bg-[var(--bg-base)] px-2 py-0.5 text-xs text-[var(--text-secondary)]"
                >
                  <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-[var(--text-secondary)]" />
                  Offline
                </span>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
              <InstallButton />
              <ThemeToggle />
              <InviteButton roomId={roomId} />
            </div>
          </div>
          <MemberChips members={members} selfId={memberId} onSelect={setSelectedMember} />
        </div>
      </header>

      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-4 py-6">
          {messages.length === 0 && !typing && (
            <p className="mt-8 text-center text-sm text-[var(--text-secondary)]">
              No messages yet. Tap a suggestion below or message @bot.
            </p>
          )}
          <MessageList messages={messages} members={members} roomId={roomId} timelines={timelines} />
          {/* Only the in-progress turn (no draft/bot message yet) renders here,
              live. Once it finishes, its timeline attaches collapsed above the
              message it produced — see MessageList. */}
          {activeTurn && timelines[activeTurn] && (
            <AgentTimeline steps={timelines[activeTurn]} live={true} />
          )}
          {typing && (
            <div role="status" className="mt-4 flex items-center gap-2 text-sm text-[var(--text-secondary)]">
              <span aria-hidden className="flex gap-1">
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--accent-primary)] [animation-delay:-0.3s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--accent-primary)] [animation-delay:-0.15s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--accent-primary)]" />
              </span>
              bot is replying…
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      <div className="pb-safe border-t border-[var(--border)] bg-[var(--bg-surface)]">
        <div className="mx-auto w-full max-w-3xl px-4 py-3">
          <Composer onSend={send} />
        </div>
      </div>

      {selectedMember &&
        (selectedMember.id === memberId ? (
          <ProfileDialog
            member={selectedMember}
            onClose={() => setSelectedMember(null)}
            onSaved={() => api.getMembers(roomId).then((m: Member[]) => setMembers(m)).catch(() => {})}
          />
        ) : (
          <MemberInfoDialog
            member={selectedMember}
            selfId={memberId}
            onClose={() => setSelectedMember(null)}
          />
        ))}
    </main>
  );
}
