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
can be triggered by a webhook, which is how voice reaches Grok Bot.

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
  faster-whisper → fuzzy `match_wake` against user-chosen `wake_phrases` → POST
  `{"source","text","message"}` to the Grok Bot routine webhook (`voice_webhook_url`).
  Runs in a thread inside `jarvis start` when `voice_enabled`. Whisper is only the ears;
  Grok Bot stays the only brain.
- Tool errors are re-raised as `ToolError` so Grok sees the reason (the SDK hides other
  exceptions' messages).

## Commands

```
uv sync                 # install deps (dev group included)
uv run pytest -q        # tests (fake Mac, real HTTP server); work on any OS
uv run jarvis --help
```

## First run on the real Mac: TODO, never done yet

`jarvis/mac.py` was written without access to a Mac. On the owner's Mac:

1. Ask the owner to run `./install.sh` **in Terminal.app themselves**. macOS grants
   Accessibility/Screen Recording to the app that launches the process. If Jarvis runs
   from Claude's own shell, the permissions attach to the Claude app instead, which is
   confusing.
2. `jarvis doctor` must show all ✓ (it also takes a screenshot and runs OCR).
3. Smoke-test each `MacController` method from Python (`uv run python`): `screenshot()`,
   `read_screen_text()`, `click()` on a known spot, `type_text("héllo 👋")` into TextEdit,
   `press_keys("cmd+t")` in Safari, `scroll`, `drag`, `confirm()`, `speak()`.
   Check click coordinates line up with what the screenshot shows (Retina scaling).
4. `jarvis start`, then have the owner send the printed message to their Grok Bot, and
   test from there.
5. Voice: `jarvis voice listen` (owner in Terminal, mic permission). Tune `Segmenter`
   sensitivity and the `speech_model` if wake-up is unreliable or slow.

Known unknowns:
- The exact JSON a Grok Bot routine webhook expects. We send `message` (a full
  instruction), `text` and `source`. Adjust `send_to_grok` if the routine needs another
  shape.
- Whether a ~1 s Whisper delay plus routine start-up feels fast enough. A
  `speech_model` of `base` is faster, and `small` is more accurate.

## Ideas for next versions

- Multi-display screenshots; accessibility-tree reading of the frontmost app.
- Menu bar app instead of a Terminal window; launch at login.
