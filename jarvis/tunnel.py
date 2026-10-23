"""Gives the Jarvis server on your Mac a public https address that Grok can reach.

Grok runs in xAI's cloud, so it can't see "localhost". A tunnel (Cloudflare or
ngrok) forwards a public address to the server running on your Mac.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
from collections.abc import Callable

from .config import HOME

_CLOUDFLARE_URL = re.compile(r"https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com")


def find_binary(name: str) -> str | None:
    local = HOME / "bin" / name
    if local.exists():
        return str(local)
    return shutil.which(name)


class Tunnel:
    def __init__(self, provider: str, port: int, ngrok_domain: str = ""):
        self.provider = provider
        self.port = port
        self.ngrok_domain = ngrok_domain
        self.proc: subprocess.Popen | None = None

    def start(self, on_url: Callable[[str], None]) -> None:
        if self.provider == "cloudflare":
            self._start_cloudflare(on_url)
        elif self.provider == "ngrok":
            self._start_ngrok(on_url)
        else:
            raise ValueError(f"Unknown tunnel provider: {self.provider}")

    def _start_cloudflare(self, on_url: Callable[[str], None]) -> None:
        binary = find_binary("cloudflared")
        if not binary:
            raise RuntimeError("cloudflared is not installed. Run ./install.sh again.")
        self.proc = subprocess.Popen(
            [binary, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )

        def watch() -> None:
            found = False
            log = (HOME / "tunnel.log").open("w")
            for line in self.proc.stdout:
                log.write(line)
                log.flush()
                match = _CLOUDFLARE_URL.search(line)
                if match and not found:
                    found = True
                    on_url(match.group(0))

        threading.Thread(target=watch, daemon=True).start()

    def _start_ngrok(self, on_url: Callable[[str], None]) -> None:
        binary = find_binary("ngrok")
        if not binary:
            raise RuntimeError("ngrok is not installed. See the README section 'A web address that never changes'.")
        if not self.ngrok_domain:
            raise RuntimeError("Set your ngrok domain first: jarvis tunnel ngrok <your-domain>")
        log = (HOME / "tunnel.log").open("w")
        self.proc = subprocess.Popen(
            [binary, "http", f"--url={self.ngrok_domain}", str(self.port), "--log=stdout"],
            stdout=log, stderr=subprocess.STDOUT,
        )
        on_url(f"https://{self.ngrok_domain}")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def connector_url(public_base: str, key: str) -> str:
    return f"{public_base.rstrip('/')}/{key}/mcp"

