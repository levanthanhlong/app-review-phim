#!/usr/bin/env bash
# RecapStudio setup: tools, Python worker, AI models and the macOS app, on a fresh Apple Silicon Mac.
#
#   ./setup.sh                  everything needed to run the app (models ≈ 15 GB)
#   ./setup.sh --with-acestep   also ACE-Step 1.5, the fallback music engine (≈ 10 GB more, slow)
#   ./setup.sh --skip-models    tools, worker and app only (models download on first use)
#   ./setup.sh --install-app    also copy RecapStudio.app to /Applications
#
# Safe to run again: finished steps are skipped, interrupted downloads resume.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKER="$REPO/worker"
APP_DIR="$REPO/app"
TOOLS="$REPO/tools"
SA3_DIR="$TOOLS/stable-audio-3/optimized/mlx"
ACE_DIR="$TOOLS/ACE-Step-1.5"
APP_BUNDLE="$APP_DIR/build/Build/Products/Debug/RecapStudio.app"
BUNDLE_ID="com.thanhle.RecapStudio"
EXTERNAL_VOLUME="/Volumes/ExternalSSD"  # big data lives here when it is mounted (internal disk is small)

PYTHON_VERSION="3.12"
VL_MODEL="qwen2.5vl:7b"
WHISPER_MODEL="mlx-community/whisper-large-v3-turbo"
SA3_MODEL="medium"  # default settings.music_model
MIN_MACOS=14
MIN_FREE_GB=30
PULL_ATTEMPTS=30

WITH_ACESTEP=0
SKIP_MODELS=0
INSTALL_APP=0

for arg in "$@"; do
    case "$arg" in
        --with-acestep) WITH_ACESTEP=1 ;;
        --skip-models) SKIP_MODELS=1 ;;
        --install-app) INSTALL_APP=1 ;;
        -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Không hiểu tham số: $arg (xem ./setup.sh --help)" >&2; exit 2 ;;
    esac
done

# ---- output helpers ---------------------------------------------------------------

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# retry N CMD...: run CMD until it succeeds (flaky networks), at most N times
retry() {
    local attempts=$1 n=1
    shift
    until "$@"; do
        ((n >= attempts)) && return 1
        warn "Lần $n lỗi, thử lại sau 10 giây..."
        sleep 10
        n=$((n + 1))
    done
}

# ---- 1. machine ---------------------------------------------------------------------

check_machine() {
    step "Kiểm tra máy"
    [[ "$(uname -s)" == "Darwin" ]] || die "Chỉ chạy trên macOS."
    [[ "$(uname -m)" == "arm64" ]] || die "Cần Mac Apple Silicon (M1 trở lên): MLX không chạy trên Intel."
    local major
    major="$(sw_vers -productVersion | cut -d. -f1)"
    ((major >= MIN_MACOS)) || die "Cần macOS $MIN_MACOS trở lên (máy đang chạy $(sw_vers -productVersion))."
    ok "macOS $(sw_vers -productVersion), $(sysctl -n hw.memsize | awk '{printf "%d GB RAM", $1/1073741824}')"

    local free_gb
    free_gb="$(df -g "$REPO" | awk 'NR==2 {print $4}')"
    if ((free_gb < MIN_FREE_GB)); then
        warn "Ổ đĩa còn ${free_gb} GB trống; model và dự án cần khoảng ${MIN_FREE_GB} GB."
    else
        ok "Ổ đĩa còn ${free_gb} GB trống"
    fi

    xcode-select -p >/dev/null 2>&1 || die "Chưa có Xcode Command Line Tools: chạy 'xcode-select --install' rồi chạy lại."
    if xcodebuild -version >/dev/null 2>&1; then
        ok "$(xcodebuild -version | head -1)"
    else
        warn "Chưa có Xcode đầy đủ (cần để build app): cài Xcode từ App Store, rồi 'sudo xcode-select -s /Applications/Xcode.app'."
    fi
}

