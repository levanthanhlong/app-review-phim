#!/usr/bin/env bash
# Build RecapStudio.app (Release) and package it as dist/RecapStudio-<version>.dmg.
#
#   ./make_dmg.sh                 ad-hoc signed DMG (enough to run on your own Macs)
#   ./make_dmg.sh --choose-cert   pick a signing certificate from the Keychain in a menu
#   ./make_dmg.sh --cert <SHA-1 or name>   sign with that certificate
#   ./make_dmg.sh --open          also open the DMG when done
#
# A real certificate (e.g. "Apple Development") keeps the app's identity stable across builds, so
# macOS remembers the folder permissions it granted. Revoked certificates are not offered.
# Only for Developer ID certificates, to share without Gatekeeper warnings:
#   NOTARY_PROFILE="recap-notary" ./make_dmg.sh --cert "Developer ID Application: ..."
#     (create the profile once: xcrun notarytool store-credentials recap-notary)
#
# The DMG holds only the app: it runs the Python worker of this repo (worker/.venv), so the
# Mac that installs it also needs the repo and ./setup.sh (paths are set in the app's Settings).

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$REPO/app"
DIST="$REPO/dist"
APP_NAME="RecapStudio"
BUILD_DIR="$APP_DIR/build"  # on the same disk as the repo (the internal disk is small)
APP_BUNDLE="$BUILD_DIR/Build/Products/Release/$APP_NAME.app"
EXTERNAL_VOLUME="/Volumes/ExternalSSD"

