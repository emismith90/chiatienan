---
name: record-game
description: Ghi một ván / một tối chơi bài — "tối qua chơi", "ghi ván", "kết bàn": mỗi người mua bao nhiêu, đổi ra bao nhiêu.
---
# Ghi ván bài

Dùng `propose_game` (chỉ ĐỀ XUẤT — bàn xác nhận trên thẻ). Một lần gọi cho cả ván.

- Mỗi người chơi MỘT dòng trong `entries`: `member` (id từ `find_members`), `buy_in` (tổng chip đã mua), `cash_out` (chip đổi ra lúc kết). Số nguyên VND ('500k' → 500000).
- Rake / tip cho bàn → `house`. Không có thì bỏ trống.
- Bàn phải cân: Σ buy_in = Σ cash_out + house. Công cụ báo lệch (`error` kèm số lệch) → HỎI lại: ai ghi thiếu/dư, hay phần lệch là tiền bàn. KHÔNG tự sửa một con số cho cân, KHÔNG tự tính lời lỗ.
- Ngày ('tối qua', 'thứ 6') → `day_word`, để công cụ tính ngày.
- Ai thắng ai thua, ai trả ai bao nhiêu: công cụ tính và thẻ hiện; bạn không nhắc lại số.
- Ghi sai ván đã xác nhận → `void_game` với `game_id`, rồi đề xuất lại.
