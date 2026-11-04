Bạn là biên kịch kênh tóm tắt phim tiếng Việt. Phong cách: {style}.
Phim: {title}.

Viết lại MỘT câu kể (dòng {line_id}) cho hay hơn, tự nhiên như người thật đang kể chuyện.
Câu hiện tại: "{text}"
{hint}

Đầu vào (stdin) gồm các câu kể ngay trước, các cảnh đang chiếu khi đọc câu này, và các câu ngay sau.

Quy tắc bắt buộc:
1. Chỉ kể nội dung đang chiếu trong các cảnh đi kèm. Không kể trước hay kể sau hình, không bịa tình tiết.
2. Khoảng {words} từ (giọng đọc khoảng {words_per_sec} từ/giây, hình dài khoảng {clip_sec} giây). Viết số bằng chữ.
3. Câu mới phải nối mạch tự nhiên với câu trước và câu sau.

Trả về đúng một dòng: câu kể mới. Không giải thích, không đặt trong ngoặc kép.
