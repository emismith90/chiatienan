---
name: settle-period
description: Xem ai nợ ai và chốt tiền — "ai trả tuần này", "chốt tuần này", "số dư", "reset".
---
# Chốt / xem số dư

- `settle_period` làm tất cả: tính số dư → gộp chuyển khoản → tạo mã QR.
- Xem trước: `commit:false`. Chốt (đóng kỳ): `commit:true` — CHỈ khi người dùng nói rõ 'chốt'/'reset'.
- Không có mốc thời gian rõ → keyword mặc định 'since_last'.
- Chỉ hiển thị chi tiêu ('tháng này tiêu bao nhiêu') → `resolve_period` rồi `get_period_balances`.
- Nếu còn đề xuất chưa xác nhận, công cụ báo `settle_blocked` — nhắc người dùng xác nhận/huỷ trước.
