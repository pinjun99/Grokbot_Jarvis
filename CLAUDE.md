# Notes for Claude working on this repo

The owner is non-technical. Explain things plainly, give exact click-by-click steps, and
do the technical work for them.

## What this is

Jarvis is an MCP server (Streamable HTTP, `mcp` Python SDK 2.x, `MCPServer`) that runs on
the owner's Mac (M4, macOS 26.5) and lets **Grok Bot** (xAI's agent app; the owner's bots
are e.g. "Elon") see and control the Mac. Grok is the only AI in the loop. Do not add another LLM unless
the owner asks.

What Grok Bot told us (Oct 2026): it can run shell commands on the Mac (each approved)
but has no Screen Recording, so it can't see or click the Mac itself. Custom MCP
connectors must be public https URLs (they run on xAI's backend) and support no auth,
static headers, or OAuth. It's unsure whether inline MCP images reach the model, so
`screenshot` also returns a 10-minute link (`ShotStore`, `/shots/<random>.jpg`). Routines
can be triggered by a webhook. The Grok Bot app also has a live voice chat (Grok speaks
in its own voice), which is what "hey <bot name>" opens by default.

Flow: Grok Bot (xAI cloud) → Cloudflare quick tunnel (or ngrok) → `127.0.0.1:8765` →
`KeyGuard` (secret key in path `/<key>/mcp`, or `Authorization: Bearer <key>` on `/mcp`)
→ MCP tools in `jarvis/server.py` → `Gatekeeper` (`jarvis/permissions.py`) →
`MacController` (`jarvis/mac.py`, pyobjc Quartz/AppKit/Vision, `screencapture`, `osascript`).

- Coordinates: tools take screenshot pixels. Screenshots are the main display, resized to
  ≤1280 px wide in *points* space. `MacController._to_points` divides by `scale`.
- Permission modes: watch / ask / assist (default) / autopilot. Risk levels: OBSERVE,
  CONTROL, SENSITIVE, CRITICAL. CRITICAL always shows the macOS pop-up. Grok must never be
  able to change the mode. Keep it CLI-only (`jarvis mode`).
- State lives in `~/.jarvis/` (config.json with the secret key, activity.log, PAUSED flag,
  tunnel.log, bin/cloudflared). Never commit it.
- Voice (`jarvis/voice.py`): sounddevice mic → loudness `Segmenter` → offline
  faster-whisper → fuzzy `match_wake` against `config.wake_phrases(cfg)` (user's own
  `wake_phrases`, else "hey <grok_bot_name>", e.g. "hey elon"; the old default
  ["hey jarvis"] counts as unset) → then by
  `wake_action`: `voice_chat` (default) opens a live voice chat in the Grok Bot app with
  `grok_bot_name` (`jarvis/grokbot.py`, presses its "Start voice chat" button through the
  accessibility API; Grok Bot is Electron, bundle id `com.anysphere.sand`, so we set
  `AXManualAccessibility` first). While its "Hang up" button exists the listener ignores
  speech. `webhook` instead POSTs `{"source","text","message"}` to a routine webhook
  (`voice_webhook_url`). Runs in a thread inside `jarvis start` when `voice_enabled`.
  Whisper is only the ears; Grok Bot stays the only brain.
- Speaking (`jarvis/speech.py`): everything Jarvis says (the `speak` tool via
  `MacController.speak`, and voice.py's spoken status lines) goes through
  `speech.speak()`: one shared queue + background thread, so it never blocks and two
  sentences never overlap. With `xai_api_key` set (and `speech_voice_provider` grok/auto) it
  POSTs to xAI TTS `https://api.x.ai/v1/tts` `{"text","voice_id": grok_voice,"language"}`
  (reply is MP3, played with `afplay`); on any error, or provider `mac`, it uses
  `say -v <voice>` and logs why. This is **TTS only**: it gives Jarvis Grok's voice, Grok
  Bot stays the only brain. Never send xAI anything but the words to speak.
  In `voice_chat` mode Grok Bot already talks in its own voice inside the app; this TTS
  is for the `speak` tool and Jarvis's own status lines.
- Tool errors are re-raised as `ToolError` so Grok sees the reason (the SDK hides other
  exceptions' messages).

## Commands

```
uv sync                 # install deps (dev group included)
uv run pytest -q        # tests (fake Mac, real HTTP server); work on any OS
uv run jarvis --help
```

## Already verified without a Mac (Oct 2026)

- Every pyobjc symbol used in `mac.py` exists in pyobjc 12.2.2 (checked against the macOS wheels).
- faster-whisper 1.2.1 and sounddevice APIs match our calls; sounddevice's macOS wheel bundles PortAudio.
- Python is pinned to 3.12 (`.python-version`, `install.sh`), because ctranslate2/onnxruntime have no 3.15 wheels.
- `install.sh` ran end to end with a faked `uname`; shellcheck clean; cloudflared download URL works.
- `jarvis start` ran end to end with a fake Mac: Bearer-header connect, 21 tools, screenshot + link,
  deny pop-up on `rm -rf`, 404 without the key, clear message if the tunnel fails or Jarvis is already running.

## First run on the real Mac (done 23 Oct 2026, M4 MacBook Air, macOS 26.5.2)

- `./install.sh` run by the owner in Terminal.app; Terminal had to be added to
  Screen & System Audio Recording by hand with "+". `jarvis doctor` all ✓.
- `scripts/mac_smoke_test.py` (owner runs it in Terminal) passes every `MacController`
  method: screenshot, OCR, mouse lines up with screenshot pixels (1470x956 pt screen,
  1280x832 shots), type_text with é/emoji, click/double-click on OCR coordinates, drag,
  scroll, cmd+t in Safari, notify, confirm, speak. No bugs found in `mac.py`. Note:
  TextEdit auto-capitalises line starts, so compare text case-insensitively.
- "Hey Jarvis" → Grok Bot voice chat with Elon works from `jarvis voice listen`.
- Still to do: `jarvis start` and test the connector from Grok Bot (incl. inside a
  voice chat, so Grok can use Jarvis while talking).
- Still to do: Grok's voice for `speak` (`jarvis/speech.py`). Owner gets a key at
  console.x.ai > API Keys (TTS costs roughly $15 per 1M characters), runs
  `jarvis voice grok-key`, pastes it, then `jarvis voice say "Hello"`. `jarvis voice speaker`
  lists/chooses voices (eve default, ara, leo, rex, sal; `mac` goes back to `say`). Never run
  against the real xAI API yet: check `afplay` plays the MP3 and the voice list parses.

Known unknowns:
- The exact JSON a Grok Bot routine webhook expects. We send `message` (a full
  instruction), `text` and `source`. Adjust `send_to_grok` if the routine needs another
  shape.
- `speech_model` is `small` (~480 MB). Now that it only spots the wake phrase, `base`
  (~145 MB) is probably enough. The owner already has openai-whisper `.pt` and
  whisper.cpp ggml models, but faster-whisper can't read those.
- How long xAI TTS takes before Grok's voice starts (whole MP3 is fetched first). If it
  feels slow, the WebSocket streaming endpoint (`wss://api.x.ai/v1/tts`) is the next step.

## Ideas for next versions

- Multi-display screenshots; accessibility-tree reading of the frontmost app.
- Menu bar app instead of a Terminal window; launch at login.
