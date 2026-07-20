"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import * as api from "@/lib/api";
import { useSession } from "@/lib/session";
import { ThemeToggle } from "@/lib/theme";
import { useRoom } from "@/hooks/use-room";
import { MessageList } from "./message-list";
import { Composer } from "./composer";
import { AgentTimeline } from "./agent-timeline";

interface Member {
  id: number;
  display_name: string;
  nickname?: string | null;
}

function MemberChips({ members }: { members: Member[] }) {
  if (members.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {members.map((m) => (
        <span
          key={m.id}
          title={m.display_name}
          className="inline-flex items-center gap-1 rounded-full border border-[var(--border)] bg-[var(--bg-base)] px-2.5 py-1 text-xs text-[var(--text-secondary)]"
        >
          <span
            aria-hidden
            className="flex h-4 w-4 items-center justify-center rounded-full bg-[var(--accent-primary)] text-[10px] font-medium text-white"
          >
            {(m.nickname || m.display_name || "?").charAt(0).toUpperCase()}
          </span>
          {m.nickname || m.display_name}
        </span>
      ))}
    </div>
  );
}

export function RoomView({ roomId }: { roomId: number }) {
  const { messages, typing, timelines, send } = useRoom(roomId);
  const { signOut } = useSession();
  const [members, setMembers] = useState<Member[]>([]);
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
    <main className="flex h-screen flex-col bg-[var(--bg-base)]">
      <header className="border-b border-[var(--border)] bg-[var(--bg-surface)]">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-3 px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <h1 className="text-base font-semibold text-[var(--text-primary)]">
              chiatienan
            </h1>
            <div className="flex items-center gap-2">
              <ThemeToggle />
              <Link
                href="/profile"
                className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text-secondary)] shadow-sm transition-colors duration-150 hover:bg-[var(--bg-base)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)]"
              >
                Hồ sơ
              </Link>
              <button
                type="button"
                onClick={signOut}
                className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text-secondary)] shadow-sm transition-colors duration-150 hover:bg-[var(--bg-base)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)]"
              >
                Đăng xuất
              </button>
            </div>
          </div>
          <MemberChips members={members} />
        </div>
      </header>

      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-4 py-6">
          {messages.length === 0 && !typing && (
            <p className="mt-8 text-center text-sm text-[var(--text-secondary)]">
              Chưa có tin nhắn nào. Bắt đầu bằng cách nhắn @bot.
            </p>
          )}
          <MessageList messages={messages} />
          {Object.entries(timelines).map(([tid, steps]) => (
            <AgentTimeline key={tid} steps={steps} live={typing} />
          ))}
          {typing && (
            <div className="mt-4 flex items-center gap-2 text-sm text-[var(--text-secondary)]">
              <span className="flex gap-1">
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--accent-primary)] [animation-delay:-0.3s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--accent-primary)] [animation-delay:-0.15s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--accent-primary)]" />
              </span>
              bot đang trả lời…
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      <div className="border-t border-[var(--border)] bg-[var(--bg-surface)]">
        <div className="mx-auto w-full max-w-3xl px-4 py-3">
          <Composer onSend={send} />
        </div>
      </div>
    </main>
  );
}
