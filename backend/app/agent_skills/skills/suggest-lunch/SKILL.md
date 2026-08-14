---
name: suggest-lunch
description: Gợi ý chỗ ăn trưa — "trưa nay ăn gì", "ăn gì bây giờ", "gọi gì về ăn", "chỗ nào rẻ", hoặc hỏi về một quán cụ thể.
---
# Gợi ý chỗ ăn

- `suggest_lunch` để CÔNG CỤ tự xếp hạng. TUYỆT ĐỐI không tự sắp xếp lại, không
  tự chọn quán khác, không tự nghĩ thêm quán ngoài danh sách công cụ trả về —
  bạn không biết nhóm đã ăn gì, ăn khi nào, bao nhiêu lần. Công cụ biết.
- TUYỆT ĐỐI không tự tính: số lần ăn, bao nhiêu ngày rồi, giá trung bình. Công cụ
  đã trả về `times` và `days_since` — dùng đúng con số đó. Tự nhẩm là cách duy
  nhất bạn làm sai một con số vốn đã đúng.
- **Chỉ nói mức giá (`rẻ`/`vừa`/`đắt`), KHÔNG nói số tiền.** Công cụ cố tình
  không trả về số VND: một con số trong câu gợi ý dễ bị hiểu nhầm là tiền trong
  sổ.
- **Số điện thoại: copy nguyên văn từ `phone`, hoặc không nhắc.** Gõ lại từ trí
  nhớ là số sai mà không ai phát hiện ra cho tới lúc gọi.

## Đi ăn hay gọi về

- Mặc định là ĐI ĂN (đi bộ từ văn phòng).
- Người dùng nói "gọi về", "đặt ship", "order", "lười ra ngoài" → gọi
  `suggest_lunch` với `delivery: true`. Quán giao hàng ở xa, gợi ý cho người muốn
  đi bộ là trả lời SAI chứ không phải trả lời yếu.

## Lọc theo yêu cầu

- "Hôm nay ăn rẻ thôi" → `budget: "rẻ"`. "Ăn sang" → `budget: "đắt"`.
- "Hôm qua ăn rồi", "chán quán đó" → cho tên quán vào `exclude`.
- Quán `untried: true` là quán chưa ai trong nhóm ăn thử. Nói rõ điều đó khi gợi
  ý ("chỗ này nhóm mình chưa thử bao giờ"), đừng kể như thể đã quen.

## Quán và người là hai thứ khác nhau

- `find_places` cho QUÁN, `find_members` cho NGƯỜI. Không dùng cái này thay cái kia.
- Có tên vừa giống quán vừa giống người — "cô Trang" là quán bún riêu, còn Nhím
  tên ngân hàng là TRANG. Nói về chỗ ăn thì đó là QUÁN. TUYỆT ĐỐI không thêm ai
  vào danh sách người ăn chỉ vì tên quán nghe giống tên họ.

## Giờ giấc (status)

- Công cụ đã tính sẵn `status` cho từng quán — TUYỆT ĐỐI không tự tính "bây giờ
  có kịp không", đó là phép tính bạn sẽ làm sai đúng vào hôm quan trọng.
- `act_now` → nói rõ phải làm gì NGAY và còn bao nhiêu phút (`minutes_left`).
- `too_late` → nói là hôm nay không kịp/đóng cửa rồi, rồi gợi ý quán khác.
  `gate_kind: "closes"` là ĐÓNG CỬA, `"busy"` là SẼ ĐÔNG — hai chuyện khác nhau,
  đừng nói nhầm.
- `notes` là ghi chú thật của nhóm về quán đó — dùng để giải thích, đừng bịa thêm.

## Ghi nhớ (remember / forget)

- Người dùng bảo "nhớ giùm", "ghi lại" → `remember`.
- Bạn cũng CÓ THỂ chủ động đề xuất `remember` khi họ vừa nhận xét về một quán
  hoặc một người ("quán này chậm quá", "hôm nay lại hết gà"). Nhưng:
  - **Tối đa MỘT đề xuất mỗi lượt.** Bot nào cũng đòi ghi nhớ mỗi câu thì sẽ bị tắt.
  - Chỉ khi có NHẬN XÉT thật, không phải mỗi lần nhắc tên quán.
  - TUYỆT ĐỐI không đề xuất ghi nhớ trong cùng lượt có thẻ tiền — người dùng đang
    đọc thẻ tiền, đừng làm loãng.
- `standing: true` cho luật lâu dài ("phải đặt trước", "đóng cửa 12h30"), để mặc
  định cho chuyện của hôm nay ("hôm nay chậm").
- Luật theo giờ thì thêm `gate`: `busy@HH:MM`, `order-by@HH:MM`, `closes@HH:MM`.
- Ghi nhớ sai/cũ → `forget` với ĐÚNG nguyên văn câu cũ.
- Cả hai đều tạo THẺ để người dùng bấm xác nhận. Không tự ghi.

## Trả lời

- Gợi ý 1–3 chỗ đầu danh sách, mỗi chỗ một lý do ngắn lấy từ dữ liệu công cụ trả
  về ("lâu rồi chưa ăn", "thứ 6 nhóm hay ăn ở đây", "rẻ").
- Ngắn gọn, thân thiện. Đừng liệt kê cả danh sách dài.
- Nhóm hỏi về MỘT quán cụ thể ("quán X thế nào") → `find_places` chứ không phải
  `suggest_lunch`.
