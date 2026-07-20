/** An image attachment on a user turn. `data` is raw base64 (no data: prefix);
 * derive a preview URL as `data:${mimeType};base64,${data}`. `data` may be ""
 * if the raw bytes were stripped upstream — render a fallback then. */
export interface ChatImage {
  data: string;
  mimeType: string;
  name?: string;
}

/** The interactive `expense_draft` message attachment. The draft_id is the
 * carrying message's `id`. `per_head_preview` (like the client-side
 * `perHead` calc in expense-draft-card.tsx) is a PROVISIONAL estimate — the
 * server recomputes the authoritative split on commit. */
export interface ExpenseDraft {
  type: "expense_draft";
  status: "pending" | "committed" | "cancelled";
  payer_member_id: number;
  member_participants: number[];
  guests: string[];
  bill_total: number;
  adjustments: { member: number; amount: number }[];
  dish: string | null;
  initiator: string | null;
  note: string | null;
  per_head_preview: number;
}
