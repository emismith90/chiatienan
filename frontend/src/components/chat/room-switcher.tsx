"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "@/lib/session";

/** Header room-name button + dropdown menu (multi-room spec 2026-07-22).
 * Single-room users see just their room's name; the menu hosts switching
 * (only when >1 room), creating a room, and removing this room locally. */
export function RoomSwitcher() {
  const { rooms, roomId, roomName, switchRoom, removeRoom } = useSession();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const others = rooms.filter((r) => r.roomId !== roomId);
  const itemClass =
    "block w-full rounded px-3 py-2 text-left text-sm text-[var(--text-primary)] transition-colors duration-150 hover:bg-[var(--bg-base)]";

  return (
    <div ref={ref} className="relative min-w-0">
      <button
        type="button"
        aria-label={`Room menu: ${roomName || "chiatienan"}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex min-w-0 items-center gap-1 text-base font-semibold text-[var(--text-primary)]"
      >
        <span className="truncate">{roomName || "chiatienan"}</span>
        <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden className="h-4 w-4 shrink-0 text-[var(--text-secondary)]">
          <path
            fillRule="evenodd"
            d="M5.22 8.22a.75.75 0 0 1 1.06 0L10 11.94l3.72-3.72a.75.75 0 1 1 1.06 1.06l-4.25 4.25a.75.75 0 0 1-1.06 0L5.22 9.28a.75.75 0 0 1 0-1.06Z"
            clipRule="evenodd"
          />
        </svg>
      </button>
      {open && (
        <div
          role="menu"
          className="absolute left-0 top-full z-50 mt-2 w-56 rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-1.5 shadow-xl"
        >
          {others.length > 0 && (
            <>
              <p className="px-3 pb-1 pt-1.5 text-xs font-medium uppercase tracking-wide text-[var(--text-secondary)]">
                Switch room
              </p>
              {others.map((r) => (
                <button
                  key={r.roomId}
                  type="button"
                  role="menuitem"
                  onClick={() => { switchRoom(r.roomId); setOpen(false); }}
                  className={itemClass}
                >
                  {r.roomName || `Room ${r.roomId}`}
                </button>
              ))}
              <hr className="my-1.5 border-[var(--border)]" />
            </>
          )}
          <button
            type="button"
            role="menuitem"
            onClick={() => { setOpen(false); router.push("/join"); }}
            className={itemClass}
          >
            Join a room
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => { setOpen(false); router.push("/create"); }}
            className={itemClass}
          >
            Create a room
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false); // close first — declining the confirm shouldn't leave the menu up
              if (roomId != null &&
                  window.confirm("Remove this room from this device? Your account in the room is kept.")) {
                removeRoom(roomId);
              }
            }}
            className={`${itemClass} text-[var(--text-secondary)]`}
          >
            Remove from this device
          </button>
        </div>
      )}
    </div>
  );
}
