# SPEC: RecapStudio — App macOS tạo video tóm tắt phim bằng AI (chạy local)

> **Cách dùng file này:** tạo thư mục dự án mới, đặt file này vào với tên `SPEC.md`, mở Claude Code trong thư mục đó và gõ:
> *"Đọc SPEC.md và triển khai theo đúng thứ tự các Milestone. Làm xong mỗi milestone thì dừng lại, báo cáo kết quả kiểm tra (acceptance) rồi mới làm tiếp."*

---

## 1. Mục tiêu

- **Đầu vào:** 1 file phim đầy đủ (.mp4/.mkv/.mov), ví dụ phim dài 2 tiếng.
- **Đầu ra:**
  1. Video tóm tắt (mặc định khoảng 20% thời lượng gốc, ví dụ 2 tiếng thành khoảng 24 phút), khung ngang 16:9.
  2. Lời kể tóm tắt tiếng Việt (giọng AI) **khớp với từng cảnh** đang chiếu.
  3. Phụ đề `.srt` cho lời kể, và một bản video có phụ đề gắn cứng (tùy chọn).
  4. File kịch bản `script.yaml` để chỉnh sửa và render lại.
- **App SwiftUI** để tạo dự án, theo dõi tiến độ từng bước, xem cảnh, sửa kịch bản, xem preview và xuất file.

### Ngoài phạm vi (KHÔNG làm ở giai đoạn này)

- Đăng video lên TikTok, YouTube, Facebook; quản lý tài khoản; thống kê view, like, comment. Chỉ cần thiết kế code để sau này dễ bổ sung (mục 11).
- Phân phối app cho người khác. App chỉ dùng cá nhân trên một máy.

### Nguyên tắc

- Mọi model chạy **local và miễn phí**, trừ bước viết kịch bản dùng **Claude Code CLI** đã đăng nhập sẵn bằng gói Pro (lệnh `claude -p`).
- Mỗi bước (stage) **chạy lại được và tiếp tục được**: nếu kết quả của một bước đã có và còn hợp lệ thì bỏ qua. Lỗi ở bước nào thì chỉ chạy lại từ bước đó.
- Toàn bộ trạng thái nằm trong **thư mục dự án** (file JSON/YAML), không phụ thuộc database.

---

## 2. Môi trường

| Thành phần | Yêu cầu |
|---|---|
| Máy | macOS 14+, Apple Silicon (máy chính: 16GB RAM, ổ trong chỉ còn ~19GB trống) |
| Lưu trữ | **Mọi dữ liệu lớn đặt trên `/Volumes/ExternalSSD`** (xem bên dưới) |
| Python | 3.11+ (dùng `uv` hoặc `venv` riêng trong `worker/.venv`) |
| FFmpeg | `brew install ffmpeg` |
| Ollama | `brew install ollama` → `ollama pull qwen2.5vl:7b` |
| Claude Code CLI | Đã cài và đã đăng nhập (`claude -p "hi"` chạy được) |
| Xcode | 16+, Swift 5.10+/6, SwiftUI |

Python packages: `mlx-whisper`, `scenedetect[opencv]`, `vieneu` (VieNeu-TTS v3 Turbo, ONNX, model ~1GB trong HF cache trên SSD), `onnxruntime==1.22.1` (ghim: bản ≥1.23 từ chối file weights dạng symlink của HF cache), `edge-tts`, `pyyaml`, `pydantic`, `ollama`, `pillow`, `numpy`, `pyobjc-framework-Vision` (đọc chữ trong hình bằng macOS Vision, cho stage `inspect`), OpenCV (có sẵn qua `scenedetect[opencv]`) + `scipy` (tìm và gom mặt, cho stage `characters`). Model mặt OpenCV Zoo: YuNet (MIT) và SFace (Apache-2.0), khoảng 37MB, tự tải lần đầu về `/Volumes/ExternalSSD/cache/recap-models` (đổi bằng `RECAP_MODELS_DIR`), kiểm tra SHA-256.

Cài trên máy mới: `./setup.sh` (công cụ, worker, model, app; mọi cache và model đặt trên ExternalSSD). Tạo file cài app: `./make_dmg.sh` (mục 7.1).

### Vị trí lưu trữ (ExternalSSD)

| Dữ liệu | Đường dẫn | Cách cấu hình |
|---|---|---|
| Model Ollama (~6GB) | `/Volumes/ExternalSSD/ollama-models` | biến môi trường `OLLAMA_MODELS` (đặt trước khi chạy `ollama serve`) |
| Model Whisper/HuggingFace (~1.6GB) | `/Volumes/ExternalSSD/hf-cache` | biến môi trường `HF_HOME` (worker tự set khi chạy) |
| Thư mục các dự án (~4–5GB/phim) | `/Volumes/ExternalSSD/myapps/app-review-phim/output/` | biến môi trường `RECAP_PROJECTS_ROOT` (mặc định `output/` trong repo) |
| Code + venv worker | `/Volumes/ExternalSSD/myapps/app-review-phim/` | — |
| Cache uv/pip/Homebrew, file tạm | `/Volumes/ExternalSSD/{uv-cache,pip-cache,homebrew-cache,cache,tmp}` | `setup.sh` đặt `UV_CACHE_DIR`, `PIP_CACHE_DIR`, `HOMEBREW_CACHE`, `XDG_CACHE_HOME`, `TMPDIR` |

- `recap doctor` phải kiểm tra ExternalSSD đã được gắn (mount) và còn ≥ 10GB trống trước khi chạy pipeline; nếu chưa gắn thì báo lỗi `storage_unavailable`.
- File tạm của ffmpeg cũng ghi vào thư mục `work/` của dự án (không dùng `/tmp` trên ổ trong).

---

## 3. Kiến trúc tổng quát

```
┌──────────────────────── RecapStudio.app (SwiftUI) ─────────────────────────┐
│ ProjectList │ PipelineView │ SceneBrowser │ ScriptEditor │ Preview/Export   │
│                         PipelineRunner (Process)                            │
└──────────────┬──────────────────────────────────────────────▲──────────────┘
               │ chạy: worker/.venv/bin/python -m recap <stage> │ stdout: JSON Lines
               ▼                                               │ (progress/log/result)
┌──────────────────────────── worker (Python CLI "recap") ───────────────────┐
│ ingest → transcribe → scenes → inspect → characters → describe → script     │
│ ffmpeg   mlx-whisper  PySceneDetect  Vision OCR  YuNet/SFace  Ollama  claude │
│                        → tts → music   (VieNeu)                             │
│                                  → assemble → export   (ffmpeg)             │
└────────────────────────────────────────────────────────────────────────────┘
                     đọc/ghi:  <Project>.recap/  (thư mục dự án)
```

- **Swift** chỉ lo giao diện, điều phối và đọc/ghi file. **Python** lo toàn bộ phần AI và video.
- Giao tiếp: Swift chạy lệnh CLI, Python in ra **JSON Lines** trên stdout (mục 6). Kết quả lưu thành file trong thư mục dự án.
- App **tắt App Sandbox** (cần chạy ffmpeg, python, claude, ollama).

### Cấu trúc repo