configure_storage() {
    step "Nơi lưu dữ liệu tải về"
    if [[ ! -d "$EXTERNAL_VOLUME" ]]; then
        warn "Không thấy $EXTERNAL_VOLUME: cache và model sẽ nằm ở ổ trong (~/.cache, ~/.ollama)."
        return
    fi
    # Explicit values from the caller win; everything else goes to the external SSD.
    : "${HF_HOME:=$EXTERNAL_VOLUME/hf-cache}"                    # Whisper, VieNeu, Stable Audio weights
    : "${OLLAMA_MODELS:=$EXTERNAL_VOLUME/ollama-models}"         # qwen2.5vl
    : "${UV_CACHE_DIR:=$EXTERNAL_VOLUME/uv-cache}"               # Python packages
    : "${UV_PYTHON_INSTALL_DIR:=$EXTERNAL_VOLUME/uv-python}"     # Python interpreters uv downloads
    : "${PIP_CACHE_DIR:=$EXTERNAL_VOLUME/pip-cache}"             # pip inside other tools' install scripts
    : "${XDG_CACHE_HOME:=$EXTERNAL_VOLUME/cache}"                # torch hub and other ~/.cache users
    : "${HOMEBREW_CACHE:=$EXTERNAL_VOLUME/homebrew-cache}"       # downloaded bottles (installs stay in brew's prefix)
    : "${TMPDIR:=$EXTERNAL_VOLUME/tmp}"                          # mktemp, pip/uv build dirs
    export HF_HOME OLLAMA_MODELS UV_CACHE_DIR UV_PYTHON_INSTALL_DIR PIP_CACHE_DIR XDG_CACHE_HOME HOMEBREW_CACHE TMPDIR
    mkdir -p "$HF_HOME" "$OLLAMA_MODELS" "$UV_CACHE_DIR" "$UV_PYTHON_INSTALL_DIR" "$PIP_CACHE_DIR" \
        "$XDG_CACHE_HOME" "$HOMEBREW_CACHE" "$TMPDIR"
    ok "Model và cache: $EXTERNAL_VOLUME"
}

# ---- 2. command-line tools ----------------------------------------------------------

install_tools() {
    step "Cài công cụ (Homebrew)"
    if ! command -v brew >/dev/null 2>&1; then
        die "Chưa có Homebrew. Cài theo https://brew.sh rồi chạy lại setup.sh."
    fi
    local formula
    for formula in ffmpeg ollama uv xcodegen aria2; do
        if brew list --formula "$formula" >/dev/null 2>&1; then
            ok "$formula"
        else
            brew install "$formula"
        fi
    done
    if command -v claude >/dev/null 2>&1; then
        ok "claude ($(claude --version 2>/dev/null | head -1))"
    else
        brew install --cask claude-code
        warn "Đã cài Claude Code: chạy 'claude' một lần để đăng nhập (stage viết kịch bản cần nó)."
    fi
}

# ---- 3. Python worker ---------------------------------------------------------------

setup_worker() {
    step "Worker Python (worker/.venv)"
    (cd "$WORKER" && uv sync --python "$PYTHON_VERSION")
    "$WORKER/.venv/bin/python" -c "import pydantic, mlx_whisper, scenedetect, vieneu, onnxruntime" \
        || die "Thư viện của worker chưa cài đủ (xem lỗi ở trên)."
    ok "worker/.venv"
}

# ---- 4. models ----------------------------------------------------------------------

start_ollama() {
    if ! curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
        # started from this shell so it inherits OLLAMA_MODELS (a `brew services` server would not,
        # and would pull models onto the internal disk); the worker restarts it on demand later
        mkdir -p "$REPO/output"
        nohup ollama serve >"$REPO/output/ollama-serve.log" 2>&1 &
    fi
    local n
    for n in $(seq 1 30); do
        curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1 && return 0
        sleep 1
    done
    die "Không khởi động được Ollama (xem output/ollama-serve.log)."
}

download_models() {
    step "Model nhận dạng giọng nói (Whisper)"
    retry 5 "$WORKER/.venv/bin/python" -c \
        "from huggingface_hub import snapshot_download; snapshot_download('$WHISPER_MODEL')" >/dev/null
    ok "$WHISPER_MODEL"

    step "Model giọng đọc (VieNeu-TTS)"
    local sample
    sample="$(mktemp -d)/voice.wav"
    (cd "$WORKER" && retry 3 .venv/bin/python -m recap voice-preview --out "$sample" >/dev/null 2>&1) \
        || die "Không tạo được giọng đọc thử (VieNeu). Chạy: cd worker && .venv/bin/python -m recap voice-preview --out /tmp/v.wav"
    ok "VieNeu-TTS"

    step "Model mô tả cảnh (Ollama $VL_MODEL, ≈ 6 GB)"
    start_ollama
    if ollama list | awk '{print $1}' | grep -qx "$VL_MODEL"; then
        ok "$VL_MODEL đã có"
    else
        # the registry connection is sometimes reset: each attempt resumes the download
        retry "$PULL_ATTEMPTS" ollama pull "$VL_MODEL" || die "Không tải được $VL_MODEL. Thử bật VPN/đổi DNS rồi chạy lại."
        ok "$VL_MODEL"
    fi
}

