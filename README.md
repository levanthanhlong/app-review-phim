# app-review-phim

RecapStudio: app macOS tạo video tóm tắt phim bằng AI chạy trên máy (spec: `docs/SPEC_RecapStudio.md`).

## Cài đặt trên máy mới

Cần: Mac Apple Silicon, macOS 14+, [Homebrew](https://brew.sh), Xcode (App Store), khoảng 30 GB trống.

```bash
git clone <repo> && cd appMReviewphim
./setup.sh                 # công cụ, worker Python, model AI (~15 GB), build app
./setup.sh --help          # các tuỳ chọn: --with-acestep, --skip-models, --install-app
claude                     # đăng nhập Claude Code một lần (stage viết kịch bản dùng nó)
```

Chạy lại `./setup.sh` lúc nào cũng được: bước đã xong sẽ bỏ qua, model đang tải dở sẽ tải tiếp.

## Thư mục không nằm trong git

| Thư mục | Nội dung |
|---|---|
| `output/` | Các dự án phim (`<Tên>.recap/`) |
| `music/` | Nhạc nền thư viện, mỗi thư mục con là một thể loại |
| `tools/` | Stable Audio 3 và ACE-Step (venv + model riêng), do `setup.sh` cài |