```
RecapStudio/
├── SPEC.md
├── app/                      # Xcode project SwiftUI
│   └── RecapStudio/...
├── worker/
│   ├── pyproject.toml
│   ├── recap/
│   │   ├── __main__.py       # CLI entry: python -m recap <stage> ...
│   │   ├── config.py         # đọc project.json + settings
│   │   ├── events.py         # in JSON Lines: progress/log/result/error
│   │   ├── models.py         # pydantic schema cho mọi file JSON/YAML
│   │   ├── stages/
│   │   │   ├── ingest.py
│   │   │   ├── transcribe.py
│   │   │   ├── scenes.py
│   │   │   ├── inspect.py    # OCR + luật: watermark, quảng cáo, credit, viền đen
│   │   │   ├── characters.py # mặt người → nhân vật, tên nghe được trong thoại
│   │   │   ├── describe.py
│   │   │   ├── script.py
│   │   │   ├── tts.py
│   │   │   ├── music.py      # nhạc nền AI (chỉ khi music_source = "ai")
│   │   │   ├── assemble.py
│   │   │   └── export.py
│   │   ├── llm/              # lớp provider có thể thay thế
│   │   │   ├── base.py
│   │   │   ├── claude_cli.py # mặc định
│   │   │   ├── claude_api.py # để sau
│   │   │   └── ollama.py     # dự phòng
│   │   ├── inspect_rules.py  # luật thuần của inspect (watermark, cờ, đoạn bỏ)
│   │   ├── ad_blurs.py       # quảng cáo chèn đè → vùng làm mờ có thời gian
│   │   ├── ocr.py            # macOS Vision
│   │   ├── cleanup.py        # filter ffmpeg: làm mờ watermark/quảng cáo, cắt viền đen
│   │   ├── faces.py          # YuNet + SFace (OpenCV): tìm mặt, vector đặc trưng
│   │   ├── characters.py     # gom mặt, tên người dùng đặt → tên theo từng beat
│   │   ├── character_names.py # tên riêng trong thoại (Claude), kiểm lại với thoại
│   │   ├── character_edit.py # lệnh characters-save
│   │   ├── script_revise.py  # viết lại cả kịch bản theo nhận xét
│   │   ├── beat_marks.py     # đánh dấu beat của người dùng + beat inspect tự bỏ
│   │   ├── branding.py       # banner kênh, logo, brand.json
│   │   ├── script_edit.py    # lệnh cho Script Editor / Scene Browser
│   │   └── prompts/
│   │       ├── describe_scene.md
│   │       ├── write_script.md
│   │       ├── narration_guide.md  # giọng văn kênh, gửi kèm prompt viết kịch bản
│   │       ├── rewrite_line.md
│   │       └── plan_music.md
│   └── tests/
├── samples/                  # đặt 1 clip ngắn (2–5 phút) để test
├── img/                      # ảnh kênh: "Banner dưới video…", "Ảnh đại diện…", brand.json
├── setup.sh                  # cài đặt máy mới
└── make_dmg.sh               # build Release + đóng gói dist/RecapStudio-<version>.dmg
```

### Thư mục dự án (mỗi phim một thư mục)

```
/Volumes/ExternalSSD/myapps/app-review-phim/output/<TenPhim>.recap/
├── project.json              # metadata + settings + trạng thái từng stage
├── source.mp4 -> (symlink tới file phim gốc)
├── work/
│   ├── proxy_480p.mp4        # bản nhẹ để phân tích và preview
│   ├── audio_16k.wav
│   ├── transcript.json
│   ├── scenes.json
│   ├── keyframes/B0001.jpg ...
│   ├── inspect.json          # kết quả stage inspect (mục 5.4)
│   ├── inspect/ocr.json      # cache chữ đọc được, 1 khung/giây (ghi dần, chạy tiếp được)
│   ├── characters.json       # kết quả stage characters (mục 5.5)
│   ├── characters/faces/f00001.jpg ... # ảnh mặt cho tab Nhân vật; faces.npz, names.json là cache
│   ├── character_choices.json # tên người dùng đặt: {confirmed, names, merged, ignored}
│   ├── beat_marks.json       # đánh dấu của người dùng: {beat_id: star|skip|keep}
│   ├── scenes_described.json
│   ├── digest/part_01.txt ...  # danh sách cảnh rút gọn, gửi cho LLM
│   ├── script_parts/part_01.yaml ...
│   ├── script_backups/       # script.yaml cũ (kể cả bản sửa tay) trước khi bị viết lại
│   ├── tts/<engine>_<hash>.wav ... + manifest.json
│   ├── music/                # plan.json + các phương án nhạc AI
│   ├── brand/<profile>.png   # khung banner + logo đã vẽ theo kích thước render
│   ├── render/<profile>/     # clip đã render của từng dòng timeline (cache)
│   └── timeline.json
├── script.yaml               # kịch bản hoàn chỉnh (người dùng sửa được)
├── output/
│   ├── recap_preview.mp4     # 854x480, render nhanh
│   ├── recap.mp4             # bản cuối, luôn 1920x1080
│   ├── recap_subbed.mp4
│   └── recap.srt
└── logs/<stage>.log
```

---

## 4. Các stage của pipeline

Mỗi stage: `python -m recap <stage> --project <path> [--force]`.
`python -m recap run --project <path> --from <stage> --to <stage>` để chạy liên tiếp.

Lệnh cho app, chạy trên dự án (ngoài hàng đợi job nặng; mọi thay đổi đánh dấu `stale` như `set`):
- `set --project P key=value …` (mục 7.1).
- `project-rename --title T`: đổi tên dự án, thư mục đổi theo (`<tên>.recap`); trùng tên dự án khác thì báo `project_exists`. Các bước đã chạy giữ nguyên (fingerprint không phụ thuộc đường dẫn thư mục). Kết quả: `{project}` là thư mục mới.
- `script-show` / `script-save --file EDIT.json` / `script-rewrite --line L0001 [--hint H]`: Script Editor (mục 7.2).
- `script-revise --feedback "…"`: Claude viết lại cả kịch bản theo nhận xét (chạy trong hàng đợi, mục 4.5).
- `characters-save --file NAMING.json`: lưu tên nhân vật từ tab Nhân vật (mục 4.3c). Kết quả gồm `stale` và `choices`.
- `beat-mark --beat B0001 --mark star|skip|keep|none`: Scene Browser. Kết quả gồm `marks` và `auto_skips` (beat do `inspect` tự bỏ, kèm lý do).
- `music-choose --cue M01 --option N` / `music-edit --cue M01 [--prompt P] [--alt-prompt P] [--reroll]`: nhạc AI (mục 4.7).

Lệnh phụ cho app (không cần dự án):
- `python -m recap voices` → `result` với `{voices: [{engine, id, description, gender, region, featured}], default: {engine, voice}}`. Đọc danh mục có sẵn trong package nên trả về ngay, không nạp model.
- `python -m recap voice-preview --voice V [--engine vieneu|edge] [--rate +0%] [--pitch +0Hz] [--text T] --out FILE` → tạo file wav nghe thử, `result` với `{file, duration}`. Lỗi: `voice_invalid`, `settings_invalid` (rate sai), `tts_failed`. Lần đầu mất khoảng 10s để nạp model VieNeu.

### 4.1 `ingest`

- `ffprobe` để lấy thời lượng, độ phân giải, fps, các track audio.
- Tạo `proxy_480p.mp4` (H.264, crf 28, giữ nguyên fps) và `audio_16k.wav` (mono, 16kHz).
- Ghi metadata vào `project.json`.

### 4.2 `transcribe`

- Model: `mlx-community/whisper-large-v3-turbo` (qua `mlx-whisper`), có cấu hình trong settings.
- Tham số: `language` = auto hoặc theo settings; `word_timestamps=True`.
- Tùy chọn `task=translate` nếu phim không phải tiếng Việt/Anh (để LLM dễ hiểu).
- Output `transcript.json`: `[{id, start, end, text, speaker?: null}]`.

### 4.3 `scenes`

- PySceneDetect `AdaptiveDetector` (hoặc `ContentDetector`, threshold cấu hình được) chạy trên proxy.
- **Gộp shot thành "beat"**: phim 2 tiếng có thể có 1.000–2.000 shot. Gộp các shot liền nhau cho tới khi beat dài ≥ `min_beat_sec` (mặc định 4s) và ≤ `max_beat_sec` (mặc định 20s). Ưu tiên cắt beat tại khoảng lặng của thoại.
- Mỗi beat xuất 1 keyframe (frame ở giữa beat, rộng 512px) vào `keyframes/`.
- Output `scenes.json`: `[{id, start, end, shots: [[s,e],...], keyframe, dialogue: "thoại nằm trong khoảng này"}]`.

