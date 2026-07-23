---
name: record-payment
description: Ghi khi một người trả tiền mặt cho người khác — "A trả B", "A đã trả", "trả hết rồi".
---
# Ghi trả tiền mặt

Dùng `propose_payment` (KHÔNG dùng `propose_meal`). Nó chỉ ĐỀ XUẤT — người dùng xác nhận trên thẻ.

- `from` = người trả (bỏ trống = người đang nhắn), `to` = người nhận.
- Có số tiền cụ thể ('A trả B 100k') → truyền `amount` (VND).
- KHÔNG có số tiền ('A đã trả B', 'trả hết rồi') → BỎ TRỐNG `amount`; công cụ tính đúng số A đang nợ B (gộp theo từng bữa). ĐỪNG tự đoán số.
- Nếu công cụ trả về `payment_ambiguous` (hai người nợ nhau CẢ HAI CHIỀU): HỎI lại người dùng — trả trọn số `gross` hay chỉ cấn trừ phần chênh `offset` — rồi gọi lại `propose_payment` với `mode:"gross"` hoặc `mode:"offset"` (đừng tự gõ số).
- `payment_settled` = thật sự không còn nợ → báo lại, không tạo thẻ.
- `nothing_owed` = người trả không nợ người kia (mà ngược lại) → giải thích, không tạo thẻ.
- Nhiều người trả trong một câu → gọi `propose_payment` MỘT LẦN CHO MỖI người.
