# Notes for Claude working on this repo

The owner is non-technical. Explain things plainly, give exact click-by-click steps, and
do the technical work for them.

## What this is

Jarvis is an MCP server (Streamable HTTP, `mcp` Python SDK 2.x, `MCPServer`) that runs on
the owner's Mac (M4, macOS 26) and lets **Grok** (grok.com custom connectors / Grok Bot)
see and control the Mac. Grok is the only AI in the loop. Do not add another LLM unless
the owner asks.

Flow: Grok (xAI cloud) → Cloudflare quick tunnel (or ngrok) → `127.0.0.1:8765` →
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
4. `jarvis start`, add the printed URL at grok.com/connectors → New Connector → Custom,
   and test from Grok.

Known unknowns to check against xAI's current docs (docs.x.ai/grok/connectors):
- Whether grok.com custom connectors accept a URL with no auth (secret in the path), or
  insist on OAuth. If OAuth is required, add an OAuth provider (the SDK has
  `mcp.server.auth`) and keep the secret-path option.
- Whether Grok forwards MCP image results to the model. If screenshots don't reach it,
  lean on `read_screen_text` (OCR with click points).
- Whether Grok voice mode can use connectors.

## Ideas for next versions

- Local "Hey Jarvis" wake word that hands speech to the Grok app.
- Multi-display screenshots; accessibility-tree reading of the frontmost app.
- Menu bar app instead of a Terminal window; launch at login.
