# RecapStudio: tài liệu phát triển

Tài liệu cho người (và Claude Code) mới vào dự án: app làm gì, chạy ra sao, code nằm đâu, đã làm tới
đâu, và cách làm việc trong repo này. Chi tiết từng thuật toán và schema nằm ở
[`SPEC_RecapStudio.md`](SPEC_RecapStudio.md) (tài liệu gốc, luôn cập nhật cùng code). Cập nhật lần cuối: 2026-11-05.

Đọc theo thứ tự: mục 1–3 để nắm toàn cảnh, mục 4–6 trước khi sửa code, mục 9 trước khi chạy bất cứ thứ gì.

---

## 1. App làm gì

RecapStudio là app macOS (SwiftUI) tạo **video tóm tắt / review phim tiếng Việt** từ một file phim, gần
như tự động và chạy trên máy (Mac Apple Silicon, 16GB RAM):

1. Nạp phim → nhận dạng lời thoại → chia phim thành các "beat" (đoạn cảnh) → kiểm tra nguồn (logo,
   quảng cáo, credit, viền đen) → tìm nhân vật → mô tả từng cảnh bằng AI nhìn ảnh.
2. Claude Code (`claude -p`) viết kịch bản lời kể theo giọng kênh "Mắm Review Phim", chọn clip cho từng
   câu và vài đoạn giữ nguyên thoại gốc.
3. Giọng AI tiếng Việt đọc kịch bản (VieNeu-TTS, offline) → nhạc nền → ghép video khớp lời kể →
   xuất 1080p kèm phụ đề.

Người dùng (chủ kênh, nói tiếng Việt) can thiệp ở các điểm: chọn giọng/phong cách khi tạo dự án, đánh
dấu cảnh ⭐/🚫, đặt tên nhân vật, sửa kịch bản (tay, ✨ viết lại một câu, hoặc "Nhận xét & viết lại"),
chọn nhạc, xem trước rồi xuất.

Chỉ dùng trên máy của người dùng (không phân phối): app ký ad-hoc, không sandbox.

---

## 2. Kiến trúc

```
┌─────────────── app/ (SwiftUI, macOS 14+) ────────────────┐
│ RecapStudio (UI)  ──dùng──  RecapKit (Swift package:      │
│                             model, WorkerClient, JobQueue) │
└──────────────┬───────────────────────────▲───────────────┘
               │ Process: worker/.venv/bin/python -m recap <lệnh> --project P
               ▼                           │ stdout: JSON Lines (progress/log/result/error)
┌─────────────── worker/ (Python 3.12, package `recap`) ───┐
│ ingest → transcribe → scenes → inspect → characters →     │
│ describe → script → tts → music → assemble → export        │
└──────────────┬───────────────────────────────────────────┘
               ▼ đọc/ghi
        output/<Tên>.recap/   (thư mục dự án: project.json, work/, output/, logs/)
```

Nguyên tắc cứng:

- **Swift chỉ lo giao diện và điều phối. Python lo mọi thứ AI và video.**
- **`project.json` chỉ do worker ghi.** App muốn đổi gì (settings, kịch bản, đánh dấu cảnh, tên nhân vật,
  đổi tên dự án) đều gọi một lệnh CLI của worker. Worker kiểm tra, lưu, và đánh dấu `stale` các stage bị ảnh hưởng.
- **Mỗi lúc chỉ một job nặng** (`JobQueue`); lệnh ngắn chạy ngoài hàng đợi nhưng bị chặn khi dự án đang có job
  (`AppModel.isBusy`).
- Giao tiếp worker → app: mỗi dòng stdout là một JSON (`stage_start`, `progress`, `log`, `artifact`,
  `stage_end`, `error`, `result`). Log thô ra stderr và `logs/<stage>.log`. Huỷ = SIGTERM, worker dừng an toàn
  (mọi file ghi ra `.tmp` rồi rename).

---

## 3. Pipeline: các stage