OPEN_DMG=0
CHOOSE_CERT=0
SIGN_IDENTITY="${SIGN_IDENTITY:-}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --open) OPEN_DMG=1 ;;
        --choose-cert) CHOOSE_CERT=1 ;;
        --cert)
            [[ $# -ge 2 && -n "$2" ]] || { echo "--cert cần SHA-1 hoặc tên chứng chỉ" >&2; exit 2; }
            SIGN_IDENTITY="$2"
            shift ;;
        -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Không hiểu tham số: $1 (xem ./make_dmg.sh --help)" >&2; exit 2 ;;
    esac
    shift
done

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

check_tools() {
    step "Kiểm tra công cụ"
    [[ "$(uname)" == "Darwin" ]] || die "Chỉ chạy được trên macOS"
    for tool in xcodebuild xcodegen hdiutil codesign; do
        command -v "$tool" >/dev/null || die "Thiếu $tool (chạy ./setup.sh để cài)"
    done
    ok "Đủ công cụ"
}

# Usable signing certificates, one "SHA1<TAB>name" per line (revoked or expired ones left out).
list_certs() {
    security find-identity -v -p codesigning \
        | grep -v 'CSSMERR_' \
        | sed -n 's/^ *[0-9][0-9]*) \([0-9A-F]\{40\}\) "\(.*\)"$/\1	\2/p' \
        | awk -F '\t' '!seen[$1]++'
}

choose_cert() {
    step "Chọn chứng chỉ ký"
    local certs=() line answer
    while IFS= read -r line; do certs+=("$line"); done < <(list_certs)
    echo "  0) Ad-hoc (không cần chứng chỉ, đủ để chạy trên máy của bạn)"
    local i
    for i in "${!certs[@]}"; do
        printf '  %d) %s\n' $((i + 1)) "$(cut -f2 <<<"${certs[$i]}")"
    done
    read -r -p "  Chọn số [0]: " answer
    answer="${answer:-0}"
    [[ "$answer" =~ ^[0-9]+$ && "$answer" -le ${#certs[@]} ]] || die "Lựa chọn không hợp lệ: $answer"
    if [[ "$answer" -eq 0 ]]; then
        SIGN_IDENTITY=""
    else
        SIGN_IDENTITY="$(cut -f1 <<<"${certs[$((answer - 1))]}")"
    fi
}

# Resolve SIGN_IDENTITY (SHA-1 or part of a name) to exactly one usable certificate.
resolve_cert() {
    [[ -n "$SIGN_IDENTITY" ]] || { SIGN_NAME="ad-hoc"; return; }
    local matches
    matches="$(list_certs | grep -F -- "$SIGN_IDENTITY" || true)"
    [[ -n "$matches" ]] || die "Không thấy chứng chỉ còn hiệu lực khớp \"$SIGN_IDENTITY\" (xem: ./make_dmg.sh --choose-cert)"
    [[ "$(wc -l <<<"$matches")" -eq 1 ]] || die "Có nhiều chứng chỉ khớp \"$SIGN_IDENTITY\": dùng mã SHA-1 hoặc --choose-cert"
    SIGN_IDENTITY="$(cut -f1 <<<"$matches")"
    SIGN_NAME="$(cut -f2 <<<"$matches")"
    if [[ -n "${NOTARY_PROFILE:-}" && "$SIGN_NAME" != "Developer ID Application:"* ]]; then
        die "Notarize chỉ dùng được với chứng chỉ Developer ID Application (đang chọn: $SIGN_NAME)"
    fi
}

# Temporary files on the external SSD when it is mounted (the internal disk is nearly full).
make_staging() {
    local base="${TMPDIR:-/tmp}"
    [[ -d "$EXTERNAL_VOLUME" ]] && base="$EXTERNAL_VOLUME/tmp" && mkdir -p "$base"
    STAGING="$(mktemp -d "$base/recap-dmg.XXXXXX")"
    trap 'rm -rf "$STAGING"' EXIT
}

read_version() {
    VERSION="$(sed -n 's/^ *MARKETING_VERSION: *"\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' "$APP_DIR/project.yml" | head -1)"
    BUILD="$(sed -n 's/^ *CURRENT_PROJECT_VERSION: *"\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' "$APP_DIR/project.yml" | head -1)"
    [[ -n "$VERSION" ]] || die "Không đọc được MARKETING_VERSION trong app/project.yml"
    DMG="$DIST/$APP_NAME-$VERSION.dmg"
}

build_app() {
    step "Build $APP_NAME $VERSION ($BUILD) bản Release"
    (cd "$APP_DIR" && xcodegen generate --quiet)
    rm -rf "$APP_BUNDLE"
    xcodebuild -project "$APP_DIR/$APP_NAME.xcodeproj" -scheme "$APP_NAME" -configuration Release \
        -destination "platform=macOS,arch=arm64" -derivedDataPath "$BUILD_DIR" build -quiet \
        || die "Build lỗi (chạy lại không có -quiet để xem chi tiết)"
    [[ -d "$APP_BUNDLE" ]] || die "Không thấy $APP_BUNDLE sau khi build"
    ok "$APP_BUNDLE"
}

sign_app() {
    local app="$STAGING/$APP_NAME.app"
    if [[ -z "$SIGN_IDENTITY" ]]; then
        ok "Ký ad-hoc (chạy trên máy của bạn; copy sang máy khác thì lần đầu: chuột phải > Mở)"
        return
    fi
    step "Ký bằng $SIGN_NAME"
    local extra=()
    # Hardened runtime and a secure timestamp: required for notarization, only then.
    [[ -n "${NOTARY_PROFILE:-}" ]] && extra=(--options runtime --timestamp)
    codesign --force --deep "${extra[@]+"${extra[@]}"}" --sign "$SIGN_IDENTITY" "$app" \
        || die "Ký lỗi (Keychain có thể hỏi mật khẩu để cho codesign dùng chứng chỉ)"
    codesign --verify --strict --deep "$app" || die "Chữ ký của app không hợp lệ"
    ok "Đã ký app"
}

make_dmg() {
    step "Tạo DMG"
    ditto "$APP_BUNDLE" "$STAGING/$APP_NAME.app"
    sign_app
    ln -s /Applications "$STAGING/Applications"  # drag the app here to install
    mkdir -p "$DIST"
    rm -f "$DMG"
    hdiutil create -quiet -volname "$APP_NAME $VERSION" -srcfolder "$STAGING" -fs HFS+ -format UDZO -ov "$DMG" \
        || die "hdiutil không tạo được DMG"
    if [[ -n "$SIGN_IDENTITY" ]]; then
        codesign --force --sign "$SIGN_IDENTITY" "$DMG" || die "Không ký được DMG"
    fi
    hdiutil verify -quiet "$DMG" || die "DMG hỏng (hdiutil verify)"
    ok "$DMG"
}

notarize() {
    [[ -n "${NOTARY_PROFILE:-}" ]] || return 0
    step "Notarize với Apple (vài phút)"
    xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait \
        || die "Notarize lỗi (xem: xcrun notarytool log <id> --keychain-profile $NOTARY_PROFILE)"
    xcrun stapler staple "$DMG" || die "Không gắn được vé notarize vào DMG"
    ok "Đã notarize và staple"
}

summary() {
    step "Xong"
    ok "$(du -h "$DMG" | cut -f1)  $DMG"
    ok "Chữ ký: $SIGN_NAME"
    ok "SHA-256: $(shasum -a 256 "$DMG" | cut -d' ' -f1)"
    echo "  Cài: mở DMG, kéo $APP_NAME vào Applications."
    echo "  Máy cài cần repo này và ./setup.sh; đường dẫn worker đặt trong Cài đặt của app."
    if [[ $OPEN_DMG -eq 1 ]]; then open "$DMG"; fi
}

check_tools
if [[ $CHOOSE_CERT -eq 1 ]]; then choose_cert; fi
resolve_cert
make_staging
read_version
build_app
make_dmg
notarize
summary
