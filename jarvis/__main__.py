"""The `jarvis` command you type in Terminal."""

from __future__ import annotations

import argparse
import sys

from . import config
from .permissions import MODES

BANNER = """
     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗
     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝
     ██║███████║██████╔╝██║   ██║██║███████╗
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝
        Grok's hands and eyes on your Mac
"""


def _mac():
    from .mac import MacController

    cfg = config.load()
    return MacController(max_width=cfg["screenshot_width"], voice=cfg["voice"])


def cmd_doctor(_args) -> int:
    mac = _mac()
    ok = True
    print("Checking Jarvis can control this Mac...\n")
    try:
        perms = mac.permissions()
    except Exception as e:  # missing pyobjc etc.
        print(f"  ✗ Could not check permissions: {e}")
        return 1
    labels = {"accessibility": "Accessibility (lets Jarvis click and type)",
              "screen_recording": "Screen Recording (lets Jarvis see the screen)"}
    for name, granted in perms.items():
        print(f"  {'✓' if granted else '✗'} {labels[name]}")
        ok &= granted
    if not ok:
        print("\nAsking macOS for the missing permissions now...")
        mac.request_permissions()
        print(
            "\nOpen System Settings > Privacy & Security, turn ON 'Terminal' under both\n"
            "'Accessibility' and 'Screen & System Audio Recording', then QUIT Terminal\n"
            "completely (cmd+Q) and open it again. Then run: jarvis doctor"
        )
        return 1
    try:
        shot = mac.screenshot()
        print(f"  ✓ Screenshot works ({shot.width}x{shot.height})")
        print(f"  ✓ Can read the screen ({len(mac.read_screen_text())} pieces of text found)")
    except Exception as e:
        print(f"  ✗ {e}")
        return 1
    print("\nAll good. Start Jarvis with: jarvis start")
    return 0


def connector_message(public: str, key: str) -> str:
    return (
        "Please add (or update) a custom MCP connector called \"Jarvis\":\n"
        f"  URL:    {public.rstrip('/')}/mcp\n"
        f"  Header: Authorization: Bearer {key}\n"
        "It lets you see and control my Mac. Once added, call its get_status tool to check it works."
    )


def _port_free(port: int) -> bool:
    import socket

    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def cmd_start(args) -> int:
    import uvicorn

    from .mac import MacController
    from .permissions import Gatekeeper
    from .server import ShotStore, build_app, build_server
    from .tunnel import Tunnel

    cfg = config.load()
    if not _port_free(cfg["port"]):
        print("Jarvis is already running in another window (or something else is using port "
              f"{cfg['port']}). Use that window, or close it first.")
        return 1
    mac = MacController(max_width=cfg["screenshot_width"], voice=cfg["voice"])
    gate = Gatekeeper(get_mode=config.current_mode, is_paused=config.is_paused,
                      confirm=mac.confirm, log=config.log)
    shots = ShotStore()
    shots.base_url = f"http://127.0.0.1:{cfg['port']}"
    app = build_app(build_server(mac, gate, shots), cfg["key"], shots)

    print(BANNER)
    print(f"Permission mode: {gate.mode()} - {MODES[gate.mode()]}")
    print("Change it any time in another Terminal window: jarvis mode\n")

    tunnel = None
    if cfg["tunnel"] != "none" and not args.local:
        def show(public: str) -> None:
            shots.base_url = public
            message = connector_message(public, cfg["key"])
            config.URL_FILE.write_text(message + "\n")
            print("=" * 72)
            print("  Jarvis is online. Send this message to your Grok Bot to connect it:\n")
            print(message)
            print("\n  Keep the key secret - anyone with it can control your Mac.")
            if cfg["tunnel"] == "cloudflare":
                print("  (The link changes every time Jarvis restarts. See the README for a")
                print("   permanent one.)  Show this message again any time: jarvis url")
            print("=" * 72)
            print("Press Ctrl+C here to stop Jarvis.\n")

        def failed(reason: str) -> None:
            print("\n" + "!" * 72)
            print("  The secure tunnel couldn't open, so Grok Bot can't reach this Mac yet.")
            print(f"  Reason: {reason}")
            print("  Check your internet connection, then press Ctrl+C and start Jarvis again.")
            print(f"  Details: {config.HOME / 'tunnel.log'}")
            print("!" * 72 + "\n")
            config.log(f"Tunnel failed: {reason}")

        tunnel = Tunnel(cfg["tunnel"], cfg["port"], cfg["ngrok_domain"])
        print("Opening a secure tunnel so Grok can reach this Mac...")
        tunnel.start(show, failed)
    else:
        print(f"Local only: http://127.0.0.1:{cfg['port']}/{cfg['key']}/mcp\n")

    if cfg["voice_enabled"]:
        _start_voice_thread(cfg)

    config.log("Jarvis started")
    try:
        uvicorn.run(app, host="127.0.0.1", port=cfg["port"], log_level="warning")
    finally:
        if tunnel:
            tunnel.stop()
        config.log("Jarvis stopped")
    return 0