| # | Stage | Làm gì | Công cụ | Output chính (trong dự án) |
|---|---|---|---|---|
| 1 | `ingest` | ffprobe, tạo proxy 480p + wav 16k | ffmpeg | `work/proxy_480p.mp4`, `work/audio_16k.wav` |
| 2 | `transcribe` | Lời thoại có mốc thời gian | mlx-whisper large-v3-turbo | `work/transcript.json` |
| 3 | `scenes` | Cắt shot, gộp thành beat 4–20s, keyframe | PySceneDetect | `work/scenes.json`, `work/keyframes/` |
| 4 | `inspect` *(không bắt buộc)* | OCR 1 khung/giây: watermark, dải phụ đề, quảng cáo, credit, viền đen → cảnh tự bỏ + vùng làm mờ | macOS Vision | `work/inspect.json` |
| 5 | `characters` *(không bắt buộc)* | Tìm mặt, gom thành người, ảnh mặt; Claude tìm tên riêng trong thoại | OpenCV YuNet + SFace, scipy, `claude -p` | `work/characters.json` |
| 6 | `describe` | 1 câu mô tả mỗi beat (có tên người đã đặt) | Ollama `qwen2.5vl:7b` | `work/scenes_described.json` |
| 7 | `script` | Kịch bản theo phần ~30 phút, validate, retry | `claude -p` | `script.yaml` |
| 8 | `tts` | Giọng đọc từng câu, cache theo hash | VieNeu-TTS (edge-tts dự phòng) | `work/tts/` |
| 9 | `music` | Thư viện nhạc, hoặc nhạc AI theo cảm xúc từng đoạn | Stable Audio 3 (MLX), `claude -p` | `work/music/` |
| 10 | `assemble` | Căn khớp hình với lời, ducking, −14 LUFS, banner kênh, làm sạch nguồn; bản xem trước | ffmpeg | `work/timeline.json`, `output/recap_preview.mp4` |
| 11 | `export` | Render 1080p từ file gốc, SRT, phụ đề gắn cứng | ffmpeg + Pillow | `output/recap.mp4`, `recap_subbed.mp4`, `recap.srt` |

**Claude Code chỉ bắt buộc ở `script`.** Ngoài ra dùng ở: tìm tên (`characters`, không có vẫn chạy),
nhạc AI, "✨ viết lại câu", "Nhận xét & viết lại". Chỉ có provider `claude_cli` (`recap/llm/claude_cli.py`,
gọi `claude -p … --tools ""`: không tool, không skill). Hết hạn mức → `llm_quota`, giữ phần đã xong.

### Cơ chế chạy lại (quan trọng)

- Mỗi stage có `fingerprint(project_dir, project)`: chuỗi mô tả đầu vào (chữ ký file = tên + size + mtime,
  settings liên quan, hash prompt…). Stage `done` có fingerprint không đổi thì **bỏ qua** khi chạy lại.
- `settings_update.save_marking_stale`: sau mỗi thay đổi, stage `done` có fingerprint đổi → `stale`, và mọi
  stage `done` phía sau cũng `stale`. Stage `stale` vẫn cho chạy các stage sau (output còn, chỉ cũ).
- `runner.OPTIONAL_STAGES = {"inspect", "characters"}`: stage sau chỉ cần stage *bắt buộc* gần nhất xong,
  nên dự án cũ (tạo trước khi có hai stage này) vẫn chạy được.
- Cache sâu trong stage: describe theo beat (key gồm prompt của beat), tts theo hash câu, render theo
  dòng timeline, OCR theo khung, mặt theo video, tên theo transcript. Sửa một câu chỉ làm lại câu đó.
- Fingerprint **không** chứa đường dẫn thư mục dự án → đổi tên dự án không phải chạy lại gì.

---

## 4. Bản đồ code

### worker/ (Python)

