#!/bin/sh
# install.sh — Mithai binary installer
# Usage: curl -fsSL https://get.mithai.dev | sh
#
# Environment variables:
#   MITHAI_INSTALL_DIR  — override install directory (default: /usr/local/bin)
#   MITHAI_VERSION      — override version (default: latest)

set -euf

REPO="nishantmodak/mithai"
INSTALL_DIR="${MITHAI_INSTALL_DIR:-/usr/local/bin}"

# ─── Helpers ──────────────────────────────────────────────────────────────────

info()  { printf "  %s\n" "$@"; }
error() { printf "  ERROR: %s\n" "$@" >&2; exit 1; }

detect_platform() {
    OS=$(uname -s | tr '[:upper:]' '[:lower:]')
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64|amd64)  ARCH="amd64" ;;
        arm64|aarch64) ARCH="arm64" ;;
        *)             error "Unsupported architecture: $ARCH" ;;
    esac
    case "$OS" in
        darwin|linux) ;;
        *)            error "Unsupported OS: $OS (only macOS and Linux are supported)" ;;
    esac
    echo "${OS}-${ARCH}"
}

has_cmd() { command -v "$1" >/dev/null 2>&1; }

# ─── Main ─────────────────────────────────────────────────────────────────────

PLATFORM=$(detect_platform)

echo ""
echo "  Mithai Installer"
echo "  ─────────────────"
info "Platform: ${PLATFORM}"

# Determine version
if [ -n "${MITHAI_VERSION:-}" ]; then
    VERSION="$MITHAI_VERSION"
else
    if has_cmd curl; then
        VERSION=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" | grep '"tag_name"' | cut -d'"' -f4)
    elif has_cmd wget; then
        VERSION=$(wget -qO- "https://api.github.com/repos/${REPO}/releases/latest" | grep '"tag_name"' | cut -d'"' -f4)
    else
        error "curl or wget is required"
    fi
fi

if [ -z "$VERSION" ]; then
    error "Could not determine latest version. Set MITHAI_VERSION manually."
fi

info "Version: ${VERSION}"

URL="https://github.com/${REPO}/releases/download/${VERSION}/mithai-${PLATFORM}"
CHECKSUM_URL="https://github.com/${REPO}/releases/download/${VERSION}/checksums.txt"

info "Downloading mithai-${PLATFORM}..."

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

if has_cmd curl; then
    curl -fsSL "$URL" -o "${TMPDIR}/mithai"
    curl -fsSL "$CHECKSUM_URL" -o "${TMPDIR}/checksums.txt" 2>/dev/null || true
elif has_cmd wget; then
    wget -q "$URL" -O "${TMPDIR}/mithai"
    wget -q "$CHECKSUM_URL" -O "${TMPDIR}/checksums.txt" 2>/dev/null || true
fi

# Verify checksum if available
if [ -f "${TMPDIR}/checksums.txt" ]; then
    EXPECTED=$(grep "mithai-${PLATFORM}" "${TMPDIR}/checksums.txt" | awk '{print $1}')
    if [ -n "$EXPECTED" ]; then
        if has_cmd sha256sum; then
            ACTUAL=$(sha256sum "${TMPDIR}/mithai" | awk '{print $1}')
        elif has_cmd shasum; then
            ACTUAL=$(shasum -a 256 "${TMPDIR}/mithai" | awk '{print $1}')
        else
            ACTUAL=""
        fi
        if [ -n "$ACTUAL" ] && [ "$EXPECTED" != "$ACTUAL" ]; then
            error "Checksum mismatch! Expected: ${EXPECTED}, got: ${ACTUAL}"
        fi
        if [ -n "$ACTUAL" ]; then
            info "Checksum: verified"
        fi
    fi
fi

chmod +x "${TMPDIR}/mithai"

# Install
if [ -w "$INSTALL_DIR" ]; then
    mv "${TMPDIR}/mithai" "${INSTALL_DIR}/mithai"
else
    info "Need sudo to install to ${INSTALL_DIR}"
    sudo mv "${TMPDIR}/mithai" "${INSTALL_DIR}/mithai"
fi

info ""
info "✓ mithai ${VERSION} installed to ${INSTALL_DIR}/mithai"
info ""
info "Next steps:"
info "  mithai init      — interactive setup wizard"
info "  mithai doctor    — verify your configuration"
info "  mithai --help    — see all commands"
echo ""
