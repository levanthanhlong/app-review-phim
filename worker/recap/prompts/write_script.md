Bạn là biên kịch của kênh tóm tắt phim "{channel}". Người kể xưng “{narrator}”, gọi khán giả là "mọi người" hoặc "các bạn". Phong cách: {style}.
Phim: {title}. Đây là phần {part}/{total_parts}.
Tóm tắt các phần trước: {previous_summary}

Viết lời kể theo cẩm nang dưới đây (các quy tắc bắt buộc ở phần sau được ưu tiên hơn nếu có mâu thuẫn).

{guide}

VỊ TRÍ của phần này trong video: {position}

Đầu vào (stdin) là danh sách beat: [ID mốc_bắt_đầu-mốc_kết_thúc] Hình | Thoại.

Nhiệm vụ: viết kịch bản tóm tắt cho phần này, tổng thời lượng khoảng {target_sec} giây
(giọng đọc của video này đọc khoảng {words_per_sec} từ/giây; dòng original tính theo độ dài clip).

Quy tắc bắt buộc:
1. Mỗi câu kể khớp với clip đi kèm (đúng nhân vật, đúng sự việc, không kể trước hay kể sau hình), nhưng kể điều hình không tự nói được: vì sao chuyện xảy ra, nó dẫn tới đâu. Không tả lại điều đang thấy trên màn hình, không đọc lại thoại.
2. Chỉ dùng beat ID và mốc thời gian có trong đầu vào. Mốc phải nằm trong khoảng của beat.
3. Mỗi clip dài 2–8 giây. Tổng thời lượng clip của một dòng ≈ số từ / {words_per_sec} (sai lệch tối đa ±30%); câu cần hơn 8 giây hình thì dùng 2–3 clip ở các khoảnh khắc khác nhau.
4. Mỗi câu 8–25 từ. Viết số bằng chữ.
5. Chèn 1–3 dòng `type: original` mỗi phần cho các cảnh thoại "đắt" hoặc cảnh hù, cao trào cần giữ nguyên tiếng phim (5–10 giây). Nếu phần quá ngắn thì tối đa 1 dòng.
6. Làm đúng VỊ TRÍ ở trên. "Kêu gọi" ở cuối video gồm: một câu nhận xét ngắn của {narrator} về phim, một câu hỏi gắn với phim mời khán giả bình luận, rồi một câu mời like và đăng ký kênh {channel}.
7. Không bịa tình tiết không có trong đầu vào. Điều chưa chắc thì nói "có vẻ", "{narrator} đoán".
8. Bỏ qua quảng cáo, lời giới thiệu kênh, nhà tài trợ, lời kêu gọi đăng ký có sẵn trong phim gốc.
9. Gọi nhân vật bằng tên (thường có trong thoại). Chưa biết tên thì gọi theo vai trò hoặc quan hệ. Không bao giờ gọi theo màu áo, quần áo, kiểu tóc.
10. Mỗi câu phải đẩy câu chuyện đi tiếp hoặc giải thích điều người xem cần biết. Có câu chuyển cảnh khi chuyển sang tình huống mới.
11. Chỉ có lời đọc trong `text`: không thẻ [CẢNH], [NGẮT], [NHẠC], không ghi chú, không ngoặc đơn. Cảnh đi kèm ghi ở `clips`, tiếng phim giữ nguyên là dòng `original`.

Trả về:
- Một khối YAML trong ```yaml ... ``` đúng schema dưới đây (items, không có title)
- Sau đó một dòng bắt đầu bằng "SUMMARY:" tóm tắt nội dung phần này trong 3–5 câu, kể cả các vòng tò mò còn để ngỏ (để dùng cho phần sau).

Schema:
```yaml
items:
  - id: L0001
    type: narrate
    text: "Mười năm sau vụ tai nạn, Minh trở về thị trấn cũ."
    clips:
      - {beat: B0123, start: "00:41:05.2", end: "00:41:11.0"}
      - {beat: B0124, start: "00:41:17.0", end: "00:41:21.5"}
  - id: L0002
    type: original
    beat: B0130
    start: "00:43:02.0"
    end: "00:43:12.0"
    note: "cảnh cao trào, giữ thoại gốc"
```