| File | Vai trò |
|---|---|
| `recap/__main__.py` | CLI (argparse): mọi lệnh, chuẩn hoá tham số NFC |
| `recap/runner.py` | `run_stage`, kiểm tra phụ thuộc, fingerprint/skip, `StageFailure`, huỷ an toàn |
| `recap/models.py` | pydantic: `Project`, `Settings`, `StageState`, `Beat`… `STAGE_NAMES` |
| `recap/config.py` | tạo/đọc/ghi dự án, ghi nguyên tử, `file_signature`, cache trên SSD |
| `recap/events.py` | `Emitter`: JSON Lines ra stdout |
| `recap/settings_update.py` | lệnh `set`, `save_marking_stale` |
| `recap/stages/*.py` | 11 stage (bảng mục 3), đăng ký trong `stages/__init__.py` |
| `recap/digest.py` | digest beat gửi Claude: `[B0001 00:00-00:05] Hình: … \| Thoại: … \| Nhân vật: …` |
| `recap/script_model.py` | schema + validate `script.yaml` |
| `recap/script_edit.py` | `script-show/save/rewrite`, `beat-mark` |
| `recap/script_revise.py` | `script-revise`: viết lại cả kịch bản theo nhận xét |
| `recap/characters.py` / `faces.py` / `character_names.py` / `character_edit.py` | gom mặt, model mặt (tải lần đầu, SHA-256), tìm tên, lệnh `characters-save` |
| `recap/inspect_rules.py` / `ocr.py` / `ad_blurs.py` / `cleanup.py` / `beat_marks.py` | luật inspect, Vision OCR, làm mờ quảng cáo, filter ffmpeg làm sạch, đánh dấu cảnh |
| `recap/fit.py` / `beats.py` / `render.py` / `timeline_render.py` | căn khớp clip–lời, render từng dòng có cache |
| `recap/tts_engines.py` / `vieneu_tts.py` / `voices.py` | giọng đọc, đo tốc độ giọng (`data/voice_rates.json`) |
| `recap/music*.py` | kế hoạch nhạc, tạo nhạc, lựa chọn phương án |
| `recap/subtitles.py` / `subtitle_images.py` | SRT, phụ đề vẽ bằng Pillow (ffmpeg Homebrew không có libass) |
| `recap/branding.py` | banner/logo kênh, `brand.json` |
| `recap/project_rename.py` | `project-rename` |
| `recap/doctor.py` | `recap doctor`: kiểm tra môi trường |
| `recap/prompts/*.md` | prompt (xem mục 5) |
| `tests/` | pytest, ~494 test |

### app/ (Swift)

| File | Vai trò |
|---|---|
| `RecapKit/…/ProjectFile.swift` | đọc `project.json` (`StageName`, `ProjectSettings`…) |
| `RecapKit/…/WorkerClient.swift`, `WorkerEvent.swift`, `JobQueue.swift`, `ProjectRunState.swift` | chạy worker, giải mã sự kiện, hàng đợi 1 job, trạng thái chạy/log/lỗi |
| `RecapKit/…/SceneBeats.swift`, `ScriptDocument.swift`, `Timeline.swift`, `Characters.swift`, `MusicPlan.swift`, `Library.swift` | đọc các file của dự án |
| `RecapStudio/AppModel.swift` | trạng thái toàn app, mọi lệnh gửi worker (`enqueue` cho job nặng, `staleCommand` cho lệnh ngắn) |
| `ContentView.swift` | sidebar dự án (đổi tên, xoá vào Thùng rác) |
| `PipelineView.swift` | tab "Các bước": danh sách stage, banner lỗi (nút "Đặt tên nhân vật" cho `characters_unnamed`), log |
| `SceneBrowserView.swift` | tab "Cảnh": lưới beat, ⭐/🚫/"Vẫn dùng cảnh này" |
| `CharactersView.swift`, `CharacterCard.swift` | tab "Nhân vật": đặt tên, giới tính, gộp, bỏ qua; tên gọi chung sửa được |
| `ScriptEditorView.swift`, `ScriptLineRow.swift`, `ScriptEditorState.swift`, `ReviseScriptSheet.swift` | tab "Kịch bản" |
| `PreviewView.swift` | tab "Xem trước" + xuất |
| `NewProjectSheet.swift` | tạo dự án + cài đặt dự án (`EditableSettings` → `recap set`) |
| `AppSettings.swift`, `SettingsView.swift` | cài đặt toàn app (UserDefaults): đường dẫn worker, python, thư mục dự án, nhạc, ảnh kênh |
| `project.yml` | XcodeGen (bundle `com.thanhle.RecapStudio`, Swift 6, không sandbox) |

### Khác

