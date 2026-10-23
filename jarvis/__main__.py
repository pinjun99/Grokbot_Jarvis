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


def cmd_start(args) -> int:
    import uvicorn

    from .mac import MacController
    from .permissions import Gatekeeper
    from .server import build_app, build_server
    from .tunnel import Tunnel, connector_url

    cfg = config.load()
    mac = MacController(max_width=cfg["screenshot_width"], voice=cfg["voice"])
    gate = Gatekeeper(get_mode=config.current_mode, is_paused=config.is_paused,
                      confirm=mac.confirm, log=config.log)
    app = build_app(build_server(mac, gate), cfg["key"])

    print(BANNER)
    print(f"Permission mode: {gate.mode()} - {MODES[gate.mode()]}")
    print("Change it any time in another Terminal window: jarvis mode\n")

    tunnel = None
    if cfg["tunnel"] != "none" and not args.local:
        def show(public: str) -> None:
            url = connector_url(public, cfg["key"])
            config.URL_FILE.write_text(url + "\n")
            print("=" * 72)
            print("  Jarvis is online. Your Grok connector URL is:\n")
            print(f"  {url}\n")
            print("  Paste it in Grok: grok.com/connectors > New Connector > Custom.")
            print("  Keep it secret - anyone with this link can control your Mac.")
            if cfg["tunnel"] == "cloudflare":
                print("  (This link changes every time Jarvis restarts. See the README for a")
                print("   permanent one.)")
            print("=" * 72)
            print("Press Ctrl+C here to stop Jarvis.\n")

        tunnel = Tunnel(cfg["tunnel"], cfg["port"], cfg["ngrok_domain"])
        print("Opening a secure tunnel so Grok can reach this Mac...")
        tunnel.start(show)
    else:
        print(f"Local only: http://127.0.0.1:{cfg['port']}/{cfg['key']}/mcp\n")

    config.log("Jarvis started")
    try:
        uvicorn.run(app, host="127.0.0.1", port=cfg["port"], log_level="warning")
    finally:
        if tunnel:
            tunnel.stop()
        config.log("Jarvis stopped")
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
