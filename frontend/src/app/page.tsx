"use client";
import { useSession } from "@/lib/session";
import { RoomView } from "@/components/chat/room-view";

export default function Home() {
  const { token, roomId, ready } = useSession();

  if (!ready) return null;

  if (!token || !roomId) {
    return (
      <main className="flex min-h-dvh items-center justify-center bg-[var(--bg-base)] p-8">
        <div className="max-w-md rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] px-8 py-8 text-center shadow-sm">
          <h1 className="text-xl font-semibold text-[var(--text-primary)]">chiatienan</h1>
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
        </div>
      </main>
    );
  }

  return <RoomView roomId={roomId} />;
}
