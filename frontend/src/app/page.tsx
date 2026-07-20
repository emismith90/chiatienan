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
            Mở link mời từ admin để tham gia phòng.
          </p>
        </div>
      </main>
    );
  }

  return <RoomView roomId={roomId} />;
}