### 4.3b `inspect` (kiểm tra nguồn)

Tìm những thứ trong phim gốc không được lọt vào video tóm tắt. Stage này **không bắt buộc**: `describe` chỉ cần `scenes` xong (dự án tạo trước khi có stage này vẫn chạy được). Thiếu `inspect.json` thì không bỏ cảnh và không làm mờ gì.

1. Lấy 1 khung/giây từ proxy, đọc chữ bằng macOS Vision (mức accurate, tiếng Việt + Anh, khoảng 30ms/khung). Kết quả cache trong `work/inspect/ocr.json`, ghi dần mỗi 100 khung, nên huỷ giữa chừng thì chạy tiếp được; đổi beat thì chỉ áp lại luật. Khung Vision không đọc được thì coi như không có chữ (cảnh báo, không dừng pipeline). Khung ảnh tạm bị xoá sau khi xong.
2. **Bản đồ điểm ảnh tĩnh** từ khoảng 150 khung thu nhỏ 240×135: điểm có độ sáng lệch ≤ 12 so với trung vị ở ≥ 70% số khung và sáng hơn 24.
3. **Luật** (`inspect_rules.py`, chỉ tính chữ có độ tin cậy ≥ 0.5):
   - **Dải phụ đề**: chữ nằm giữa khung (lệch tâm ≤ 20%), thấp ≤ 12% khung, nội dung thay đổi, xuất hiện ở ≥ 10% số khung (và ≥ 10 dòng) thì cả dải ngang đó là phụ đề gắn cứng; chữ trong dải không bị gắn cờ.
   - Chữ ở cùng chỗ trong ≥ 30% số khung là lớp phủ: nội dung giống nhau là **watermark** (vùng được nới ra theo cụm điểm tĩnh quanh chữ để phủ cả logo), nội dung thay đổi là **phụ đề gắn cứng** (để nguyên). Logo không chữ: cụm điểm tĩnh liền nhau lớn nhất ở mỗi góc (vùng 30%).
   - Chữ ngoài lớp phủ có từ khoá quảng cáo (tài trợ, nhà cái, hotline, tên miền…), credit (đạo diễn, diễn viên, tập N…), hậu trường hoặc tuyên bố miễn trừ thì gắn cờ. (Luật "thẻ tiêu đề ở đầu/cuối phim" đã bỏ: phim vietsub có phụ đề ở mọi khung nên luật này bỏ nhầm cả phút đầu.) **Cờ chỉ được tính khi có ≥ 2 cờ trong vòng 3 giây** (thẻ thật hiện vài giây; biển hiệu thoáng qua hay chữ đọc nhầm thì không).
   - Quảng cáo **chèn đè lên phim** (chữ thấp hơn 8% khung và không nằm giữa khung) thì **làm mờ đúng vùng đó trong lúc nó hiện**, giữ cảnh (`ad_blurs`). Quảng cáo dạng thẻ (chữ to hoặc ở giữa) thì bỏ cảnh.
   - Cờ trong 15% đầu phim thì bỏ từ đầu tới đó; cờ trong 25% cuối thì bỏ từ đó tới hết (thẻ mở đầu, credit, hậu trường). Beat chạm đoạn bỏ thì bị bỏ, kèm lý do.
4. **Viền đen**: `cropdetect` trên proxy (1 mẫu/2 giây). Chỉ cắt khi một kết quả chiếm đa số mẫu, giữ ≥ 50% khung, viền hai bên cân nhau (lệch ≤ 5%), và bỏ đi ≥ 5% khung. `cropdetect` không ra kết quả thì đo trung vị độ sáng theo hàng/cột của các khung thu nhỏ: viền tối (≤ 16) dày ≥ 3% mỗi bên, hai bên lệch ≤ 5%.
5. **Chất lượng nguồn**: cao ≥ 1080 là `ok`, ≥ 720 là `acceptable`, thấp hơn là `low` (cảnh báo nên tìm bản tốt hơn).

Người dùng giữ lại cảnh bị tự bỏ bằng mark `keep` ("Vẫn dùng cảnh này" trong Scene Browser); mọi mark của người dùng đều thắng cờ tự động. Đổi luật thì tăng `RULES_VERSION` để stage chạy lại (OCR vẫn dùng cache).

Đo trên Cơm Nguội tập 136 (26 phút, 1080p): 52 giây (9 giây khi dùng cache OCR); tìm đúng watermark kênh gốc, bỏ 0–10s và 1354s–hết (24/169 beat).

### 4.3c `characters` (nhân vật và tên)

Để kịch bản gọi đúng tên nhân vật. Stage này **không bắt buộc** (như `inspect`).

1. Lấy 1 khung/giây từ **phim gốc** (mặt to hơn bản 480p; mất phim gốc thì dùng proxy và cảnh báo), thu về rộng 960px. YuNet tìm mặt, chỉ giữ mặt rõ: điểm ≥ 0.9, rộng ≥ 5% khung, gần nhìn thẳng (mũi lệch ≤ 0.35 khoảng cách hai mắt). SFace tạo vector đặc trưng. Cache trong `work/characters/faces.npz` theo chữ ký video.
2. Gom mặt bằng average-linkage, khoảng cách cosine 0.65. Người thấy ở < 3 khung là vai quần chúng, không đưa ra. Mỗi nhân vật (C01 = nhiều thời lượng nhất) có danh sách beat xuất hiện và 6 ảnh mặt 160px.
3. Claude tìm **tên riêng** trong thoại (`prompts/find_names.md`), trả JSON; tên nào không có thật trong thoại thì bỏ (chữ Thái/Lào/Khmer/CJK so khớp chuỗi con, chữ khác theo ranh giới từ). Cache theo transcript + model + prompt.
4. Ghi `characters.json`. Tìm mặt lại từ đầu (video đổi) thì mã nhân vật mang nghĩa khác nên xoá tên đã đặt.

**Người dùng đặt tên** ở tab Nhân vật (mục 7.2), lưu qua `characters-save` vào `character_choices.json`: tên cho từng nhân vật (tên trong phim, tên gọi chung như "cô gái", "người mẹ", "bác sĩ", hoặc tự gõ), **giới tính** nam/nữ cho tên riêng (tên dùng trong prompt thành "Cha (nữ)"), **gộp** hai thẻ là cùng một người, **bỏ qua** vai phụ.

**Tên đi vào bước mô tả cảnh**: `describe` chạy sau `characters`; với beat có người đã đặt tên, prompt mô tả thêm "Người có mặt trong cảnh (đã được xác định, tin thông tin này hơn điều bạn đoán từ ảnh): Cha (nữ)". Lý do: model VL đoán người và giới tính từ một khung (phim Thái: nữ chính bị tả là "người đàn ông lớn tuổi" ở 21 cảnh). Chưa đặt tên thì `describe` dừng với `characters_unnamed` trước khi mô tả (đỡ phải mô tả lại). Đổi tên chỉ mô tả lại các beat có người đó (cache theo prompt từng beat); beat không có người đặt tên giữ prompt và cache cũ. "Bỏ qua, để máy tự gọi tên" (`{"skip": true}`) lưu xác nhận không có tên.

Đo trên "Thất Tình (Thái Lan)" (15 phút, 720p): 263 mặt, 8 nhân vật, 3 tên nghe được (Hiro, Bat, Cha).

### 4.4 `describe`

