"use client";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { BalanceTable } from "./balance-table";
import { fmt } from "@/lib/format";

interface Transfer {
  from_name: string;
  to_name: string;
  amount: number;
  qr_url?: string | null;
}

interface Share {
  name: string;
  amount: number;
}

interface BotMessageProps {
  body: string;
  attachments?: any;
}

function SettlementCard({ attachments }: { attachments: any }) {
  const transfers: Transfer[] = attachments.transfers ?? [];
  const warnings: string[] = attachments.warnings ?? [];
  const period = attachments.period ?? {};

  return (
    <div className="mt-3 space-y-3">
      {(period.from || period.to) && (
        <p className="text-xs font-medium uppercase tracking-wide text-[var(--text-secondary)]">
          {period.from ? `Period ${period.from} → ${period.to}` : `Period up to ${period.to}`}
        </p>
      )}
      {transfers.length === 0 && (
        <p className="text-sm text-[var(--text-secondary)]">
          {attachments.message || "Nothing to settle."}
        </p>
      )}
      {transfers.map((t, i) => (
        <div
          key={i}
          className="flex flex-col gap-3 rounded-lg border border-[var(--border)] bg-[var(--bg-base)] p-3 sm:flex-row sm:items-center sm:justify-between"
        >
          <div className="text-sm text-[var(--text-primary)]">
            <span className="font-medium">{t.from_name}</span>
            <span className="mx-1.5 text-[var(--text-secondary)]">→</span>
            <span className="font-medium">{t.to_name}</span>
            <span className="ml-2 font-semibold text-[var(--accent-text)]">
              {fmt(t.amount)} đ
            </span>
          </div>
          {t.qr_url && (
            <img
              src={t.qr_url}
              alt={`QR to transfer ${fmt(t.amount)} đ to ${t.to_name}`}
              width={160}
              height={160}
              className="h-40 w-40 shrink-0 self-center rounded-lg border border-[var(--border)] bg-white object-contain p-1"
            />
          )}
        </div>
      ))}
      {warnings.map((w, i) => (
        <p key={i} className="text-sm text-[var(--accent-text)]">
          ⚠️ {w}
        </p>
      ))}
    </div>
  );
}

function MealCard({ attachments }: { attachments: any }) {
  const payer = attachments.payer ?? {};
  const shares: Share[] = attachments.shares ?? [];
  const bill: number = attachments.bill_total ?? attachments.tracked_total ?? 0;
  const guests: string[] = attachments.guests ?? [];

  return (
    <div className="mt-3 space-y-2">
      <div className="flex flex-wrap items-baseline gap-x-2 text-sm text-[var(--text-primary)]">
        <span className="text-[var(--text-secondary)]">Payer:</span>
        <span className="font-medium">{payer.name ?? "?"}</span>
        <span className="ml-auto font-semibold text-[var(--accent-text)]">
          {fmt(bill)} đ
        </span>
      </div>
      {guests.length > 0 && (
        <p className="text-xs text-[var(--text-secondary)]">
          incl. {guests.length} guest(s) paying cash: {guests.join(", ")}
        </p>
      )}
      {attachments.dish && (
        <p className="text-xs text-[var(--text-secondary)]">Dish: {attachments.dish}</p>
      )}
      {shares.length > 0 && (
        <ul className="divide-y divide-[var(--border)] rounded-lg border border-[var(--border)] bg-[var(--bg-base)]">
          {shares.map((s, i) => (
            <li
              key={i}
              className="flex items-center justify-between px-3 py-2 text-sm"
            >
              <span className="text-[var(--text-primary)]">{s.name}</span>
              <span className="font-medium text-[var(--text-secondary)]">
                {fmt(s.amount)} đ
              </span>
            </li>
          ))}
        </ul>
      )}
      <BalanceTable rows={attachments.balances ?? []} />
    </div>
  );
}

export function BotMessage({ body, attachments }: BotMessageProps) {
  const type = attachments?.type;
  return (
    <div className="max-w-[85%] rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] px-4 py-3 shadow-sm">
      <div className="prose-chat text-sm leading-relaxed text-[var(--text-primary)] [&_a]:text-[var(--accent-text)] [&_a]:underline [&_code]:rounded [&_code]:bg-[var(--bg-base)] [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[0.85em] [&_h1]:mb-2 [&_h1]:mt-1 [&_h1]:text-base [&_h1]:font-semibold [&_h2]:mb-2 [&_h2]:mt-1 [&_h2]:text-sm [&_h2]:font-semibold [&_li]:my-0.5 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-2 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0 [&_table]:my-2 [&_table]:w-full [&_table]:border-collapse [&_td]:border [&_td]:border-[var(--border)] [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:border-[var(--border)] [&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
      </div>
      {type === "settlement" && <SettlementCard attachments={attachments} />}
      {type === "meal" && <MealCard attachments={attachments} />}
    </div>
  );
}