- `setup.sh`: cài trên máy mới (Homebrew tools, uv + venv worker, model Ollama/Whisper/VieNeu/Stable Audio, build app).
- `make_dmg.sh`: build Release → `dist/RecapStudio-<version>.dmg`; ký ad-hoc, `--choose-cert`, `--cert`.
- `recap-script-vi/`: skill gốc của người dùng về giọng kể; nội dung đã được chuyển vào `narration_guide.md`.
- `.claude/skills/recap-scriptwriting/`: skill cho Claude Code khi người dùng nhờ sửa kịch bản trong chat.
- `music/<thể loại>/`, `img/` (banner, ảnh đại diện kênh), `samples/clip.mp4` (clip test), `tools/` (Stable Audio 3).
- `docs/PLAN_*.md`: kế hoạch đã duyệt nhưng chưa làm (mục 8).

---

## 5. Prompt và giọng kênh

Prompt nằm trong `worker/recap/prompts/` và được đọc lại mỗi lần gọi (sửa prompt có hiệu lực ngay, kể cả
với job đang chạy). Hash prompt nằm trong fingerprint → sửa prompt thì stage tương ứng thành cần chạy lại.

| File | Dùng ở |
|---|---|
| `narration_guide.md` | **Cẩm nang giọng kể**, chèn vào mọi prompt viết kịch bản. Soạn từ skill `recap-script-vi`: kể nhân quả, đọc nội tâm, bình luận mỗi 45–90s, vòng mở, khung video (hook ≤ 80 từ → mở truyện → leo thang → cao trào → kết → nhận xét + kêu gọi like/đăng ký), kho từ lóng (chỉ phong cách "đời thường"), checklist |
| `write_script.md` | prompt từng phần của `script`: biến `{channel}`, `{narrator}`, `{style}`, `{position}` (vị trí của phần trong khung), `{words_per_sec}`… và schema YAML |
| `revise_script.md` | thêm vào khi "Nhận xét & viết lại" |
| `rewrite_line.md` | ✨ viết lại một câu (cũng chèn cẩm nang) |
| `describe_scene.md` | mô tả cảnh; nếu có người đã đặt tên thì thêm dòng "Người có mặt trong cảnh… tin thông tin này" |
| `find_names.md` | tìm tên riêng trong thoại |
| `plan_music.md` | kế hoạch nhạc AI |

Settings liên quan: `narration_style` (`kich_tinh`, `nghiem_tuc`, `hai_mia_mai`, `doi_thuong_hai`),
`channel_name` (mặc định "Mắm Review Phim"), `narrator` (mặc định "Mắm"), `target_ratio` (0.2 = 20% độ
dài phim), `voice`, `tts_rate`. Kịch bản: mỗi câu 8–25 từ, clip 2–8s, đoạn `original` (thoại gốc) 5–10s,
1–3 đoạn mỗi phần; không gọi nhân vật theo màu áo; không bịa.

---

## 6. Dữ liệu của một dự án

```
output/<Tên>.recap/
├── project.json              # chỉ worker ghi: title, settings, media, trạng thái + fingerprint từng stage
├── source.mp4 → /đường/dẫn/phim/gốc   (symlink tuyệt đối: xoá phim gốc thì không export được)
├── script.yaml               # kịch bản (người dùng sửa được)
├── work/
│   ├── proxy_480p.mp4, audio_16k.wav, transcript.json, scenes.json, keyframes/
│   ├── inspect.json, inspect/ocr.json
│   ├── characters.json, characters/{faces.npz,names.json,faces/*.jpg}, character_choices.json
│   ├── beat_marks.json, scenes_described.json, describe_cache.json
│   ├── digest/, script_parts/, script_backups/
│   ├── tts/, music/, timeline.json, render/<profile>/, subs/
├── output/  recap_preview.mp4, recap.mp4, recap_subbed.mp4, recap.srt, music_provenance.json
└── logs/<stage>.log
```

Schema chi tiết: SPEC §5. Swift đọc bằng `convertFromSnakeCase`; trường mới phải là optional bên Swift
để dự án cũ vẫn đọc được.

---

## 7. Tính năng đã có (theo milestone)