- Ollama, model `qwen2.5vl:7b` (cấu hình được). Với mỗi beat, gửi keyframe + thoại, nhận mô tả ngắn tiếng Việt (≤ 30 từ): ai (tên hoặc vai trò, không tả quần áo hay tóc), đang làm gì, ở đâu, cảm xúc.
- Đây là stage chậm nhất (Cơm Nguội 26 phút: khoảng 27 phút) và tốn RAM nhất (llama-server khoảng 5.6GB).
- Prompt nằm trong `prompts/describe_scene.md`.
- **Cache theo beat id**: chạy lại chỉ xử lý beat chưa có mô tả. In progress theo từng beat.
- Output `scenes_described.json` = `scenes.json` thêm trường `desc`.

### 4.5 `script` (Claude Code CLI)

1. **Tạo digest**: chia phim thành các phần khoảng 30 phút (`part_size_min`). Mỗi phần là file text gọn, mỗi dòng một beat:
   ```
   [B0123 00:41:05-00:41:17] Hình: Minh cãi nhau với bố trong bếp | Thoại: "Con không về nữa đâu"
   ```
2. **Gọi Claude cho từng phần**, tuần tự:
   ```bash
   claude -p "<nội dung prompts/write_script.md đã điền biến>" \
     --model <settings.claude_model> --output-format json < digest/part_01.txt
   ```
   - **Giọng kênh** (soạn từ skill `recap-script-vi`): `narration_guide.md` gồm nguyên tắc kể nhân quả, đọc nội tâm, bình luận mỗi 45–90 giây, vòng mở, nhịp, cầu nối văn hóa, khung cả video (hook ≤ 80 từ → mở truyện → leo thang → cao trào → kết và giải thích → nhận xét và kêu gọi), kho từ lóng (chỉ phong cách "đời thường") và checklist tự kiểm. Người kể xưng `settings.narrator` (mặc định "Mắm"), cuối video mời like và đăng ký kênh `settings.channel_name` (mặc định "Mắm Review Phim"). Mỗi phần được cho biết **vị trí** của nó: một phần thì đi trọn khung; phần đầu có hook và mở truyện; phần giữa nối và leo thang; phần cuối có cao trào, kết, nhận xét và kêu gọi. Thẻ [CẢNH]/[NGẮT]/[NHẠC] của skill không dùng: cảnh là `clips`, tiếng phim là dòng `original`. Đổi tên kênh hay cách xưng thì kịch bản thành `stale`.
   - Biến trong prompt: tên phim, phần thứ mấy/tổng số phần, **tóm tắt các phần trước** (lấy từ output phần trước), thời lượng mục tiêu cho phần này, phong cách kể (settings: nghiêm túc / hài mỉa mai / kịch tính / đời thường, hài hước, lém lỉnh), **tốc độ đọc của giọng đã chọn** (`{words_per_sec}` = tốc độ đo được × `tts_rate`; giọng chưa đo thì dùng 3).
   - Ước tính thời lượng = Σ(số từ / words_per_sec + 0.25s đệm mỗi câu kể) + Σ độ dài dòng original. Đổi giọng thì fingerprint của `script` đổi theo. `script.yaml` lưu `words_per_sec` đã dùng.
   - Không cấp tool cho Claude (chỉ nhận text). Lấy trường `result` trong JSON.
3. **Validate** YAML theo schema (mục 5.2): beat id phải tồn tại; mốc thời gian nằm trong beat; thời lượng ước tính gần mục tiêu ±15%. Nếu sai thì gọi lại **tối đa 2 lần**, kèm thông báo lỗi cụ thể.
   - Mỗi clip dưới lời kể dài 2–8 giây, mỗi đoạn `original` 5–10 giây (validate chặn clip > 8s và original > 10s). Lời kể nói nhân quả và chuyển ý, không tả lại hình hay nhắc lại thoại; kết bằng nhận xét của kênh và lời mời bình luận (`prompts/narration_guide.md`).
   - **Đánh dấu beat**: beat bị bỏ (mark `skip`, hoặc `inspect` tự bỏ mà người dùng không mark) không có trong digest nên Claude không dùng được. Beat ⭐ được gắn cờ `⭐ QUAN TRỌNG` trong digest và prompt thêm quy tắc phải dùng. Mọi beat đều bị bỏ thì báo lỗi, nói rõ bao nhiêu beat do `inspect` bỏ. Fingerprint có thêm chữ ký của các mark (chỉ khi có mark).
   - Thời lượng mục tiêu = `target_ratio` × thời lượng **cả phim** (kể cả đoạn bị bỏ).
   - **Tên nhân vật**: `characters` tìm được người mà người dùng chưa lưu (hay bỏ qua) phần đặt tên thì `script` dừng với `characters_unnamed`; banner lỗi có nút "Đặt tên nhân vật". Đã đặt tên thì mỗi dòng digest thêm `| Nhân vật: Cha, Hiro` (người có mặt trong beat), prompt liệt kê các tên và yêu cầu gọi đúng tên đó, hiểu "(nam)/(nữ)" là để xưng hô chứ không đọc ra, và khi phần "Hình" trái với dòng "Nhân vật:" thì tin dòng "Nhân vật:". Fingerprint có thêm chữ ký tên theo beat (chỉ khi có tên), nên đổi tên thì kịch bản thành `stale`.
6. **Viết lại theo nhận xét** (`script-revise`, nút "Nhận xét & viết lại" trong Script Editor): chia phần như trên, mỗi phần gửi Claude prompt của stage + nhận xét, kèm digest và các dòng hiện tại của phần đó (cả phần sửa tay). Validate và retry như stage. Chỉ ghi khi mọi phần đều xong; bản cũ vào `work/script_backups/`. Stage `script` vẫn `done` với fingerprint cũ (chỉ đổi giờ xong để app tải lại), các stage sau thành `stale`.
4. Gộp các phần thành `script.yaml`. Nếu `script.yaml` cũ khác bản mới thì lưu bản cũ vào `work/script_backups/` (giữ lại phần sửa tay).
5. Lỗi hết hạn mức hoặc lỗi CLI: dừng stage với `error.code = "llm_quota"` hoặc `"llm_failed"`, giữ nguyên các phần đã xong để chạy tiếp sau.

> Lớp `llm/base.py` định nghĩa `generate(prompt, input_text) -> str`. Mặc định dùng `ClaudeCLIProvider`. Chuyển provider trong settings mà không phải sửa code stage.

### 4.6 `tts`

- `settings.tts_engine` chọn engine:
  - `vieneu` (mặc định): VieNeu-TTS v3 Turbo, chạy offline, 25 giọng có sẵn (nam/nữ, Bắc/Trung/Nam, phong cách tự nhiên/kể chuyện/tin tức/đọc truyện). `voice` là tên giọng, ví dụ `"Hải Đăng"` hay `"Thiện Minh"`. VieNeu không có tham số tốc độ nên `tts_rate` được áp sau bằng ffmpeg `atempo` (giữ cao độ, trong khoảng −50%..+100%), còn `tts_pitch` không có tác dụng. Lỗi thì báo `tts_failed` ngay, không retry, không dùng giọng dự phòng. Model nạp một lần mỗi lần chạy stage và được giải phóng khi stage kết thúc (RAM cao nhất khoảng 1.9GB).
  - `edge`: edge-tts, `voice` là `vi-VN-NamMinhNeural` hoặc `vi-VN-HoaiMyNeural`, cần mạng. Thực tế edge-tts thỉnh thoảng trả `NoAudioReceived` với một số câu, nên retry 3 lần rồi chuyển sang `say -v Linh`.
