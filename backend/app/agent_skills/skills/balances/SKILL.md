---
name: balances
description: Xem số dư và chốt tiền — "tôi nợ ai", "how much do I owe", "summary", "current state", "ai trả tuần này", "chốt", "reset".
---
# Xem số dư / tóm tắt / chốt

Chọn đúng công cụ theo câu hỏi:
- Ngôi thứ nhất, hỏi về mình ('tôi nợ bao nhiêu', 'nợ ai', 'nợ buổi nào', 'how much do I owe', 'my part') → `member_statement` (mặc định = người nhắn). KHÔNG hiện cả nhóm.
- Tóm tắt / trạng thái nhóm ('summary', 'current state', 'tổng kết', 'cả nhóm thế nào') → `get_period_summary`.
- Chốt / tạo QR ('ai trả tuần này', 'tạo QR', 'chốt', 'reset') → `settle_period`. `commit:false` để xem trước; `commit:true` CHỈ khi người dùng nói rõ 'chốt'/'reset'.
- Không có mốc thời gian rõ → mặc định 'since_last'.
- Nếu còn đề xuất chưa xác nhận, `settle_period` báo `settle_blocked` — nhắc xác nhận/huỷ trước.
