---
name: balances
description: Xem ai nợ ai và tính ai trả cho ai — "tôi nợ ai", "how much do I owe", "summary", "current state", "ai trả tuần này", "chốt", "reset".
---
# Xem nợ / tóm tắt / tạm tính

KHÔNG có "số dư" hay "ròng"/"net" trong app này. Không cộng chiều nợ với chiều được nợ
thành một con số, không nói "cân bằng ±X". Chỉ có hai danh sách: **bạn nợ ai** và **ai nợ
bạn**, từng người từng bữa. Công cụ cũng không trả về `net` — không có gì để gõ lại.

Chọn đúng công cụ theo câu hỏi:
- Ngôi thứ nhất, hỏi về mình ('tôi nợ bao nhiêu', 'nợ ai', 'nợ buổi nào', 'how much do I owe', 'my part') → `member_statement` (mặc định = người nhắn). KHÔNG hiện cả nhóm.
- Tóm tắt / trạng thái nhóm ('summary', 'current state', 'tổng kết', 'cả nhóm thế nào') → `get_period_summary`. Nó trả về `outstanding`: từng dòng "X nợ Y bao nhiêu", hai chiều để riêng.
- Ai trả cho ai / tạo QR ('ai trả tuần này', 'tạo QR', 'chốt', 'reset') → `settle_period`.
  Cùng nhóm này: **'tính tiền'**, 'tính toán đi', 'settle', **'còn ai nợ ai gì không'**,
  'còn nợ gì không', và **'tôi phải trả bao nhiêu' / 'tôi phải chuyển cho ai'** —
  người hỏi muốn DANH SÁCH TRẢ TIỀN (kèm QR), không phải bảng kê. `get_period_summary`
  ở đây là sai: nó liệt kê hai chiều chưa bù trừ, nên "A nợ B 100k" và "B nợ A 100k"
  cùng hiện ra và không ai biết phải chuyển bao nhiêu.
  Phân biệt với dòng trên: 'tôi nợ **ai**', 'nợ buổi nào' = hỏi cho biết → `member_statement`;
  'tôi phải **trả** bao nhiêu' = hỏi để chuyển tiền → `settle_period`.
  `settle_period` CHỈ TẠM TÍNH: nó không ghi gì, không đóng kỳ, không reset. Nếu người dùng
  muốn 'chốt'/'reset' kỳ, đưa số tạm tính rồi nói rõ: hiện chưa có tính năng đóng kỳ, mọi
  khoản vẫn được tính từ đầu sổ. ĐỪNG nói là đã chốt/đã reset.
  Đây là chỗ DUY NHẤT được gộp hai chiều A↔B — và chỉ để ra một mã QR trả tiền.
- Không có mốc thời gian rõ → mặc định 'since_last'.
- Nếu còn đề xuất chưa xác nhận, `settle_period` báo `settle_blocked` — nhắc xác nhận/huỷ trước.