- Giọng không hợp lệ thì báo lỗi `voice_invalid` trước khi tạo câu nào.
- Mỗi dòng `narrate` ra một file `tts/<engine>_<hash>.wav` (44.1kHz mono). Đo thời lượng, ghi vào `tts/manifest.json`.
- **Cache theo hash(engine + voice + rate + pitch + text)**: sửa 1 câu thì chỉ tạo lại câu đó. Đổi giọng thì tạo lại toàn bộ.
- Tốc độ đọc khác nhau theo giọng, từ 2.98 từ/giây (Kim Thanh) đến 4.78 từ/giây (Thùy Dung). Số đo lưu trong `recap/data/voice_rates.json`, tạo bằng `tools/measure_voice_rates.py` (8 câu chuẩn cho mỗi giọng; đo lại khi thêm giọng hoặc nâng cấp vieneu). `recap voices` trả kèm `words_per_sec`.
- Nếu `script.yaml` được viết cho tốc độ lệch hơn 15% so với giọng hiện tại thì log `warning` gợi ý chạy lại `script`.

### 4.7 `music` (nhạc nền)

- `music_source: library` (mặc định): dùng `music_file` (file trong thư mục `music/`, mỗi thư mục con một thể loại) hoặc không có nhạc. Stage này bỏ qua.
- `music_source: ai`: (1) Claude chia timeline thành các đoạn cùng cảm xúc và viết prompt nhạc tiếng Anh cho mỗi đoạn (cấm nhắc tên phim, nhân vật, nghệ sĩ; sai thì gọi lại). Kế hoạch giữ nguyên khi đầu vào không đổi, nên lựa chọn và chỉnh sửa của người dùng được giữ. (2) Mỗi đoạn tạo 3 phương án bằng Stable Audio 3 (MLX, `music_model`), dự phòng ACE-Step 1.5, có cache. (3) Ghi `output/music_provenance.json` (prompt, seed, model, giấy phép, hash file).
- Khi ghép: nhạc ở `music_volume` (0.10) dưới lời kể, `music_bed_volume` (0.25) khi không ai nói.

### 4.8 `assemble` (căn khớp + render)

**Quy tắc căn khớp** cho mỗi mục trong script, tạo `timeline.json`:

| Loại | Xử lý |
|---|---|
| `narrate` + `clips` | Độ dài đoạn = độ dài TTS + `pad` (0.25s). Lấy các clip theo thứ tự. **Clip dư** thì cắt bớt đuôi. **Clip thiếu** thì: (1) kéo dài clip cuối trong phạm vi beat của nó, (2) nối tiếp sang các shot kế tiếp trong cùng beat, (3) nếu vẫn thiếu thì làm chậm hình tối đa 0.85x, (4) vẫn thiếu thì đánh dấu `fit: "short"` để báo trên UI |
| `original` | Giữ nguyên hình và tiếng gốc, không có lời kể |

- Không đoạn phim liền nào dài quá 8 giây khi kéo dài clip để khớp lời kể (`fit.MAX_SEGMENT_SEC`).
- **Âm thanh**: khi đang có lời kể, tiếng phim ở `duck_volume` (mặc định **0 = tắt**), có fade 0.2s ở hai đầu. Đoạn `original` để âm lượng 1.0. Chuẩn hóa toàn bộ về **−14 LUFS** (mức YouTube/Facebook phát).
- **Làm sạch nguồn** (từ `inspect.json`, module `cleanup`): watermark làm mờ suốt video, quảng cáo chèn đè làm mờ trong khoảng thời gian của nó, viền đen bị cắt. Vùng tính theo tỉ lệ khung nên dùng chung cho proxy và file gốc; filter đặt ngay sau input đã seek nên thời gian tính từ đầu mỗi đoạn. Chỉ báo cáo `inspect` thay đổi phần làm sạch thì mới render lại (đổi danh sách beat bỏ thì không).
- **Banner kênh** (`brand_banner`, `brand_logo`): film co vào khung, dải banner dưới, dải phim mỏng trên, logo tròn ở góc. Tùy chỉnh trong `brand.json` đặt cạnh ảnh banner: `top_ratio` (0–0.02, mặc định 0.02), `bottom_ratio` (0.03–0.08, mặc định 0.08), `banner_align` (left/center/right), `overlay` (banner đè lên mép phim) + `overlay_opacity` (0.6), `video_fit` (`fill` = cắt phần thừa, không viền đen; `fit` = giữ cả hình), `logo_corner`, `logo_ratio` (0.07).
- **Render**: `assemble` tạo `recap_preview.mp4` (480p, nhanh, từ proxy). Bản cuối do stage `export` render từ file gốc. Mỗi dòng timeline được render thành một clip riêng trong `work/render/<profile>/` và cache theo hash, nên sửa một câu chỉ render lại câu đó (module `timeline_render`, dùng chung cho cả hai).
- Chuyển cảnh: cắt thẳng; có tùy chọn crossfade 0.2s.

### 4.9 `export`

- `recap.srt`: lấy từ timeline. Mỗi câu kể hiển thị trong đúng khoảng giọng đọc (bắt đầu từ `out_start + 0.1s`, dài bằng thời lượng TTS). Câu dài được tách thành nhiều đoạn, mỗi đoạn tối đa 2 dòng × 42 ký tự, ưu tiên ngắt sau dấu câu; thời gian chia theo số ký tự. Khoảng trống giữa hai đoạn dưới 0.5s thì được lấp để phụ đề không chớp tắt.
- `recap.mp4`: render lại timeline từ **file gốc** (đúng track âm thanh đã chọn), **luôn 1920×1080** (nguồn thấp hơn thì phóng to bằng lanczos và làm nét nhẹ `unsharp`), x264 bitrate trung bình 10 Mbps, tối đa 12 Mbps, âm thanh −14 LUFS như preview. Không ép bitrate tối thiểu: hình đơn giản (hoạt hình) tự dùng ít hơn mà vẫn nét (đo được: phim người đóng 8.8 Mbps, hoạt hình 6.6 Mbps).
- `recap_subbed.mp4`: `recap.mp4` có phụ đề gắn cứng. Bản ffmpeg của Homebrew không có libass/freetype (không có filter `subtitles` hay `drawtext`), nên mỗi đoạn phụ đề được vẽ bằng Pillow thành một ảnh PNG trong suốt (chữ trắng, viền đen, font `settings.subtitle_font`, mặc định Arial; tên font hoặc đường dẫn file). Một file `ffconcat` định thời gian cho các ảnh, rồi dùng filter `overlay` chồng lên video. Font không tồn tại thì báo `font_missing` trước khi render.
- Đã kiểm tra trên phim mẫu bằng Whisper word timestamps: từ đầu và từ cuối của mỗi đoạn lệch so với phụ đề trung vị dưới 0.25s. Chỗ lệch lớn nhất (khoảng 1.3s) nằm ở ranh giới giữa các đoạn được tách từ cùng một câu dài.
- Tùy chọn khung dọc 9:16 (crop giữa, hoặc blur nền) cho TikTok. Để mức ưu tiên thấp (chưa làm).

---

## 5. Schema dữ liệu (pydantic + Codable trong Swift)

### 5.1 `project.json`

```json
{
  "id": "uuid",
  "title": "Tên phim",
  "source_path": "/path/to/movie.mp4",
  "created_at": "ISO8601",
  "settings": {
    "target_ratio": 0.2,
    "language": "auto",
    "narration_style": "kich_tinh",   // kich_tinh | nghiem_tuc | hai_mia_mai | doi_thuong_hai
    "channel_name": "Mắm Review Phim",
    "narrator": "Mắm",
    "tts_engine": "vieneu",
    "voice": "Hải Đăng",
    "tts_rate": "+0%",
    "tts_pitch": "+0Hz",
    "vl_model": "qwen2.5vl:7b",
    "whisper_model": "mlx-community/whisper-large-v3-turbo",
    "llm_provider": "claude_cli",
    "claude_model": "sonnet",
    "whisper_task": "transcribe",
    "scene_detector": "adaptive",
    "scene_threshold": null,
    "duck_volume": 0.0,
    "music_source": "library",
    "music_file": null,
    "music_volume": 0.10,
    "music_bed_volume": 0.25,
    "music_model": "medium",
    "brand_banner": null,
    "brand_logo": null,
    "subtitle_font": "Arial",
    "min_beat_sec": 4,
    "max_beat_sec": 20,
    "part_size_min": 30,
    "audio_track": null
  },
  "stages": {
    "ingest":     {"status": "done", "started_at": "...", "finished_at": "...", "elapsed_sec": 56, "fingerprint": "...", "error": null},
    "transcribe": {"status": "running", "progress": 0.42},
    "scenes":     {"status": "pending"}
  }
}
```

