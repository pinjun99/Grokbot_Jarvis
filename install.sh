#!/bin/bash
# One-time setup for Jarvis. Run from Terminal:  ./install.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
JARVIS_HOME="${JARVIS_HOME:-$HOME/.jarvis}"
mkdir -p "$JARVIS_HOME/bin"

say_step() { printf "\n\033[1;36m==> %s\033[0m\n" "$1"; }

if [[ "$(uname)" != "Darwin" ]]; then
  echo "Jarvis only works on a Mac."; exit 1
fi

say_step "1/4  Installing uv (manages Python for Jarvis)"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv --version

say_step "2/4  Installing Jarvis"
uv tool install --force --editable "$REPO"
uv tool update-shell >/dev/null 2>&1 || true
export PATH="$HOME/.local/bin:$PATH"

say_step "3/4  Installing cloudflared (the secure tunnel Grok connects through)"
if command -v cloudflared >/dev/null 2>&1 || [[ -x "$JARVIS_HOME/bin/cloudflared" ]]; then
  echo "Already installed."
else
  ARCH="$(uname -m)"; [[ "$ARCH" == "x86_64" ]] && ARCH="amd64"
  TMP="$(mktemp -d)"
  curl -fL --progress-bar -o "$TMP/cf.tgz" \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-$ARCH.tgz"
  tar -xzf "$TMP/cf.tgz" -C "$TMP"
  mv "$TMP/cloudflared" "$JARVIS_HOME/bin/cloudflared"
  chmod +x "$JARVIS_HOME/bin/cloudflared"
  xattr -d com.apple.quarantine "$JARVIS_HOME/bin/cloudflared" 2>/dev/null || true
  rm -rf "$TMP"
  "$JARVIS_HOME/bin/cloudflared" --version
fi

say_step "4/4  Checking macOS permissions"
jarvis doctor || true

cat <<'DONE'

------------------------------------------------------------------
 Setup finished.

 If any permission above showed ✗, grant it in System Settings,
 quit Terminal (cmd+Q), reopen it, and run:   jarvis doctor

 Then start Jarvis any time by double-clicking  "Start Jarvis.command"
 in this folder, or by typing:                  jarvis start
------------------------------------------------------------------
DONE
