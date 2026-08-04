---
name: pick-random
description: Chọn ngẫu nhiên một người trong nhóm — "bốc thăm ai trả", "random một người", "chọn đại ai đi mua đồ ăn".
---
# Bốc thăm một người

- `pick_random` để CÔNG CỤ tự bốc ngẫu nhiên một thành viên. TUYỆT ĐỐI không tự chọn người — bạn không thể random thật, và kết quả phải do công cụ quyết định.
- Bốc trong các thành viên "default_participant" (thành viên thường xuyên tham gia) của nhóm — không nhận danh sách giới hạn/loại trừ theo từng lần bốc. Nếu người dùng đòi 'trừ ai đó' hay 'chỉ trong A, B, C' cho MỘT lần bốc, giải thích rằng việc đó không làm theo từng lần được — muốn loại ai vĩnh viễn khỏi các lượt bốc/chia tiền mặc định thì dùng `update_member` với `default_participant:false` (họ vẫn có thể được nêu tên riêng khi chia tiền cụ thể).
- Bốc để làm gì ('trả tiền', 'đi mua đồ ăn') → truyền nguyên văn vào `label`.
- Thẻ kết quả đã hiện tên người được chọn; trả lời ngắn gọn, ĐỪNG gõ lại tên (gõ lại là cách duy nhất làm sai một kết quả đúng).
