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

Python packages: `mlx-whisper`, `scenedetect[opencv]`, `vieneu` (VieNeu-TTS v3 Turbo, ONNX, model ~1GB trong HF cache trên SSD), `onnxruntime==1.22.1` (ghim: bản ≥1.23 từ chối file weights dạng symlink của HF cache), `edge-tts`, `pyyaml`, `pydantic`, `ollama`, `pillow`, `numpy`.

### Vị trí lưu trữ (ExternalSSD)

| Dữ liệu | Đường dẫn | Cách cấu hình |
|---|---|---|
| Model Ollama (~6GB) | `/Volumes/ExternalSSD/ollama-models` | biến môi trường `OLLAMA_MODELS` (đặt trước khi chạy `ollama serve`) |
| Model Whisper/HuggingFace (~1.6GB) | `/Volumes/ExternalSSD/hf-cache` | biến môi trường `HF_HOME` (worker tự set khi chạy) |
| Thư mục các dự án (~4–5GB/phim) | `/Volumes/ExternalSSD/myapps/appMReviewphim/output/` | biến môi trường `RECAP_PROJECTS_ROOT` (mặc định `output/` trong repo) |
| Code + venv worker | `/Volumes/ExternalSSD/myapps/appMReviewphim/` | — |

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
│ ingest → transcribe → scenes → describe → script → tts → assemble → export │
│   ffmpeg   mlx-whisper  PySceneDetect  Ollama VL  claude -p  VieNeu   ffmpeg│
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
│   │   │   ├── describe.py
│   │   │   ├── script.py
│   │   │   ├── tts.py
│   │   │   ├── assemble.py
│   │   │   └── export.py
│   │   ├── llm/              # lớp provider có thể thay thế
│   │   │   ├── base.py
│   │   │   ├── claude_cli.py # mặc định
│   │   │   ├── claude_api.py # để sau
│   │   │   └── ollama.py     # dự phòng
│   │   └── prompts/
│   │       ├── describe_scene.md
│   │       └── write_script.md
│   └── tests/
└── samples/                  # đặt 1 clip ngắn (2–5 phút) để test
```

### Thư mục dự án (mỗi phim một thư mục)

```
/Volumes/ExternalSSD/myapps/appMReviewphim/output/<TenPhim>.recap/
├── project.json              # metadata + settings + trạng thái từng stage
├── source.mp4 -> (symlink tới file phim gốc)
├── work/
│   ├── proxy_480p.mp4        # bản nhẹ để phân tích và preview
│   ├── audio_16k.wav
│   ├── transcript.json
│   ├── scenes.json
│   ├── keyframes/scene_0001.jpg ...
│   ├── scenes_described.json
│   ├── digest/part_01.txt ...  # danh sách cảnh rút gọn, gửi cho LLM
│   ├── script_parts/part_01.yaml ...
│   ├── tts/line_0001.wav ...
│   └── timeline.json
├── script.yaml               # kịch bản hoàn chỉnh (người dùng sửa được)
├── output/
│   ├── recap_preview.mp4     # 854x480, render nhanh
│   ├── recap.mp4             # bản cuối, tối đa 1920x1080 (không phóng to)
│   ├── recap_subbed.mp4
│   └── recap.srt
└── logs/<stage>.log
```

---

## 4. Các stage của pipeline

Mỗi stage: `python -m recap <stage> --project <path> [--force]`.
`python -m recap run --project <path> --from <stage> --to <stage>` để chạy liên tiếp.

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

### 4.4 `describe`

- Ollama, model `qwen2.5vl:7b` (cấu hình được). Với mỗi beat, gửi keyframe + thoại, nhận mô tả ngắn tiếng Việt (≤ 30 từ): ai, đang làm gì, ở đâu, cảm xúc.
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
   - Biến trong prompt: tên phim, phần thứ mấy/tổng số phần, **tóm tắt các phần trước** (lấy từ output phần trước), thời lượng mục tiêu cho phần này, phong cách kể (settings: nghiêm túc / hài mỉa mai / kịch tính), **tốc độ đọc của giọng đã chọn** (`{words_per_sec}` = tốc độ đo được × `tts_rate`; giọng chưa đo thì dùng 3).
   - Ước tính thời lượng = Σ(số từ / words_per_sec + 0.25s đệm mỗi câu kể) + Σ độ dài dòng original. Đổi giọng thì fingerprint của `script` đổi theo. `script.yaml` lưu `words_per_sec` đã dùng.
   - Không cấp tool cho Claude (chỉ nhận text). Lấy trường `result` trong JSON.
3. **Validate** YAML theo schema (mục 5.2): beat id phải tồn tại; mốc thời gian nằm trong beat; thời lượng ước tính gần mục tiêu ±15%. Nếu sai thì gọi lại **tối đa 2 lần**, kèm thông báo lỗi cụ thể.
4. Gộp các phần thành `script.yaml`.
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

### 4.7 `assemble` (căn khớp + render)

**Quy tắc căn khớp** cho mỗi mục trong script, tạo `timeline.json`:

| Loại | Xử lý |
|---|---|
| `narrate` + `clips` | Độ dài đoạn = độ dài TTS + `pad` (0.25s). Lấy các clip theo thứ tự. **Clip dư** thì cắt bớt đuôi. **Clip thiếu** thì: (1) kéo dài clip cuối trong phạm vi beat của nó, (2) nối tiếp sang các shot kế tiếp trong cùng beat, (3) nếu vẫn thiếu thì làm chậm hình tối đa 0.85x, (4) vẫn thiếu thì đánh dấu `fit: "short"` để báo trên UI |
| `original` | Giữ nguyên hình và tiếng gốc, không có lời kể |

- **Âm thanh**: khi đang có lời kể, giảm tiếng phim xuống `duck_volume` (mặc định 0.15) bằng sidechain hoặc volume theo khoảng thời gian, có fade 0.2s ở hai đầu. Đoạn `original` để âm lượng 1.0. Chuẩn hóa toàn bộ về −16 LUFS.
- **Render**: `assemble` tạo `recap_preview.mp4` (480p, nhanh, từ proxy). Bản cuối do stage `export` render từ file gốc. Mỗi dòng timeline được render thành một clip riêng trong `work/render/<profile>/` và cache theo hash, nên sửa một câu chỉ render lại câu đó (module `timeline_render`, dùng chung cho cả hai).
- Chuyển cảnh: cắt thẳng; có tùy chọn crossfade 0.2s.

### 4.8 `export`

- `recap.srt`: lấy từ timeline. Mỗi câu kể hiển thị trong đúng khoảng giọng đọc (bắt đầu từ `out_start + 0.1s`, dài bằng thời lượng TTS). Câu dài được tách thành nhiều đoạn, mỗi đoạn tối đa 2 dòng × 42 ký tự, ưu tiên ngắt sau dấu câu; thời gian chia theo số ký tự. Khoảng trống giữa hai đoạn dưới 0.5s thì được lấp để phụ đề không chớp tắt.
- `recap.mp4`: render lại timeline từ **file gốc** (đúng track âm thanh đã chọn), khung 16:9, cao tối đa 1080p nhưng **không phóng to quá độ phân giải gốc** (phim 480p thì xuất 480p). x264 crf 20, preset medium, âm thanh −16 LUFS như preview.
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
    "narration_style": "kich_tinh",
    "tts_engine": "vieneu",
    "voice": "Hải Đăng",
    "tts_rate": "+0%",
    "tts_pitch": "+0Hz",
    "vl_model": "qwen2.5vl:7b",
    "whisper_model": "mlx-community/whisper-large-v3-turbo",
    "llm_provider": "claude_cli",
    "claude_model": "sonnet",
    "duck_volume": 0.15,
    "subtitle_font": "Arial",
    "min_beat_sec": 4,
    "max_beat_sec": 20,
    "part_size_min": 30
  },
  "stages": {
    "ingest":     {"status": "done", "started_at": "...", "finished_at": "...", "error": null},
    "transcribe": {"status": "running", "progress": 0.42},
    "scenes":     {"status": "pending"}
  }
}
```

