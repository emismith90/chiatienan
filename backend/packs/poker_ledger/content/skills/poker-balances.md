---
name: poker-balances
description: Ai nợ ai sau các ván, tạm tính và QR — "ai trả ai", "tôi nợ bao nhiêu", "chốt", "lịch sử ván".
---
# Xem nợ / tạm tính / lịch sử

- Ngôi thứ nhất ('tôi nợ ai', 'tôi được bao nhiêu') → `member_statement` (mặc định = người nhắn). Hai danh sách: bạn nợ ai / ai nợ bạn, từng ván — KHÔNG có một con số "ròng".
- Ai trả cho ai, tạo QR, 'chốt', 'tính tiền' → `settle_period`. Nó CHỈ TẠM TÍNH và không đóng kỳ; nói rõ vậy nếu người dùng muốn 'reset'.
- Tổng kết chung → `get_period_summary`; danh sách các ván với lời/lỗ từng người → `game_history`.
- Có thẻ ván chưa xác nhận thì `settle_period` bị chặn: xin bàn bấm Confirm trên thẻ, hoặc huỷ bằng `cancel_draft` nếu họ bảo bỏ.
- Trả tiền mặt cho nhau ('A trả B rồi') → `propose_payment` (bỏ trống `amount` để công cụ tính đúng số A nợ B).