def _start_voice_thread(cfg: dict) -> None:
    import threading

    from .voice import Listener

    def run() -> None:
        try:
            listener = Listener(cfg, config.log, config.is_paused)
            listener.run(on_ready=lambda: print(f"🎙  Listening for: {', '.join(listener.phrases)}\n"))
        except Exception as e:
            print(f"Voice stopped: {e}")
            config.log(f"Voice stopped: {e}")

    threading.Thread(target=run, daemon=True).start()


def cmd_voice(args) -> int:
    from .voice import wake_phrases

    cfg = config.load()
    if args.action == "setup":
        from .grokbot import GrokBotApp

        print("Voice setup. When you say \"Hey\" and your bot's name, a live voice chat opens")
        print("in Grok Bot, and your bot answers in its own voice.\n")
        found = GrokBotApp().main_bot()
        if found:
            bot = input(f"Which bot should wake up? Press Enter for {found}, or type another name: ").strip() or found
        else:
            bot = input("Which bot should wake up? Type its name as shown in Grok Bot (e.g. Elon): ").strip()
        cfg = config.update(wake_action="voice_chat", grok_bot_name=bot, wake_phrases=[], voice_enabled=True)
        print(f"\nSaved. Say \"{wake_phrases(cfg)[0].capitalize()}\" to start a voice chat.")
        print("Test it without the microphone:  jarvis voice test")
        return 0
    if args.action == "webhook":
        print("Webhook setup. First, ask your Grok Bot to create the routine (see README > Voice),")
        print("then paste the webhook details it gives you.\n")
        url = input("Webhook URL: ").strip()
        if not url.startswith("https://"):
            print("That doesn't look like a webhook URL (it should start with https://).")
            return 1
        headers = {}
        header = input("Secret header, if Grok Bot gave you one (e.g. 'Authorization: Bearer abc'), "
                       "else press Enter: ").strip()
        if header:
            name, _, value = header.partition(":")
            headers[name.strip()] = value.strip()
        config.update(wake_action="webhook", voice_webhook_url=url, voice_webhook_headers=headers,
                      voice_enabled=True)
        print("\nSaved. Test it without the microphone:  jarvis voice test \"open Safari\"")
        return 0
    if args.action == "test" and cfg["wake_action"] == "voice_chat":
        from .grokbot import GrokBotApp

        try:
            started = GrokBotApp().start_voice_chat(cfg["grok_bot_name"])
        except Exception as e:
            print(f"Couldn't open the voice chat: {e}")
            return 1
        print("Opened a Grok Bot voice chat. Say hello!" if started else "A Grok Bot voice chat is already open.")
        return 0
    if args.action == "test":
        from .voice import send_to_grok

        if not cfg["voice_webhook_url"]:
            print("Run 'jarvis voice webhook' first.")
            return 1
        text = " ".join(args.text) or "open Safari"
        status = send_to_grok(cfg["voice_webhook_url"], text, cfg["voice_webhook_headers"])
        print(f"Sent \"{text}\" to Grok Bot (answer: {status}). Check that your Grok Bot reacted.")
        return 0
    if args.action in ("on", "off"):
        config.update(voice_enabled=args.action == "on")
        print(f"Voice wake-up is {args.action}. Restart Jarvis for it to take effect.")
        return 0
    if args.action == "listen":
        from .voice import Listener

        listener = Listener(cfg, config.log, config.is_paused)
        listener.run(on_ready=lambda: print(f"🎙  Listening for: {', '.join(listener.phrases)}  (Ctrl+C to stop)"))
        return 0
    print(f"Voice wake-up: {'on' if cfg['voice_enabled'] else 'off'}")
    print(f"Wake phrases: {', '.join(wake_phrases(cfg))}")
    if cfg["wake_action"] == "voice_chat":
        print(f"Wake phrase opens: a Grok Bot voice chat with {cfg['grok_bot_name'] or 'the chat that is open'}")
    else:
        print(f"Wake phrase sends to: Grok Bot webhook "
              f"({'set' if cfg['voice_webhook_url'] else 'not set, run: jarvis voice webhook'})")
    return 0