setup_stable_audio() {
    step "Nhạc nền AI: Stable Audio 3 (MLX)"
    if [[ ! -x "$SA3_DIR/sa3" ]]; then
        mkdir -p "$TOOLS"
        rm -rf "$TOOLS/stable-audio-3"
        git clone --depth 1 --filter=blob:none --sparse https://github.com/Stability-AI/stable-audio-3.git "$TOOLS/stable-audio-3"
        git -C "$TOOLS/stable-audio-3" sparse-checkout set optimized/mlx
    fi
    if [[ ! -d "$SA3_DIR/.venv" ]]; then
        (cd "$SA3_DIR" && ./install.sh -y --python "$PYTHON_VERSION" </dev/null)
    fi
    ok "tools/stable-audio-3"
    if ((SKIP_MODELS)); then return; fi
    # a 2-second track downloads the $SA3_MODEL weights (≈ 6 GB) and proves the engine works
    local check
    check="$(mktemp -d)/sa3_check.wav"
    (cd "$SA3_DIR" && retry 3 ./sa3 --prompt "soft piano, instrumental" --dit "$SA3_MODEL" \
        --decoder "$([[ $SA3_MODEL == medium ]] && echo same-l || echo same-s)" \
        --seconds 2 --seed 1 --out "$check" </dev/null >/dev/null) \
        || die "Stable Audio 3 chưa chạy được (xem: cd tools/stable-audio-3/optimized/mlx && ./sa3 --help)."
    ok "Stable Audio 3 $SA3_MODEL"
}

setup_acestep() {
    step "Nhạc nền dự phòng: ACE-Step 1.5"
    if [[ ! -d "$ACE_DIR/.git" ]]; then
        mkdir -p "$TOOLS"
        git clone --depth 1 https://github.com/ace-step/ACE-Step-1.5.git "$ACE_DIR"
    fi
    (cd "$ACE_DIR" && uv sync --python "$PYTHON_VERSION")
    ok "tools/ACE-Step-1.5 (model ≈ 10 GB tải ở lần tạo nhạc đầu tiên)"
}

# ---- 5. app -------------------------------------------------------------------------

build_app() {
    step "Build RecapStudio.app"
    xcodebuild -version >/dev/null 2>&1 || { warn "Bỏ qua: chưa có Xcode."; return; }
    (cd "$APP_DIR" && xcodegen generate --quiet \
        && xcodebuild -project RecapStudio.xcodeproj -scheme RecapStudio -destination "platform=macOS,arch=arm64" \
        -derivedDataPath build build -quiet)
    # built from source on this Mac: no quarantine (a copied build folder may carry it, and macOS
    # would then run the app from a read-only copy without showing its window)
    xattr -dr com.apple.quarantine "$APP_BUNDLE" 2>/dev/null || true
    ok "$APP_BUNDLE"

    local bundle="$APP_BUNDLE"
    if ((INSTALL_APP)); then
        rm -rf "/Applications/RecapStudio.app"
        cp -R "$APP_BUNDLE" /Applications/
        bundle="/Applications/RecapStudio.app"
        ok "Đã chép vào /Applications"
    fi
    /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$bundle" || true
}

configure_app() {
    step "Cài đặt của app trên máy này"
    # The app's built-in defaults point at the author's external SSD; use this checkout instead.
    defaults write "$BUNDLE_ID" workerDirectory "$WORKER"
    defaults write "$BUNDLE_ID" pythonPath "$WORKER/.venv/bin/python"
    defaults write "$BUNDLE_ID" projectsRoot "$REPO/output"
    defaults write "$BUNDLE_ID" toolPath "$(brew --prefix)/bin:/usr/local/bin"
    defaults write "$BUNDLE_ID" musicRoot "$REPO/music"
    defaults write "$BUNDLE_ID" brandFolder "$REPO/img"
    mkdir -p "$REPO/output"
    ok "Thư mục dự án: $REPO/output"
}

# ---- 6. check -----------------------------------------------------------------------

final_check() {
    step "Kiểm tra môi trường (recap doctor)"
    # doctor prints one ✅/❌ line per check on stderr; the JSON result on stdout is not needed here
    (cd "$WORKER" && .venv/bin/python -m recap doctor --root "$REPO/output" >/dev/null) \
        || die "Còn thiếu thành phần (xem dòng ❌ ở trên)."
}

check_machine
configure_storage
install_tools
setup_worker
if ((SKIP_MODELS)); then warn "Bỏ qua tải model (--skip-models): model sẽ tải ở lần dùng đầu tiên."; else download_models; fi
setup_stable_audio
if ((WITH_ACESTEP)); then setup_acestep; fi
build_app
configure_app
final_check

step "Xong"
echo "  Mở app:  open \"$([[ $INSTALL_APP == 1 ]] && echo /Applications/RecapStudio.app || echo "$APP_BUNDLE")\""
echo "  Nếu chưa đăng nhập Claude Code (stage viết kịch bản cần nó): chạy 'claude' một lần."