Thứ tự stage: `ingest, transcribe, scenes, inspect, characters, describe, script, tts, music, assemble, export`. `status`: `pending | running | done | failed | stale`. Khi người dùng đổi một setting, đánh dấu `stale` cho các stage phụ thuộc vào setting đó, ví dụ đổi giọng thì `tts`, `assemble`, `export` thành `stale`.

### 5.2 `script.yaml`

```yaml
title: "Tên phim"
target_duration_sec: 1440
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
    end: "00:43:14.0"
    note: "cảnh cao trào, giữ thoại gốc"
```

### 5.3 `timeline.json` (do `assemble` tạo ra, UI chỉ đọc)

`[{id, out_start, out_end, tts_file, tts_dur, segments:[{src_start, src_end, speed}], fit: "ok|short|trimmed"}]`

---

### 5.4 `inspect.json` (do `inspect` tạo ra)

```json
{
  "source": {"width": 1920, "height": 1080, "quality": "ok"},
  "watermarks": [{"x": 0.04, "y": 0.80, "w": 0.33, "h": 0.20, "text": "youtube.com/kenh"}],
  "subtitles": [{"x": 0.1, "y": 0.85, "w": 0.8, "h": 0.1, "text": "mẫu phụ đề"}],
  "letterbox": {"x": 0.0, "y": 0.125, "w": 1.0, "h": 0.75},
  "ad_blurs": [{"start": 299.5, "end": 305.5, "x": 0.73, "y": 0.03, "w": 0.24, "h": 0.08, "text": "w88.bet"}],
  "skip_ranges": [{"start": 0.0, "end": 10.5, "reason": "credit: đạo diễn: …"}],
  "skip_beats": {"B0001": "credit: đạo diễn: …"}
}
```

Mọi vùng tính theo tỉ lệ khung (0–1, gốc trên trái). `letterbox` là phần giữ lại (null = không cắt).

### 5.5 `characters.json` (do `characters` tạo ra)

```json
{
  "faces_key": "<chữ ký video>|1",
  "characters": [{"id": "C01", "faces": 187, "screen_sec": 187.0, "beats": ["B0003", "B0004"],
                  "pictures": ["characters/faces/f00239.jpg"]}],
  "names": [{"name": "Hiro", "count": 1, "examples": ["Hiro... ไขยวะ"]}],
  "names_warning": null
}
```

`pictures` tính từ `work/`. `character_choices.json`: `{"confirmed": true, "names": {"C01": "Cha"}, "merged": {"C06": "C01"}, "ignored": ["C08"], "genders": {"C01": "nữ"}}`.

---

## 6. Giao thức JSON Lines (worker → app)

Mỗi dòng stdout là một JSON object:

```json
{"type":"stage_start","stage":"describe","total":742}
{"type":"progress","stage":"describe","done":120,"total":742,"message":"B0120"}
{"type":"log","level":"info","message":"..."}
{"type":"artifact","stage":"scenes","path":"work/scenes.json"}
{"type":"stage_end","stage":"describe","status":"done","elapsed_sec":1834}
{"type":"error","stage":"script","code":"llm_quota","message":"...","retryable":true}
```

- stderr dùng cho log thô, đồng thời ghi vào `logs/<stage>.log`.
- Worker bắt `SIGTERM` để dừng an toàn (hủy được từ UI), không để lại file hỏng: ghi ra file `.tmp` rồi mới rename.

---

## 7. App SwiftUI

### 7.1 Kỹ thuật

- SwiftUI, macOS 14+, kiến trúc MVVM, Swift Concurrency (`async/await`, `AsyncStream` để đọc JSON Lines).
- **Tắt App Sandbox.** Hardened Runtime bật cũng được (app không phân phối).
- `PipelineRunner` (actor): chạy `Process`, đọc stdout từng dòng, decode thành enum `WorkerEvent`, cập nhật `@Observable ProjectViewModel`. Mỗi lúc chỉ 1 job nặng; các dự án khác vào hàng đợi.
- Lưu trữ: đọc/ghi trực tiếp các file trong thư mục dự án. Danh sách dự án là thư mục `app-review-phim/output/` (cấu hình được), theo dõi bằng `FileManager` hoặc `DispatchSource`.
- Settings toàn cục (`@AppStorage`): đường dẫn `python` (venv của worker), `ffmpeg`, `claude`, `ollama`; thư mục dự án mặc định; các model mặc định. Có nút **"Kiểm tra môi trường"** chạy `recap doctor` và hiển thị ✅/❌ cho từng công cụ.

- **App chỉ đọc `project.json`.** Mọi thay đổi settings đi qua `python -m recap set --project P key=value …`. Worker kiểm tra giá trị (pydantic, giọng có tồn tại không, `tts_rate` có hợp lệ không), lưu, và đánh dấu `stale` cho stage có fingerprint thay đổi cùng mọi stage `done` phía sau. Kết quả trả về `{settings, stale}`; lỗi trả về `settings_invalid`. Stage `stale` vẫn đủ điều kiện để chạy các stage sau nó (output còn, chỉ cũ). Fingerprint của `script` chỉ đổi khi tốc độ giọng lệch quá 15% so với `words_per_sec` ghi trong `script.yaml`.
- **Unicode:** Foundation `Process` truyền tham số ở dạng NFD. Worker chuẩn hóa mọi tham số dòng lệnh về NFC trước khi xử lý (tên giọng, tên phim).
- **Mã nguồn:** `app/RecapKit` (Swift package: model `project.json`, giải mã `WorkerEvent`, `WorkerClient` chạy `Process`, đọc pipe bằng `readabilityHandler`, hủy bằng SIGTERM, `JobQueue` 1 job nặng một lúc, `ProjectRunState`, danh sách dự án, cache nghe thử giọng). `app/RecapStudio` (SwiftUI). `app/project.yml` dùng cho XcodeGen.
  - Test: `/usr/bin/xcrun swift test --package-path app/RecapKit` (gọi qua `xcrun` để dùng toolchain của Xcode, không bị swiftly che). Thêm `RECAP_INTEGRATION=1` để chạy cả các test gọi worker thật.
  - Build: `cd app && xcodegen generate && xcodebuild -project RecapStudio.xcodeproj -scheme RecapStudio -destination "platform=macOS,arch=arm64" -derivedDataPath build build`.
  - Đóng gói: `./make_dmg.sh` build bản Release và tạo `dist/RecapStudio-<MARKETING_VERSION>.dmg` (app + lối tắt Applications). Mặc định ký ad-hoc (đủ cho máy cá nhân); `--choose-cert` chọn chứng chỉ trong Keychain (bỏ chứng chỉ bị thu hồi), `--cert <SHA-1|tên>` ký thẳng. Notarize (`NOTARY_PROFILE`) chỉ với chứng chỉ Developer ID. DMG chỉ chứa app: máy cài vẫn cần repo và `./setup.sh`, đường dẫn worker đặt trong Cài đặt.
  - Các lệnh ngắn (`new`, `set`, `voices`, `voice-preview`, `doctor`) chạy ngoài hàng đợi. Đang có job của một dự án thì nút Cài đặt của dự án đó bị khóa, vì runner sẽ ghi đè `project.json` khi stage kết thúc.