`status`: `pending | running | done | failed | stale`. Khi người dùng đổi một setting, đánh dấu `stale` cho các stage phụ thuộc vào setting đó, ví dụ đổi giọng thì `tts`, `assemble`, `export` thành `stale`.

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
- Lưu trữ: đọc/ghi trực tiếp các file trong thư mục dự án. Danh sách dự án là thư mục `appMReviewphim/output/` (cấu hình được), theo dõi bằng `FileManager` hoặc `DispatchSource`.
- Settings toàn cục (`@AppStorage`): đường dẫn `python` (venv của worker), `ffmpeg`, `claude`, `ollama`; thư mục dự án mặc định; các model mặc định. Có nút **"Kiểm tra môi trường"** chạy `recap doctor` và hiển thị ✅/❌ cho từng công cụ.

- **App chỉ đọc `project.json`.** Mọi thay đổi settings đi qua `python -m recap set --project P key=value …`. Worker kiểm tra giá trị (pydantic, giọng có tồn tại không, `tts_rate` có hợp lệ không), lưu, và đánh dấu `stale` cho stage có fingerprint thay đổi cùng mọi stage `done` phía sau. Kết quả trả về `{settings, stale}`; lỗi trả về `settings_invalid`. Stage `stale` vẫn đủ điều kiện để chạy các stage sau nó (output còn, chỉ cũ). Fingerprint của `script` chỉ đổi khi tốc độ giọng lệch quá 15% so với `words_per_sec` ghi trong `script.yaml`.
- **Unicode:** Foundation `Process` truyền tham số ở dạng NFD. Worker chuẩn hóa mọi tham số dòng lệnh về NFC trước khi xử lý (tên giọng, tên phim).
- **Mã nguồn:** `app/RecapKit` (Swift package: model `project.json`, giải mã `WorkerEvent`, `WorkerClient` chạy `Process`, đọc pipe bằng `readabilityHandler`, hủy bằng SIGTERM, `JobQueue` 1 job nặng một lúc, `ProjectRunState`, danh sách dự án, cache nghe thử giọng). `app/RecapStudio` (SwiftUI). `app/project.yml` dùng cho XcodeGen.
  - Test: `cd app/RecapKit && swift test`. Thêm `RECAP_INTEGRATION=1` để chạy cả các test gọi worker thật.
  - Build: `cd app && xcodegen generate && xcodebuild -project RecapStudio.xcodeproj -scheme RecapStudio -derivedDataPath build build`.
  - Các lệnh ngắn (`new`, `set`, `voices`, `voice-preview`, `doctor`) chạy ngoài hàng đợi. Đang có job của một dự án thì nút Cài đặt của dự án đó bị khóa, vì runner sẽ ghi đè `project.json` khi stage kết thúc.

