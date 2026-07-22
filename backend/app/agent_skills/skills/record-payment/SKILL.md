---
name: record-payment
description: Ghi khi một người trả tiền mặt cho người khác — "A trả B", "A đã trả", "A gửi B 100k", "trả đủ rồi".
---
# Ghi trả tiền mặt

Dùng `propose_payment` (KHÔNG dùng `propose_meal`). Nó chỉ ĐỀ XUẤT — người dùng xác nhận trên thẻ.

- `from` = người trả (bỏ trống = người đang nhắn), `to` = người nhận.
- Có số tiền cụ thể ('A trả B 100k') → truyền `amount` (VND).
- KHÔNG có số tiền ('A đã trả B', 'trả đủ rồi') → BỎ TRỐNG `amount`; công cụ tự tính đúng số A đang nợ B. ĐỪNG tự đoán số.
- Nhiều người trả trong một câu ('Dũng và Giang đã trả Linh') → gọi `propose_payment` MỘT LẦN CHO MỖI người trả.
- Nếu công cụ trả về `payment_settled` nghĩa là người đó không còn nợ — báo lại, không tạo thẻ.