- **M1–M10** (SPEC §10): toàn bộ pipeline, app (danh sách dự án, tạo dự án, chọn/nghe thử giọng, Pipeline
  View, Scene Browser, Script Editor, Preview & Export), test trên phim dài.
- **M11, đã xong:**
  - (1) Sửa nhanh: clip ≤ 8s, original ≤ 10s, tắt tiếng phim dưới lời kể, −14 LUFS, luôn 1080p 10 Mbps +
    lanczos/unsharp, banner kênh tuỳ chỉnh bằng `brand.json`, prompt không tả quần áo.
  - (4) Stage `inspect`: watermark (làm mờ cả video), dải phụ đề cứng (giữ nguyên), quảng cáo chèn đè
    (làm mờ đúng lúc hiện), thẻ quảng cáo/credit/hậu trường (bỏ cảnh, cần ≥ 2 cờ trong 3s), viền đen
    (cropdetect, dự phòng đo trung vị). Người dùng giữ lại cảnh bằng "Vẫn dùng cảnh này".
  - Stage `characters` + tab **Nhân vật**: ảnh mặt từng người, tên nghe được trong phim, tên gọi chung
    (người đàn ông, cô gái, con ma… + nghề nghiệp, sửa được), giới tính nam/nữ, gộp, bỏ qua, "Bỏ qua, để
    máy tự gọi tên". Tên đi vào `describe` (sửa lỗi model nhìn ảnh đoán sai người/giới tính) và `script`.
    Chưa đặt tên thì `describe` dừng với `characters_unnamed`.
  - Kịch bản: **"Nhận xét & viết lại"**, giọng kênh từ skill `recap-script-vi`, phong cách "Đời thường",
    tên kênh/cách xưng trong cài đặt dự án, kêu gọi like/đăng ký cuối video.
  - Đổi tên dự án (thư mục đổi theo) và xoá dự án (vào Thùng rác).
  - `make_dmg.sh`.

Số đo thật (máy 16GB): Cơm Nguội tập 136 (26 phút, 1080p): ingest 56s, transcribe 2:55, scenes 23s,
inspect 52s, describe 26:50, script 2–8 phút, tts 1:08, assemble 21s, export ~4 phút. Phim Thái 15 phút:
describe ~19 phút, script ~1,5–2 phút.

---

## 8. Việc còn lại

| Việc | Trạng thái |
|---|---|
| Tab Nhân vật dễ nhận người hơn: xem cảnh khi bấm thẻ, ảnh 320px nửa người, chọn ảnh nét nhất | **Đã duyệt**, kế hoạch chi tiết: [`PLAN_character_picker.md`](PLAN_character_picker.md) |
| M11 (2): QA tự động có ngưỡng chặn | chưa làm |
| M11 (3): hồ sơ phim, viết kịch bản kể chuyện trước | chưa làm |
| M11 (5): giọng đọc 150–170 từ/phút (hiện ~273), ngắt nghỉ, từ điển phát âm; clip | chưa làm |
| Phụ đề tiếng Việt cho đoạn thoại gốc của phim nước ngoài không có vietsub | đề xuất, chưa duyệt |
| Lấy lời thoại từ vietsub in trên hình (OCR) thay cho Whisper | đề xuất, đã thử (`/Volumes/ExternalSSD/tmp/vietsub_probe`), chưa duyệt |
| Cẩm nang: bớt câu chuyển ý kiểu "lém" ở phong cách Kịch tính/Nghiêm túc | đề xuất, chưa duyệt |
| Sau đó: kiểm tra sự thật bằng VL, Real-ESRGAN, khung 9:16 | xa |

Dự án mẫu trong `output/`: "Cơm Nguội 136" (phim gốc đã bị xoá khỏi Downloads, không export lại được;
kịch bản đã viết lại, tts→export cần chạy lại), "Thất Tình (Thái Lan)" (tới `script`, tên nhân vật đặt thử),
"Tee Yod- Quỷ Ăn Tạng Phần 1 Vietsub" (người dùng đang chạy trong app), "Clip mẫu M8".

---

## 9. Cách làm việc trong repo

### Lệnh