### 7.2 Màn hình

1. **Project List** (sidebar)
   - Danh sách dự án: thumbnail, tên, thời lượng gốc → thời lượng mục tiêu, trạng thái tổng thể.
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

4. **Script Editor**
   - Bảng các dòng kịch bản: id, loại, text (sửa trực tiếp), clip (chọn lại từ Scene Browser), thời lượng TTS, trạng thái `fit` (✅ / ⚠️ short / ✂️ trimmed).
   - Mỗi dòng có nút: ▶︎ nghe TTS, ▶︎ xem đoạn ghép, 🔁 tạo lại TTS, ✨ "Nhờ Claude viết lại câu này" (gọi `claude -p` cho riêng câu đó, kèm ngữ cảnh 3 câu trước và sau).
   - Tổng thời lượng hiện tại so với mục tiêu.
   - Lưu thì ghi `script.yaml` và đánh dấu `tts`, `assemble` thành `stale`.

5. **Preview & Export**
   - AVPlayer phát `recap_preview.mp4`. Danh sách dòng kịch bản bên cạnh, tự highlight theo thời gian phát. Bấm vào dòng thì nhảy tới đoạn đó.
   - Nút "Render bản cuối (1080p)", "Xuất SRT", "Mở thư mục Output".

### 7.3 Trải nghiệm

- Đóng app khi đang chạy thì hỏi xác nhận, gửi SIGTERM cho worker.
- Mở lại app thì đọc `project.json`. Stage nào đang `running` mà không có process thì chuyển sang `failed` (bị gián đoạn), cho phép Retry.
- Thông báo hệ thống (UserNotifications) khi một stage dài chạy xong.

---

## 8. Prompt mẫu

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
| RAM | Không chạy Whisper và Ollama VL cùng lúc. Sau stage `describe` thì gọi unload model Ollama (`keep_alive: 0`) |

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

Yêu cầu chung: có unit test cho phần căn khớp (`assemble`) và validate script; dùng type hint đầy đủ; code Swift tách View, ViewModel và Service.

---

## 11. Chuẩn bị cho giai đoạn sau (chỉ thiết kế, chưa code)

- Thêm module `publish/` (Python) và màn hình "Publish" (Swift). Mỗi nền tảng là một `Publisher` riêng (`youtube`, `tiktok`, `facebook`) với interface `upload(video, meta) -> post_id` và `stats(post_id) -> {views, likes, comments}`.
- Token OAuth lưu trong **Keychain**, không lưu trong file dự án.
- Lưu ý: YouTube Data API có hạn mức upload thấp; TikTok và Facebook cần xin duyệt app. Chưa duyệt thì video chỉ đăng được ở chế độ riêng tư.

---

## 12. Ghi chú pháp lý và sử dụng

- Chỉ dùng file phim có nguồn hợp pháp. Video tóm tắt có dùng cảnh phim vẫn có thể bị nền tảng nhận diện bản quyền.
- Bước viết kịch bản dùng hạn mức gói Claude Pro cá nhân thông qua Claude Code CLI. Nếu sau này phân phối app cho người khác, phải chuyển sang `claude_api` provider.
