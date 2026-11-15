#!/usr/bin/env bash
# CyberDeck installer: Python venv + optional local LLM (Ollama).
# Safe to re-run. Never runs sudo without asking first.
set -euo pipefail

cd "$(dirname "$0")"

MODEL="${CYBERDECK_LLM_MODEL:-qwen2.5:1.5b}"
LLM_CHOICE=""
OLLAMA_INSTALLER_URL="https://ollama.com/install.sh"

info() { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# --- progress --------------------------------------------------------------
TOTAL_STEPS=4
STEP=0
BAR_WIDTH=20

step() {
    STEP=$((STEP + 1))
    label="$1"
    if [ -t 1 ]; then
        filled=$((STEP * BAR_WIDTH / TOTAL_STEPS))
        bar=""
        i=0
        while [ "$i" -lt "$BAR_WIDTH" ]; do
            if [ "$i" -lt "$filled" ]; then bar="${bar}#"; else bar="${bar}-"; fi
            i=$((i + 1))
        done
        printf '[%s] %d/%d %s\n' "$bar" "$STEP" "$TOTAL_STEPS" "$label"
    else
        # Redirection (CI, logs): no bar, no escape codes.
        printf '==> [%d/%d] %s\n' "$STEP" "$TOTAL_STEPS" "$label"
    fi
}

usage() {
    cat <<'EOF'
Usage: ./install.sh [options]

Sets up ./.venv, installs dependencies, and can download a local LLM.

Options:
  --with-llm       Install Ollama if missing and pull the model, no prompt
  --no-llm         Skip everything LLM-related
  --model NAME     Model to pull (default: qwen2.5:1.5b)
  -h, --help       Show this help

Environment:
  CYBERDECK_LLM_MODEL   Default model name (same as --model)

Without --with-llm/--no-llm the script asks once, and skips the LLM in
non-interactive use.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --with-llm) LLM_CHOICE=yes ;;
        --no-llm) LLM_CHOICE=no ;;
        --model)
            shift
            [ $# -gt 0 ] || die "--model needs a value"
            case "$1" in
                *[![:space:]]*) MODEL="$1" ;;
                *) die "--model needs a value" ;;
            esac
            ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
    shift
done

# --- 1. Python -------------------------------------------------------------
step "Python"
command -v python3 >/dev/null 2>&1 || die "python3 not found"
python3 - <<'PY' || die "Python 3.10 or newer is required"
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
info "Python $(python3 -c 'import platform; print(platform.python_version())')"

# --- 2. Virtualenv ---------------------------------------------------------
step "Virtual environment"
if [ ! -x .venv/bin/python ]; then
    info "Creating .venv"
    python3 -m venv .venv || die "venv failed; install python3-venv (Debian/Ubuntu)"
else
    info ".venv already exists"
fi
chmod +x cyberdeck

# --- 3. Dependencies -------------------------------------------------------
step "Python dependencies"
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt

# --- 4. Optional LLM -------------------------------------------------------
if [ -z "$LLM_CHOICE" ]; then
    if [ -t 0 ]; then
        printf 'Download the local LLM (Ollama + %s, about 1 GB)? [y/N] ' "$MODEL"
        read -r answer || answer=""
        case "$answer" in
            y|Y|yes|YES) LLM_CHOICE=yes ;;
            *) LLM_CHOICE=no ;;
        esac
    else
        LLM_CHOICE=no
    fi
fi

if [ "$LLM_CHOICE" = yes ]; then
    step "Local LLM ($MODEL)"
else
    step "Local LLM (skipped)"
fi

if [ "$LLM_CHOICE" = yes ]; then
    if ! command -v ollama >/dev/null 2>&1; then
        info "Ollama is not installed; it comes from $OLLAMA_INSTALLER_URL"
        command -v curl >/dev/null 2>&1 || die "curl is required to download the Ollama installer"

        # Downloaded first, not piped into a shell, so it can be inspected.
        mkdir -p .tmp
        tmp=".tmp/ollama-install.sh"
        curl -fsSL "$OLLAMA_INSTALLER_URL" -o "$tmp" || die "could not download the Ollama installer"
        info "Downloaded installer: $tmp"
        info "sha256: $(sha256sum "$tmp" 2>/dev/null | cut -d' ' -f1 || echo unavailable)"
        printf 'Run that installer as root with sudo? [y/N] ' >&2
        read -r ok || ok=""
        case "$ok" in
            y|Y|yes|YES)
                sudo sh "$tmp" || warn "Ollama installation failed"
                ;;
            *)
                info "Skipped Ollama installation"
                ;;
        esac
        rm -f "$tmp"
    fi

    if command -v ollama >/dev/null 2>&1; then
        info "Pulling model $MODEL (this is the slow part: ~1 GB)"
        if ! ollama pull "$MODEL"; then
            warn "pull failed; is the service running? try: sudo systemctl start ollama"
        fi
    fi
fi

# --- Hints ---------------------------------------------------------------
if ! command -v tailscale >/dev/null 2>&1; then
    info "Tailscale not found: the web UI will only bind 127.0.0.1"
fi

info "Done. Start with: ./cyberdeck"
