---
name: pick-random
description: Chọn ngẫu nhiên một người trong nhóm — "bốc thăm ai trả", "random một người", "chọn đại ai đi mua đồ ăn".
---
# Bốc thăm một người

- `pick_random` để CÔNG CỤ tự bốc ngẫu nhiên một thành viên. TUYỆT ĐỐI không tự chọn người — bạn không thể random thật, và kết quả phải do công cụ quyết định.
- Mặc định bốc trong tất cả thành viên đang hoạt động ('cả nhóm').
  - 'trừ An' → `exclude_ids` (dùng `find_members` lấy id nếu cần).
  - 'trong A, B, C' → `candidate_ids`.
- Bốc để làm gì ('trả tiền', 'đi mua đồ ăn') → truyền nguyên văn vào `label`.
- Thẻ kết quả đã hiện tên người được chọn; trả lời ngắn gọn, ĐỪNG gõ lại tên (gõ lại là cách duy nhất làm sai một kết quả đúng).
