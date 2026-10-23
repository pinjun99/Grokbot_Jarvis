"""Settings stored in ~/.jarvis (outside the repo, so your secret key never gets committed)."""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime
from pathlib import Path

from .permissions import DEFAULT_MODE

HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
CONFIG_FILE = HOME / "config.json"
PAUSE_FILE = HOME / "PAUSED"
LOG_FILE = HOME / "activity.log"
URL_FILE = HOME / "connector-url.txt"

DEFAULTS = {
    "mode": DEFAULT_MODE,
    "port": 8765,
    "voice": "Daniel",           # macOS backup voice (British male, closest to the films' Jarvis)
    "speech_voice_provider": "auto",  # grok | mac | auto (= grok once an xAI key is saved)
    "xai_api_key": "",           # only for Grok's voice (text-to-speech); set with: jarvis voice grok-key
    "grok_voice": "eve",         # eve, ara, leo, rex, sal, or a custom voice id
    "screenshot_width": 1280,
    "tunnel": "cloudflare",      # cloudflare | ngrok | none
    "ngrok_domain": "",          # e.g. "my-jarvis.ngrok-free.app" for a URL that never changes
    "voice_enabled": False,
    "wake_phrases": ["hey jarvis"],
    "voice_webhook_url": "",     # the Grok Bot routine webhook that receives what you say
    "voice_webhook_headers": {},
    "speech_model": "small",     # offline Whisper model: tiny, base, small, medium
    "speech_language": "",       # e.g. "en"; empty = detect automatically
}


def new_key() -> str:
    return secrets.token_urlsafe(24)


def load() -> dict:
    HOME.mkdir(parents=True, exist_ok=True)
    data = {}
    if CONFIG_FILE.exists():
        data = json.loads(CONFIG_FILE.read_text())
    merged = {**DEFAULTS, **data}
    if not merged.get("key"):
        merged["key"] = new_key()
        save(merged)
    return merged


def save(cfg: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + "\n")
    CONFIG_FILE.chmod(0o600)


def update(**changes) -> dict:
    cfg = load()
    cfg.update(changes)
    save(cfg)
    return cfg


def current_mode() -> str:
    return load()["mode"]


def is_paused() -> bool:
    return PAUSE_FILE.exists()


def log(message: str) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a") as f:
        one_line = " ⏎ ".join(part.strip() for part in message.splitlines())
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {one_line}\n")
