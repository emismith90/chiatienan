---
name: record-meal
description: Ghi một bữa ăn nhóm — "840k cả nhóm trừ An", "bún bò 300k 5 người", có khách, có điều chỉnh.
---
# Ghi một bữa ăn

1. `find_members` để xác định người trả + người tham gia (`all_active:true` cho 'cả nhóm').
2. `propose_meal` với payer, participants (id), total (tổng hoá đơn), adjustments, guests, và dish/initiator/note nếu có.
   - 'trừ An' = An KHÔNG nằm trong participants.
   - 'An trả nhưng không ăn' = An là payer nhưng không nằm trong participants.
   - 'Bình +50k' = adjustment {member: <id Bình>, amount: 50000}.
   - `propose_meal` CHỈ ĐỀ XUẤT — người dùng xác nhận trên thẻ nháp.
- Sửa/xoá: `void_meal` để xoá; sửa thì void rồi `propose_meal` lại.
- Có ảnh hoá đơn: đọc tổng tiền từ ảnh, dùng làm `total`. Chỉ nhận ảnh dán trực tiếp.
