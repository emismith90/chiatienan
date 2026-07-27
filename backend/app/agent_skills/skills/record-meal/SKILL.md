---
name: record-meal
description: Ghi một bữa ăn nhóm — "840k cả nhóm trừ An", "bún bò 300k 5 người", ai ăn nấy trả theo hoá đơn, có khách, có điều chỉnh.
---
# Ghi một bữa ăn

1. `find_members` để xác định người trả + người tham gia (`all_active:true` cho 'cả nhóm').
2. `propose_meal` với payer, participants (id), total (tổng hoá đơn), và `items` HOẶC `adjustments`,
   cùng guests/dish/initiator/note nếu có.
   - 'trừ An' = An KHÔNG nằm trong participants.
   - 'An trả nhưng không ăn' = An là payer nhưng không nằm trong participants.
   - 'Bình +50k' = adjustment {member: <id Bình>, amount: 50000}.
   - `propose_meal` CHỈ ĐỀ XUẤT — người dùng xác nhận trên thẻ nháp.
- Sửa/xoá: `void_meal` để xoá; sửa thì void rồi `propose_meal` lại.
- Ngày: nếu người dùng nói rõ một ngày ('thứ 2', 'hôm qua', '20/7'), truyền nguyên văn vào `day_word` của `propose_meal` — công cụ tự tính ngày (giờ VN), TUYỆT ĐỐI không tự suy ra ngày. Không nói ngày → bỏ trống (mặc định hôm nay).

## Ai ăn nấy trả (ghi theo món) — dùng `items`

Khi người dùng nói ai ăn món gì ("emi ăn bò, nhím gà, linh với kun cơm tấm"), hoặc hỏi
"ghi theo từng người được không" → **dùng `items`**, KHÔNG chia đều và KHÔNG nhét thông tin
đó vào `note`.

- Mỗi participant đúng MỘT dòng `{member, amount, label}`; `amount` là **giá trên hoá đơn**.
- Một dòng "2x cơm tấm 138.000đ" cho Linh và Kun → mỗi người 69.000đ.
- **Σ items không cần bằng `total`.** Giảm giá / phí ship / phí dịch vụ là chuyện bình thường —
  công cụ tự chia phần chênh lệch theo tỉ lệ món. ĐỪNG tự tính "số sau giảm", đừng bắt người
  dùng tính hộ, và đừng bỏ cuộc vì Σ items > total.
- `total` luôn là số tiền **thực trả** (người dùng nói, hoặc dòng tổng cuối hoá đơn).
- Chưa hỗ trợ ghi theo món khi có khách lẻ (guests) — khi đó chia đều.

## Hoá đơn bằng ảnh

- Ảnh hoá đơn trong ngữ cảnh lượt này (kể cả người dùng dán ở tin nhắn ngay trước rồi mới
  `@bot`) là dùng được — **đọc luôn**, đừng hỏi lại thứ đã có trong ảnh.
- Đọc từ ảnh: tổng thực trả → `total`; giá từng dòng → `items` (nhớ nhân số lượng, và giá
  đã gạch ngang là giá gốc — lấy giá đang áp dụng).
- Trong lịch sử hội thoại, `[ảnh: N]` nghĩa là tin nhắn đó có ảnh. Nếu cần ảnh mà lượt này
  không thấy, hỏi người dùng gửi lại **một lần** — đừng hỏi lại nữa.

## Hỏi lại — tối đa một lần

Chỉ hỏi khi thiếu thứ KHÔNG thể suy ra: ai trả, hoặc tổng tiền khi không có hoá đơn.

- Đã có đủ để đề xuất → gọi `propose_meal` ngay. Thẻ nháp sửa được, nên đề xuất tốt hơn hỏi.
- KHÔNG hỏi lại thông tin người dùng đã nói ở tin nhắn trước trong lượt/lịch sử này.
- KHÔNG hỏi giá từng món khi có hoá đơn — đọc từ hoá đơn.
- KHÔNG hỏi cùng một câu hai lần. Nếu lần trước đã hỏi mà vẫn thiếu, chọn cách hợp lý nhất,
  đề xuất, và nói rõ mình đã giả định gì.
