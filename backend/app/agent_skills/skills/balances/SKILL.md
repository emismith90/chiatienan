---
name: balances
description: Xem số dư và tính ai trả cho ai — "tôi nợ ai", "how much do I owe", "summary", "current state", "ai trả tuần này", "chốt", "reset".
---
# Xem số dư / tóm tắt / tạm tính

Chọn đúng công cụ theo câu hỏi:
- Ngôi thứ nhất, hỏi về mình ('tôi nợ bao nhiêu', 'nợ ai', 'nợ buổi nào', 'how much do I owe', 'my part') → `member_statement` (mặc định = người nhắn). KHÔNG hiện cả nhóm.
- Tóm tắt / trạng thái nhóm ('summary', 'current state', 'tổng kết', 'cả nhóm thế nào') → `get_period_summary`.
- Ai trả cho ai / tạo QR ('ai trả tuần này', 'tạo QR', 'chốt', 'reset') → `settle_period`.
  `settle_period` CHỈ TẠM TÍNH: nó không ghi gì, không đóng kỳ, không reset. Nếu người dùng
  muốn 'chốt'/'reset' kỳ, đưa số tạm tính rồi nói rõ: hiện chưa có tính năng đóng kỳ, mọi
  khoản vẫn được tính từ đầu sổ. ĐỪNG nói là đã chốt/đã reset.
- Không có mốc thời gian rõ → mặc định 'since_last'.
- Nếu còn đề xuất chưa xác nhận, `settle_period` báo `settle_blocked` — nhắc xác nhận/huỷ trước.
