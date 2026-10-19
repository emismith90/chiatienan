"use client";
import { useEffect, useState } from "react";
import { useSession } from "@/lib/session";
import { RoomView } from "@/components/chat/room-view";
import { PoweredBy } from "@/components/powered-by";

/** How long the boot splash stays up at least. Hydration alone takes a few
 * milliseconds — a flash nobody can read. */
export const SPLASH_MIN_MS = 2000;

export default function Home() {
  const { ready } = useSession();
  const [held, setHeld] = useState(true);

  useEffect(() => {
    const timer = setTimeout(() => setHeld(false), SPLASH_MIN_MS);
    return () => clearTimeout(timer);
  }, []);

  const splash = held || !ready;

  // The splash is laid over the app rather than in place of it: the room view
  // mounts underneath as soon as the session is ready, so its history and
  // balances load while the splash is still showing. `inert` keeps the hidden
  // view out of focus and the accessibility tree until the splash lifts.
  return (
    <>
      {ready && (
        <div className="contents" inert={splash}>
          <Content />
        </div>
      )}
      {splash && (
        <main
          role="status"
          aria-label="Loading chiatienan"
          className="fixed inset-0 z-[100] flex flex-col items-center justify-center gap-6 bg-[var(--bg-base)] p-8"
        >
          <PoweredBy variant="splash" />
        </main>
      )}
    </>
  );
}

function Content() {
  const { token, roomId } = useSession();

  if (!token || !roomId) {
    return (
      <main className="flex min-h-dvh items-center justify-center bg-[var(--bg-base)] p-8">
        <div className="max-w-md rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] px-8 py-8 text-center shadow-sm">
          <h1 className="text-xl font-semibold text-[var(--text-primary)]">Reborn: chiatienan</h1>
          <p className="mt-2 text-sm text-[var(--text-secondary)]">
            Join a room with an invite link, or start a new one.
          </p>
          <div className="mt-5 flex flex-col gap-2 sm:flex-row sm:justify-center">
            <a
              href="/join"
              className="inline-block rounded-md bg-[var(--accent-primary)] px-4 py-2 text-sm text-white transition-all duration-150 hover:bg-[var(--accent-hover)]"
            >
              Join a room
            </a>
            <a
              href="/create"
              className="inline-block rounded-md border border-[var(--border)] px-4 py-2 text-sm text-[var(--text-primary)] transition-all duration-150 hover:bg-[var(--bg-base)]"
            >
              Create a room
            </a>
          </div>
          <div className="mt-6 border-t border-[var(--border)] pt-4">
            <PoweredBy />
          </div>
        </div>
      </main>
    );
  }

  return <RoomView roomId={roomId} />;
}