```bash
# Worker
cd worker
.venv/bin/python -m pytest -q                          # toàn bộ test (~2 phút)
.venv/bin/python -m recap doctor                       # kiểm tra môi trường
.venv/bin/python -m recap run --project "../output/X.recap" --from describe --to script
.venv/bin/python -m recap <stage> --project P [--force]
uv add <gói>                                            # thêm thư viện (đừng pip install)

# App
/usr/bin/xcrun swift test --package-path app/RecapKit  # dùng xcrun (tránh toolchain swiftly)
cd app && xcodegen generate && xcodebuild -project RecapStudio.xcodeproj -scheme RecapStudio \
  -destination "platform=macOS,arch=arm64" -derivedDataPath build build
./make_dmg.sh [--choose-cert]                          # đóng gói, người dùng tự cài

# Sau khi sửa file nguồn
codegraph sync                                         # từ gốc repo
```

### Quy trình

1. **Đọc trước khi sửa**: dùng codegraph (`codegraph_context`, `codegraph_explore`) trước khi grep/đọc file.
2. **TDD**: viết test trước, thấy nó fail, rồi mới code. Test theo kiểu Arrange–Act–Assert, tên test mô
   tả hành vi. Worker có fake cho LLM (`FakeLLM`), describer, face reader; không gọi Claude/Ollama thật trong test.
3. **Thay đổi có ảnh hưởng tới người dùng** (ví dụ thêm stage, đổi prompt, đổi fingerprint) thì nghĩ tới dự
   án cũ: trường mới optional, stage mới không bắt buộc, không làm `stale` những thứ không cần.
4. **Chạy thật** trên một dự án mẫu sau khi test xanh, báo số đo (thời gian, kết quả) cho người dùng.
5. **Cập nhật `SPEC_RecapStudio.md`** cùng lúc với code (nó là tài liệu gốc) và tài liệu này nếu đổi kiến trúc.
6. **Không commit** nếu người dùng chưa bảo. Commit theo dạng `feat: …`, `fix: …`.

### Ràng buộc và bẫy đã gặp

- **Ổ trong gần đầy.** Mọi thứ nặng nằm trên `/Volumes/ExternalSSD` (cache HF, model Ollama, model mặt
  `/Volumes/ExternalSSD/cache/recap-models`, dự án). File tạm lớn để ở `/Volumes/ExternalSSD/tmp`.
- **Không chạy lệnh trên dự án app đang xử lý** (xem `ps aux | grep "[-]m recap"`): hai tiến trình cùng ghi
  `project.json`. App cài ở `/Applications`; build ra `app/build/` không ghi đè bản đang chạy.
- Sửa file `.py` không ảnh hưởng job đang chạy (code đã nạp), nhưng **sửa prompt thì có** (đọc lại mỗi lần gọi).
- Foundation `Process` truyền tham số dạng NFD; worker chuẩn hoá về NFC (tên giọng, tên phim tiếng Việt).
- ffmpeg của Homebrew không có libass/freetype: phụ đề phải vẽ bằng Pillow.
- `onnxruntime==1.22.1` phải ghim (bản mới từ chối file symlink của HF cache, VieNeu cần).
- YAML: mốc thời gian phải để trong ngoặc kép (`"01:02:05"` không thì thành số base-60).
- Model nhìn ảnh (Qwen2.5-VL) hay đoán sai giới tính/người trong cảnh tối; tên nhân vật đã đặt được đưa vào
  prompt để sửa. Whisper với tiếng Thái ra chữ có dấu cách lẻ ("ไป ด ี ว ะ"), Claude vẫn đọc được.
- Không gọi Ollama VL và Whisper cùng lúc (RAM); `describe` giải phóng model sau khi xong.
- Model mặt OpenCV Zoo tự tải lần đầu (YuNet MIT, SFace Apache-2.0), kiểm tra SHA-256.

### Làm việc với người dùng

- Người dùng nói **tiếng Việt**; trả lời bằng tiếng Việt, rõ ràng, có số đo.
- Không bao giờ nhắc tới chi phí/token.
- Việc lớn: đề xuất và hỏi trước; việc đã được duyệt thì làm hết, chạy thử thật, rồi báo kết quả kèm
  những gì còn chưa làm.