### 7.2 Màn hình

1. **Project List** (sidebar)
   - Danh sách dự án: thumbnail, tên, thời lượng gốc → thời lượng mục tiêu, trạng thái tổng thể.
   - Chuột phải một dự án: **Đổi tên…**, **Hiện trong Finder**, **Chuyển vào Thùng rác…** (có hỏi lại; phím Delete cũng được). Xoá là chuyển cả thư mục dự án vào Thùng rác của ổ chứa nó, lấy lại được; phim gốc không bị đụng tới. Đang có tác vụ thì không đổi tên hay xoá được.
   - Nút "Dự án mới": kéo thả file phim, nhập tên, chọn tỉ lệ hoặc thời lượng mục tiêu, phong cách kể, giọng đọc.
   - **Chọn giọng đọc** (khi tạo dự án và trong settings của dự án): danh sách lấy từ `recap voices`, giọng được khuyên dùng (`featured`) xếp trước, có lọc theo nam/nữ và vùng miền. Mỗi giọng có nút ▶︎ **nghe thử**, gọi `recap voice-preview` với câu mặc định hoặc câu người dùng nhập (ví dụ câu đầu của kịch bản). File nghe thử được cache theo (engine, voice, rate, text) trong thư mục cache của app. Có thanh trượt tốc độ (`tts_rate`). Đổi giọng thì đánh dấu `tts`, `assemble`, `export` thành `stale`.

2. **Pipeline View** (màn hình chính của dự án)
   - Danh sách stage theo chiều dọc: icon trạng thái, thanh tiến độ, thời gian đã chạy và ước tính còn lại, nút **Chạy / Chạy lại / Hủy** cho từng stage.
   - Nút "Chạy toàn bộ" và "Chạy từ stage này".
   - Panel log (lọc theo stage, tự cuộn).
   - Banner lỗi: ví dụ `llm_quota` → "Hết hạn mức Claude, thử lại lúc …", kèm nút Retry.

3. **Scene Browser**
   - Lưới keyframe của các beat: mốc thời gian, mô tả, thoại.
   - Bấm vào một beat để phát đoạn đó (AVPlayer, seek tới `start`).
   - Đánh dấu beat ⭐ "quan trọng" (gửi kèm gợi ý cho Claude) hoặc 🚫 "bỏ qua".
   - Beat do `inspect` tự bỏ hiện mờ với nhãn 🤖🚫 và lý do ("Tự động bỏ: credit: …"); nút **"Vẫn dùng cảnh này"** đặt mark `keep`. Bộ lọc "Bỏ qua" gồm cả beat tự bỏ. Hộp chọn clip trong Script Editor cũng ẩn các beat bị bỏ.
   - Đổi mark thì `script` thành `stale`; chạy lại sẽ viết kịch bản mới (bản cũ vào `work/script_backups/`).

3b. **Nhân vật**
   - Lưới thẻ nhân vật: 6 ảnh mặt, thời lượng trên màn hình, số cảnh. Bên phải là các tên nghe được trong phim, kèm số lần và câu thoại mẫu.
   - Mỗi thẻ: ô tên, chọn giới tính (Không rõ / Nam / Nữ), menu "Chọn" (tên trong phim; tên gọi chung: người đàn ông, người phụ nữ, cô gái, chàng trai, người đi đường, người anh, người em gái, người mẹ, con ma, con quỷ, sinh vật lạ, cậu bé… và nghề nghiệp). Danh sách tên gọi chung sửa được ("Sửa danh sách tên chung…", lưu trong UserDefaults, dùng cho mọi dự án). "Gộp vào" một thẻ khác (cùng người), "Bỏ qua (vai phụ)".
   - "Lưu tên" (⌘S) và "Bỏ qua, để máy tự gọi tên". Lưu xong thì báo các stage cần chạy lại.

4. **Script Editor**
   - Bảng các dòng kịch bản: id, loại, text (sửa trực tiếp), clip (chọn lại từ Scene Browser), thời lượng TTS, trạng thái `fit` (✅ / ⚠️ short / ✂️ trimmed).
   - Mỗi dòng có nút: ▶︎ nghe TTS, ▶︎ xem đoạn ghép, 🔁 tạo lại TTS, ✨ "Nhờ Claude viết lại câu này" (gọi `claude -p` cho riêng câu đó, kèm ngữ cảnh 3 câu trước và sau).
   - Tổng thời lượng hiện tại so với mục tiêu.
   - Lưu thì ghi `script.yaml` và đánh dấu `tts`, `assemble` thành `stale`.
   - Nút **"Nhận xét & viết lại"**: người dùng viết nhận xét (có gợi ý mẫu), Claude viết lại cả kịch bản (`script-revise`, mục 4.5). Tắt khi còn sửa chưa lưu hoặc đang có job.

5. **Preview & Export**
   - AVPlayer phát `recap_preview.mp4`. Danh sách dòng kịch bản bên cạnh, tự highlight theo thời gian phát. Bấm vào dòng thì nhảy tới đoạn đó.
   - Nút "Render bản cuối (1080p)", "Xuất SRT", "Mở thư mục Output".

### 7.3 Trải nghiệm

- Đóng app khi đang chạy thì hỏi xác nhận, gửi SIGTERM cho worker.
- Mở lại app thì đọc `project.json`. Stage nào đang `running` mà không có process thì chuyển sang `failed` (bị gián đoạn), cho phép Retry.
- Thông báo hệ thống (UserNotifications) khi một stage dài chạy xong.

---

## 8. Prompt mẫu

> Bản đang dùng nằm trong `worker/recap/prompts/` và đã khác các mẫu dưới đây (thêm `narration_guide.md`: không tả quần áo, kể nhân quả, có câu chuyển ý, clip 2–8s, original 5–10s, kết bằng nhận xét kênh + mời bình luận). Các mẫu dưới đây chỉ để tham khảo ý ban đầu.

### 8.1 `prompts/describe_scene.md`

```
Bạn mô tả cảnh phim cho người viết tóm tắt. Nhìn ảnh và đọc thoại.
Trả lời 1 câu tiếng Việt, tối đa 30 từ: nhân vật (đặc điểm nhận dạng), hành động, bối cảnh, cảm xúc.
Không suy đoán tình tiết ngoài hình ảnh và thoại.
Thoại: {dialogue}
```

### 8.2 `prompts/write_script.md`

```
Bạn là biên kịch kênh tóm tắt phim tiếng Việt. Phong cách: {style}.
Phim: {title}. Đây là phần {part}/{total_parts}.
Tóm tắt các phần trước: {previous_summary}

Đầu vào (stdin) là danh sách beat: [ID mốc_bắt_đầu-mốc_kết_thúc] Hình | Thoại.

Nhiệm vụ: viết kịch bản tóm tắt cho phần này, tổng thời lượng khoảng {target_sec} giây
(lời kể tiếng Việt đọc khoảng 3 từ/giây).

Quy tắc bắt buộc:
1. Mỗi câu kể chỉ nói về nội dung đang chiếu trong clip đi kèm. Không kể trước hay kể sau hình.
2. Chỉ dùng beat ID và mốc thời gian có trong đầu vào. Mốc phải nằm trong khoảng của beat.
3. Tổng thời lượng clip của một dòng ≈ số từ / 3 (sai lệch tối đa ±30%).
4. Mỗi câu 8–25 từ. Viết số bằng chữ.
5. Chèn 1–3 dòng `type: original` mỗi phần cho các cảnh thoại "đắt" (5–15 giây).
6. Câu đầu phần 1 là hook gây tò mò. Phần cuối kết bằng nhận xét hoặc bài học ngắn.
7. Không bịa tình tiết không có trong đầu vào.

Trả về:
- Một khối YAML đúng schema dưới đây (items, không có title)
- Sau đó một dòng bắt đầu bằng "SUMMARY:" tóm tắt nội dung phần này trong 3–5 câu (để dùng cho phần sau).

Schema:
{schema_example}
```

