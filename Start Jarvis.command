#!/bin/bash
# Double-click me in Finder to start Jarvis.
export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")"
if ! command -v jarvis >/dev/null 2>&1; then
  echo "Jarvis isn't installed yet. Running the installer first..."
  ./install.sh
fi
jarvis start
