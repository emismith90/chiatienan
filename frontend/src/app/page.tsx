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
            Open an invite link from the admin to join a room.
          </p>
          <a
            href="/create"
            className="mt-4 inline-block rounded-md bg-[var(--accent-primary)] px-4 py-2 text-sm text-white transition-all duration-150 hover:bg-[var(--accent-hover)]"
          >
            Create a room
          </a>
        </div>
      </main>
    );
  }

  return <RoomView roomId={roomId} />;
}