---

## 9. Xử lý lỗi và hiệu năng

| Tình huống | Xử lý |
|---|---|
| Ollama chưa chạy | `describe` tự chạy `ollama serve` hoặc báo lỗi `ollama_unavailable` |
| Model chưa tải | Báo lỗi có kèm lệnh `ollama pull ...` |
| Claude hết hạn mức | `llm_quota`, giữ các phần đã xong, cho Retry |
| Claude trả YAML sai | Tự retry 2 lần kèm lỗi validate. Vẫn sai thì lưu bản thô vào `script_parts/part_XX.raw.txt` để sửa tay |
| edge-tts lỗi mạng / `NoAudioReceived` | Retry 3 lần, sau đó chuyển sang `say -v Linh` và báo trên UI (chỉ khi `tts_engine: edge`) |
| VieNeu lỗi / chưa tải model | `tts_failed` (retryable). Lần chạy đầu tự tải model khoảng 1GB về HF cache trên SSD |
| Phim có nhiều track audio | Dùng track mặc định, cho chọn trong settings |
| OCR (Vision) không đọc được khung / thiếu pyobjc | Khung đó coi như không có chữ, log `warning`; `inspect` vẫn xong (vẫn tìm logo tĩnh và viền đen) |
| `cropdetect` lỗi / không có ffmpeg | Bỏ qua phần viền đen, log `warning` |
| Đã tìm thấy nhân vật nhưng chưa đặt tên | `describe` (và `script`) báo `characters_unnamed`; banner có nút "Đặt tên nhân vật" mở tab Nhân vật |
| Chưa tải được model mặt / OpenCV lỗi | `characters_failed` (retryable); pipeline vẫn chạy được nếu bỏ qua stage này |
| Mọi beat đều bị bỏ | `script` báo `input_missing`, nói số beat do `inspect` bỏ và chỉ tới nút "Vẫn dùng cảnh này" |
| RAM | Không chạy Whisper và Ollama VL cùng lúc. Sau stage `describe` thì gọi unload model Ollama (`keep_alive: 0`) |

Đo thật trên Cơm Nguội tập 136 (26 phút, 1080p, máy 16GB): ingest 56s, transcribe 2:55, scenes 23s (169 beat), inspect 52s, describe 26:50, script 2–8 phút, tts 1:08, assemble 21s, export 3:52–4:27.

Ước tính trên M1 Max cho phim 2 tiếng: transcribe 10–20 phút, scenes 5–10 phút, describe 30–60 phút, script 5–15 phút, tts 2–5 phút, assemble preview 5 phút, render 1080p 10–20 phút.

---

## 10. Milestone (làm theo đúng thứ tự)

| # | Nội dung | Kiểm tra (acceptance) |
|---|---|---|
| **M1** | Worker skeleton: CLI, `events.py`, `models.py`, `project.json`, `recap doctor`, stage `ingest` | `python -m recap ingest` trên `samples/clip.mp4` tạo proxy + wav, in JSON Lines đúng format |
| **M2** | `transcribe` + `scenes` (gộp beat, keyframe) | `transcript.json` và `scenes.json` hợp lệ, số beat hợp lý (clip 5 phút ra khoảng 20–60 beat) |
| **M3** | `describe` qua Ollama, có cache | Chạy lại lần 2 không xử lý lại beat nào. Mô tả bằng tiếng Việt |
| **M4** | `script` qua `claude -p`, có digest, chia phần, validate, retry | `script.yaml` hợp lệ schema, mọi beat id và mốc thời gian đều tồn tại |
| **M5** | `tts` + `assemble` (căn khớp, ducking) + preview | `recap_preview.mp4` phát được. Lời kể khớp clip. Có báo cáo số dòng `fit` ok/short/trimmed |
| **M6** | `export` (SRT, phụ đề gắn cứng, render bản cuối tối đa 1080p từ file gốc) | File output đầy đủ, phụ đề khớp tiếng |
| **M7** | App SwiftUI: Settings + Doctor, Project List, tạo dự án (có chọn giọng + nghe thử), Pipeline View chạy stage và hiển thị tiến độ, log, hủy, retry | Chạy được toàn bộ pipeline từ UI với clip mẫu. Chọn giọng, nghe thử, đổi giọng thì tts chạy lại |
| **M8** | Scene Browser + Script Editor (sửa câu, nghe TTS, viết lại 1 câu bằng Claude) | Sửa 1 câu, render lại, chỉ câu đó tạo TTS mới |
| **M9** | Preview & Export: player + highlight dòng theo thời gian phát | Bấm vào dòng thì nhảy đúng đoạn |
| **M10** | Test với 1 phim đầy đủ ~2 tiếng, tối ưu thời gian và RAM | Hoàn thành không lỗi. Ghi lại thời gian từng stage trong `project.json` |

| **M11** | Cải tiến theo góp ý chuyên gia, làm theo thứ tự đã duyệt: (1) sửa nhanh: clip ≤ 8s, original ≤ 10s, tắt tiếng phim dưới lời kể, −14 LUFS, luôn 1080p 10 Mbps + lanczos/sharpen, banner mỏng tùy chỉnh bằng `brand.json`, prompt không tả quần áo; (4) stage `inspect`: OCR, watermark, quảng cáo, credit, viền đen; (2) QA tự động có ngưỡng chặn; (3) hồ sơ phim + kịch bản kể chuyện trước; (5) sửa clip và giọng đọc (150–170 từ/phút, ngắt nghỉ, từ điển phát âm). Sau đó: gom mặt nhân vật, kiểm tra sự thật bằng VL, Real-ESRGAN, khung 9:16 | Mỗi mục chạy thử trên video mẫu và báo cáo số đo. **Đã xong (1) và (4)**, thêm: gom mặt + đặt tên nhân vật (stage `characters`, tab Nhân vật), viết lại kịch bản theo nhận xét. Còn lại (2), (3), (5): hiện giọng đọc khoảng 273 từ/phút |

Yêu cầu chung: có unit test cho phần căn khớp (`assemble`) và validate script; dùng type hint đầy đủ; code Swift tách View, ViewModel và Service.

---

## 11. Chuẩn bị cho giai đoạn sau (chỉ thiết kế, chưa code)

- Thêm module `publish/` (Python) và màn hình "Publish" (Swift). Mỗi nền tảng là một `Publisher` riêng (`youtube`, `tiktok`, `facebook`) với interface `upload(video, meta) -> post_id` và `stats(post_id) -> {views, likes, comments}`.
- Token OAuth lưu trong **Keychain**, không lưu trong file dự án.
- Lưu ý: YouTube Data API có hạn mức upload thấp; TikTok và Facebook cần xin duyệt app. Chưa duyệt thì video chỉ đăng được ở chế độ riêng tư.

---

## 12. Ghi chú pháp lý và sử dụng

- Chỉ dùng file phim có nguồn hợp pháp. Video tóm tắt có dùng cảnh phim vẫn có thể bị nền tảng nhận diện bản quyền.
- Các bước dùng Claude (viết kịch bản, chia nhạc AI, viết lại 1 câu) dùng hạn mức gói Claude cá nhân thông qua Claude Code CLI (không đặt `ANTHROPIC_API_KEY` nên không tính tiền API). Một lần viết kịch bản phim 26 phút khoảng 25–35 nghìn token. Nếu sau này phân phối app cho người khác, phải chuyển sang `claude_api` provider.
