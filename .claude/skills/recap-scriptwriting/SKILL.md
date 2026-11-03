---
name: recap-scriptwriting
description: Viết, sửa, chấm và viết lại kịch bản tóm tắt phim tiếng Việt (script.yaml của RecapStudio) theo giọng kênh review phim YouTube Việt Nam — lời kể tự nhiên như người thật, có hook, nhịp kể, khớp hình. Dùng khi người dùng nhờ viết hay hơn, sửa câu cứng/máy, review kịch bản, viết lại một dòng, hoặc chỉnh prompt viết kịch bản của worker (prompts/write_script.md, prompts/narration_guide.md).
---

# Viết kịch bản tóm tắt phim (RecapStudio)

## Nguồn kiến thức duy nhất

Đọc **`worker/recap/prompts/narration_guide.md`** trước khi làm bất cứ việc gì. Đây là cẩm nang lời kể,
cũng chính là đoạn stage `script` chèn vào prompt gửi cho `claude -p`. Muốn đổi phong cách viết thì
sửa file đó (không chép nội dung sang chỗ khác). Sửa xong, stage `script` sẽ tự viết lại kịch bản ở
lần chạy sau (fingerprint có hash của cẩm nang).

Các quy tắc bắt buộc và schema YAML nằm ở `worker/recap/prompts/write_script.md`.
Validate kịch bản: `worker/recap/script_model.py` (`validate_items`, `load_script`).

## Khi sửa hoặc viết lại `script.yaml` của một dự án

Dự án nằm ở `<projects_root>/<Tên>.recap/`. Các file cần đọc:

- `script.yaml` — kịch bản (mỗi dòng `narrate` có `text` + `clips`, dòng `original` giữ thoại gốc).
- `work/scenes_described.json` — beat: `id`, `start`, `end`, `desc` (mô tả hình), `dialogue` (thoại).
- `work/timeline.json` — nếu đã assemble: `fit` = `short` nghĩa là câu dài hơn hình, `trimmed` là hình dư.

Ràng buộc phải giữ khi sửa:

1. Giữ nguyên `id`, `type`, beat id và mốc thời gian, trừ khi người dùng yêu cầu đổi clip. Mốc mới
   phải nằm trong `[start, end]` của beat.
2. Độ dài câu ≈ tổng thời lượng clip × `words_per_sec` (ghi trong `script.yaml`), lệch tối đa ±30%.
   Mỗi câu 8–25 từ, số viết bằng chữ.
3. Không bịa tình tiết ngoài `desc` và `dialogue` của các beat.
4. Mốc thời gian luôn để trong dấu ngoặc kép (`"00:41:05.2"`), nếu không YAML sẽ đọc thành số.

Quy trình:

1. Đọc cẩm nang, đọc kịch bản và các beat liên quan (khi viết lại một câu, đọc thêm 3 câu trước và 3 câu sau).
2. Chỉ ra cụ thể câu nào cứng/máy/lặp và vì sao (dựa vào bảng "Tránh / Nên viết" của cẩm nang).
3. Viết lại, rồi kiểm tra theo mục 6 của cẩm nang và đếm từ so với thời lượng clip.
4. Ghi `script.yaml`, rồi kiểm tra bằng:
   `cd worker && .venv/bin/python -c "from pathlib import Path; from recap.script_model import load_script; load_script(Path('<dự án>/script.yaml')); print('ok')"`
5. Sau khi sửa, chạy lại từ `tts`: `cd worker && .venv/bin/python -m recap run --project <dự án> --from tts`
   (chỉ những câu đã đổi mới phải tạo lại giọng đọc và render lại).
