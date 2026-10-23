# Jarvis: let Grok control your Mac

Like J.A.R.V.I.S. from Iron Man: you tell Grok what you want ("open Safari and find
flights to Tokyo", "open Claude and start a new chat"), and Grok does it on your Mac by
looking at the screen and using the mouse, keyboard and apps.

**Grok stays the brain.** Jarvis is not another AI. It is a small bridge that runs on
your Mac and gives Grok eyes (screenshots, reading the screen) and hands (mouse,
keyboard, opening apps). Every action passes through permission layers that you
control.

```
 You  ──talk/type──►  Grok (grok.com, Grok app, Grok Bot)
                         │   decides what to do
                         ▼
              secure tunnel (Cloudflare)
                         │
                         ▼
          Jarvis on your Mac ── checks your permission mode ──► pop-up if needed
                         │
                         ▼
          screenshots · mouse · keyboard · apps · websites · voice
```

Technically, Jarvis is an **MCP server**, which Grok calls a "custom connector". Grok
supports these officially at grok.com/connectors.

---

## Setup (about 10 minutes, once)

You need: a Mac (M-series), a paid Grok plan that supports custom connectors, and
this folder on your Mac.

1. **Open Terminal** (press `cmd + space`, type `Terminal`, press Enter).
2. **Go to this folder.** Type `cd ` (with a space after it), drag this folder from
   Finder into the Terminal window, then press Enter.
3. **Run the installer.** Type the line below and press Enter:
   ```
   ./install.sh
   ```
4. **Give permissions.** macOS will ask you to allow Terminal to use:
   - **Accessibility** (so Jarvis can click and type)
   - **Screen & System Audio Recording** (so Jarvis can see the screen)

   Open **System Settings → Privacy & Security**, find each one, and switch
   **Terminal** ON. Then quit Terminal completely (`cmd + Q`), open it again and type
   `jarvis doctor`. Everything should show ✓.

## Every day: start Jarvis

Double-click **`Start Jarvis.command`** in this folder (or type `jarvis start` in
Terminal). After a few seconds it prints something like:

```
  Jarvis is online. Your Grok connector URL is:

  https://some-words-here.trycloudflare.com/Abc123SecretKey/mcp
```

Leave that window open. Jarvis runs as long as it stays open.

## Connect Grok (first time, and whenever the link changes)

1. Go to **grok.com/connectors**, then **New Connector → Custom**.
2. Name it `Jarvis` and paste the URL from above. If it asks about
   authentication, choose **None**. The secret key is already inside the URL.
3. In a Grok chat (or Grok Bot), make sure the Jarvis connector is turned on and
   try: *"Use Jarvis to take a screenshot and tell me what's on my screen."*
4. Then: *"Open Safari and go to youtube.com."*

> ⚠️ **The URL is like a key to your Mac.** Don't share it or post screenshots of
> it. If it ever leaks, run `jarvis new-key`, restart Jarvis, and update Grok.

The free Cloudflare link changes each time Jarvis restarts, so you'd have to paste a
new one into Grok each time. To get a link that never changes, see
[A link that never changes](#a-link-that-never-changes) below.

---

## Permission layers

You choose how much freedom Grok has. Grok **cannot** change this itself.

| Mode | What Grok can do |
|---|---|
| `watch` | Only look at your screen. No clicking, typing or opening anything. |
| `ask` | Look freely, but asks you (with a pop-up) before every single action. |
| `assist` **(default)** | Click, type, open apps and websites on its own. Asks before sensitive things: Terminal commands, AppleScript, quitting apps, reading your clipboard. |
| `autopilot` | Does almost everything on its own. Still asks before serious things. |

**Always asks, in every mode:** deleting files, sending or posting, buying or paying,
admin commands (`sudo`), emptying the Trash, and anything Grok itself flags as
important.

When Jarvis needs your OK, a pop-up appears on your Mac with **Allow once**,
**Allow for 10 min** or **Deny**. If you don't answer within 45 seconds, the answer
is Deny.

Change the mode any time (even while Jarvis is running) in a new Terminal window:

```
jarvis mode            # show modes
jarvis mode ask        # switch to "ask"
```

### Emergency stop

- `jarvis pause` blocks all actions immediately. `jarvis resume` turns them back on.
- Or press `Ctrl + C` in the Jarvis window, or just close it.
- `jarvis log` shows everything Grok did or tried to do.

---

## Voice

- **You → Grok:** use voice mode in the Grok app (Mac or iPhone) with the Jarvis
  connector on. Whether voice mode can use connectors is up to xAI. If yours can't,
  type instead.
- **Jarvis → you:** Grok can make your Mac speak out loud with a British voice
  (`speak`). To change the voice, edit `"voice"` in `~/.jarvis/config.json`. Run
  `say -v '?'` in Terminal to list the voices.

A "Hey Jarvis" wake word on the Mac is a possible next step.

## What Grok can do through Jarvis

`screenshot`, `read_screen_text` (finds text and where to click it), `list_apps`,
`open_app`, `open_url`, `click`, `move_mouse`, `drag`, `scroll`, `type_text`,
`press_keys` (shortcuts like cmd+t), `wait`, `speak`, `notify`, `set_clipboard`,
`get_clipboard`, `quit_app`, `run_applescript`, `run_shell`, `ask_user_permission`,
`get_status`.

## A link that never changes

1. Make a free account at [ngrok.com](https://ngrok.com) and claim your free static
   domain (Dashboard → Domains), for example `my-jarvis.ngrok-free.app`.
2. In Terminal, install ngrok and log in. ngrok's dashboard shows the exact commands
   (`brew install ngrok` then `ngrok config add-authtoken ...`).
3. Tell Jarvis to use it:
   ```
   jarvis tunnel ngrok my-jarvis.ngrok-free.app
   ```
4. Restart Jarvis, and paste the new URL into Grok one last time.

## Troubleshooting

| Problem | Fix |
|---|---|
| Screenshots are black or only show the wallpaper | Screen Recording isn't on for Terminal. Turn it on, quit Terminal, reopen it. |
| Clicks or typing do nothing | Accessibility isn't on for Terminal. Same fix. |
| Grok says it can't connect | Is the Jarvis window still open? Did the link change? Run `jarvis url` to see the current one. |
| Grok won't accept the URL without "OAuth" | Tell Claude. An OAuth sign-in can be added. |
| `jarvis: command not found` | Close and reopen Terminal, or run `./install.sh` again. |

## Status

Version 0.1, built and tested on Linux with a simulated Mac. The connector, the
permission layers and the security checks pass their tests. The parts that touch the
real Mac (`jarvis/mac.py`) still need their first real run on macOS. See `CLAUDE.md`.

## Files

- `jarvis/mac.py`: the hands and eyes (screenshots, mouse, keyboard, apps)
- `jarvis/permissions.py`: the permission layers
- `jarvis/server.py`: the connector Grok talks to
- `jarvis/tunnel.py`: the secure public link
- `jarvis/__main__.py`: the `jarvis` Terminal command
- Your settings, secret key and activity log live in `~/.jarvis/`, outside this
  folder, so they never get uploaded to GitHub.
