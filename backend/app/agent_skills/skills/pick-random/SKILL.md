---
name: pick-random
description: Chọn ngẫu nhiên một người trong nhóm — "bốc thăm ai trả", "random một người", "chọn đại ai đi mua đồ ăn".
---
# Bốc thăm một người

- `pick_random` để CÔNG CỤ tự bốc ngẫu nhiên một thành viên. TUYỆT ĐỐI không tự chọn người — bạn không thể random thật, và kết quả phải do công cụ quyết định.
- Bốc trong các thành viên "default_participant" (thành viên thường xuyên tham gia) của nhóm — không nhận danh sách giới hạn/loại trừ theo từng lần bốc. Nếu người dùng đòi 'trừ ai đó' hay 'chỉ trong A, B, C' cho MỘT lần bốc, giải thích rằng việc đó không làm theo từng lần được — muốn loại ai vĩnh viễn khỏi các LƯỢT BỐC thì dùng `update_member` với `default_participant:false`.
- `default_participant:false` **chỉ ảnh hưởng tới `pick_random`**. Nó KHÔNG loại ai khỏi
  "cả nhóm" khi chia tiền: `find_members all_active:true` luôn trả về toàn bộ phòng. Muốn
  ai đó không phải trả một bữa thì bỏ họ khỏi `participants` của bữa đó.
- "Tôi ngồi ngoài" / "lượt này không tính tôi" / "bốc lại" = BỐC LẠI (`pick_random`) —
  đó là chuyện của MỘT lượt. TUYỆT ĐỐI không gọi `update_member` để đổi
  `default_participant`: đó là thay đổi lâu dài cho MỌI lượt bốc sau này, và người dùng
  không yêu cầu điều đó. Chỉ đổi khi họ nói rõ là muốn *từ nay* không bốc nữa.
- Chỉ bốc khi người dùng NÓI RÕ là muốn bốc: 'bốc thăm', 'random', 'roll', 'chọn đại',
  'ai rót trà'. "Hôm nay ai trả tiền?" / "ai trả tuần này?" là câu HỎI VỀ SỔ (ai đã trả)
  → dùng `get_period_summary`/`settle_period`, TUYỆT ĐỐI không bốc thăm. Bốc thăm khi
  người ta chỉ hỏi thông tin là tự dựng ra một nghĩa vụ trả tiền.
- Bốc để làm gì ('trả tiền', 'đi mua đồ ăn') → truyền nguyên văn vào `label`.
- Thẻ kết quả đã hiện tên người được chọn; trả lời ngắn gọn, ĐỪNG gõ lại tên (gõ lại là cách duy nhất làm sai một kết quả đúng).