def cmd_wake(args) -> int:
    from .voice import wake_phrases

    if not args.phrases:
        print("Wake phrases: " + ", ".join(wake_phrases(config.load())))
        print('Change with: jarvis wake "hey elon" "yo friday"   (or: jarvis wake default)')
        return 0
    phrases = [p.strip().lower() for p in args.phrases if p.strip()]
    if phrases == ["default"]:
        phrases = []  # back to "hey <bot name>"
    phrases = wake_phrases(config.update(wake_phrases=phrases))
    print(f"Jarvis now wakes up to: {', '.join(phrases)}. Restart Jarvis for it to take effect.")
    return 0


def cmd_mode(args) -> int:
    if not args.name:
        current = config.current_mode()
        print("Permission modes (current one marked with ►):\n")
        for name, meaning in MODES.items():
            print(f"  {'►' if name == current else ' '} {name:10} {meaning}")
        print("\nChange with: jarvis mode <name>   e.g. jarvis mode ask")
        return 0
    if args.name not in MODES:
        print(f"Unknown mode {args.name!r}. Choose one of: {', '.join(MODES)}")
        return 1
    config.update(mode=args.name)
    config.log(f"Mode changed to {args.name}")
    print(f"Mode is now '{args.name}': {MODES[args.name]}")
    return 0


def cmd_pause(_args) -> int:
    config.HOME.mkdir(parents=True, exist_ok=True)
    config.PAUSE_FILE.touch()
    config.log("Paused by user")
    print("Jarvis is paused. Grok can't do anything until you run: jarvis resume")
    return 0


def cmd_resume(_args) -> int:
    config.PAUSE_FILE.unlink(missing_ok=True)
    config.log("Resumed by user")
    print("Jarvis is back on.")
    return 0


def cmd_url(_args) -> int:
    if config.URL_FILE.exists():
        print(config.URL_FILE.read_text().strip())
        return 0
    print("Jarvis hasn't been started yet. Run: jarvis start")
    return 1


def cmd_new_key(_args) -> int:
    config.update(key=config.new_key())
    config.URL_FILE.unlink(missing_ok=True)
    print("Made a new secret key. The old connector link no longer works.")
    print("Restart Jarvis (jarvis start) and paste the new link into Grok.")
    return 0


def cmd_log(args) -> int:
    if not config.LOG_FILE.exists():
        print("Nothing has happened yet.")
        return 0
    lines = config.LOG_FILE.read_text().splitlines()
    print("\n".join(lines[-args.lines:]))
    return 0


def cmd_tunnel(args) -> int:
    if args.provider == "ngrok":
        if not args.domain:
            print("Usage: jarvis tunnel ngrok <your-domain.ngrok-free.app>")
            return 1
        config.update(tunnel="ngrok", ngrok_domain=args.domain)
    else:
        config.update(tunnel=args.provider)
    print(f"Tunnel set to {args.provider}. Restart Jarvis for it to take effect.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis", description="Let Grok control your Mac.")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("start", help="start Jarvis and print the link for Grok")
    p.add_argument("--local", action="store_true", help="no tunnel (for testing on this Mac)")
    p.set_defaults(func=cmd_start)
    sub.add_parser("doctor", help="check macOS permissions").set_defaults(func=cmd_doctor)
    p = sub.add_parser("mode", help="show or change the permission mode")
    p.add_argument("name", nargs="?")
    p.set_defaults(func=cmd_mode)
    sub.add_parser("pause", help="emergency stop: block all actions").set_defaults(func=cmd_pause)
    sub.add_parser("resume", help="undo pause").set_defaults(func=cmd_resume)
    sub.add_parser("url", help="show the current Grok connector link").set_defaults(func=cmd_url)
    sub.add_parser("new-key", help="make a new secret link (old one stops working)").set_defaults(func=cmd_new_key)
    p = sub.add_parser("log", help="show what Grok has done recently")
    p.add_argument("-n", "--lines", type=int, default=40)
    p.set_defaults(func=cmd_log)
    p = sub.add_parser("voice", help="voice wake-up: setup, test, on, off, listen, webhook")
    p.add_argument("action", nargs="?", choices=["setup", "test", "on", "off", "listen", "webhook"])
    p.add_argument("text", nargs="*")
    p.set_defaults(func=cmd_voice)
    p = sub.add_parser("wake", help="show or change the wake phrase(s)")
    p.add_argument("phrases", nargs="*")
    p.set_defaults(func=cmd_wake)
    p = sub.add_parser("tunnel", help="choose how Grok reaches this Mac")
    p.add_argument("provider", choices=["cloudflare", "ngrok", "none"])
    p.add_argument("domain", nargs="?")
    p.set_defaults(func=cmd_tunnel)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
