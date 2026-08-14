import type { KnowledgeData } from "@/lib/api";

/** One room's knowledge, as the server sends it. Shared by the panel tests so a
 * shape change fails in one place rather than five. */
export const knowledge: KnowledgeData = {
  etags: { observations: "obs-etag-1", memory: "mem-etag-1" },
  places: [
    {
      id: 1, slug: "quan-be-bu", name: "Quán Bé Bự",
      aliases: ["bé bự"], tags: ["cơm"], delivery: ["grab"],
      address: "12 Ngõ Nhỏ", phone: "0912345678",
      walkable: true, walk_minutes: null, price_hint: null,
      closed_until: null, active: true, untried: false, note_count: 2,
      stats: {
        times: 4, last_on: "2026-08-02", days_since: 12, avg_per_head: 55000,
        band: "vừa", weekday_counts: { "0": 0, "1": 0, "2": 0, "3": 3, "4": 1, "5": 0, "6": 0 },
      },
    },
    {
      id: 2, slug: "com-ga-thinh-lo", name: "Cơm gà Thịnh Lơ",
      aliases: [], tags: ["chưa-thử"], delivery: [],
      address: null, phone: null,
      walkable: false, walk_minutes: null, price_hint: 40000,
      closed_until: "2026-08-20", active: true, untried: true, note_count: 0,
      stats: {
        times: 0, last_on: null, days_since: null, avg_per_head: 40000,
        band: "rẻ", weekday_counts: {},
      },
    },
  ],
  observations: [
    {
      id: "aaa111", when: null, standing: true,
      subject: "place:quan-be-bu", subject_key: "place:quan-be-bu",
      subject_kind: "place", subject_label: "Quán Bé Bự", subject_id: 1,
      gate: "order-by@11:30", gate_kind: "order-by", gate_label: "Order by 11:30",
      text: "Phải gọi trước — quán làm chậm.", stale: false,
    },
    {
      id: "bbb222", when: "2026-08-10", standing: false,
      subject: "place:quan-be-bu", subject_key: "place:quan-be-bu",
      subject_kind: "place", subject_label: "Quán Bé Bự", subject_id: 1,
      gate: null, gate_kind: null, gate_label: null,
      text: "Hôm nay hết gà.", stale: false,
    },
    {
      id: "ccc333", when: "2025-01-05", standing: false,
      subject: "member:nhim", subject_key: "member:nhim",
      subject_kind: "member", subject_label: "Giang Hoàng", subject_id: 9,
      gate: null, gate_kind: null, gate_label: null,
      text: "Đề xuất quán rồi lại đổi ý.", stale: true,
    },
  ],
  memory: {
    sections: [
      { index: 0, title: "Tóm tắt", date: "2026-07-01", body: "- Ăn bún chả\n- Linh trả" },
      { index: 1, title: "Tóm tắt", date: "2026-08-01", body: "- Ăn cơm gà" },
    ],
    watermark: { through_id: 42, through_at: "2026-08-01T12:00:00" },
  },
  subjects: [
    { subject: "place:quan-be-bu", label: "Quán Bé Bự", kind: "place", id: 1 },
    { subject: "member:nhim", label: "Giang Hoàng", kind: "member", id: 9 },
  ],
};
