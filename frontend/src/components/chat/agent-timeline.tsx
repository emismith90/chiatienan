"use client";
import { useState } from "react";
import type { TimelineStep } from "@/hooks/use-room";

const LABELS: Record<string, string> = {
  find_members: "Đang tra thành viên…",
  propose_meal: "Đang soạn bữa ăn…",
  settle_period: "Đang tính chuyển khoản…",
  get_period_balances: "Đang tính số dư…",
  resolve_period: "Đang xác định kỳ…",
};

export function AgentTimeline({ steps, live }: { steps: TimelineStep[]; live: boolean }) {
  const [open, setOpen] = useState(false);
  if (steps.length === 0 && !live) return null;
  const collapsed = !live && !open;
  return (
    <div className="mt-2 rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] px-3 py-2 text-xs text-[var(--text-secondary)]">
      <button type="button" onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-2 text-left">
        <span className="font-medium text-[var(--accent-primary)]">
          {live ? "Bot đang xử lý…" : `▸ ${steps.length} bước`}
        </span>
      </button>
      {!collapsed && (
        <ul className="mt-1 space-y-1">
          {steps.map((s, i) => (
            <li key={i} className="flex items-center gap-2">
              {s.kind === "tool" ? (
                <>
                  <span aria-hidden>{s.status === "running" ? "⏳" : s.status === "error" ? "⚠️" : "✓"}</span>
                  <span>{s.name ? LABELS[s.name] ?? s.name : "công cụ"}</span>
                </>
              ) : (
                <span className="italic">{s.text}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
