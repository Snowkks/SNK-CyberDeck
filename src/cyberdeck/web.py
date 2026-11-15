"""A plain HTML report of the diagnostics, reachable from your tailnet.

Reuses diagnostics.run_all_checks(), so the TUI and this page can never
disagree. No framework, no new dependency: http.server + string templates.

Security note: the page has no login. When bound to the tailscale0 address
only tailnet devices can reach it, and Tailscale ACLs decide which ones.
"""

from __future__ import annotations

import html
import ipaddress
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cyberdeck.diagnostics import CheckResult, run_all_checks

DEFAULT_PORT = 8080
MAX_RAW_CHARS = 4000
TAILSCALE_TIMEOUT = 2

# One report costs ~6 subprocesses and a DNS lookup on a Pi. Browsers
# refresh every 30 s, so a 10 s cache cuts the work by ~3x.
CACHE_SECONDS = 10
# Do not let a slow or idle client hold a handler thread forever.
REQUEST_TIMEOUT = 5


# Tailscale hands out addresses from the shared 100.64.0.0/10 range.
TAILNET_NETWORK = ipaddress.IPv4Network("100.64.0.0/10")


def tailnet_address(timeout: int = TAILSCALE_TIMEOUT) -> str | None:
    """The Pi's tailnet IPv4 address, or None if Tailscale is not usable.

    The output is treated as untrusted: only a plain address inside
    100.64.0.0/10 is accepted, so a stopped or misbehaving tailscale can
    never make us bind 0.0.0.0 or some other interface.
    """
    try:
        proc = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None

    for line in (proc.stdout or "").splitlines():
        try:
            address = ipaddress.IPv4Address(line.strip())
        except ValueError:
            continue
        if address in TAILNET_NETWORK:
            return str(address)
    return None


def _raw_block(result: CheckResult) -> str:
    if not result.command and not result.raw:
        return ""
    raw = result.raw
    if len(raw) > MAX_RAW_CHARS:
        raw = raw[:MAX_RAW_CHARS] + "\n... (truncated)"
    return (
        "<details><summary>command and raw output</summary>"
        f"<pre><code>$ {html.escape(result.command)}</code>\n"
        f"<code>{html.escape(raw) or '(no output)'}</code></pre></details>"
    )


def _section(result: CheckResult) -> str:
    css = "ok" if result.ok else "fail"
    badge = "OK" if result.ok else "FAIL"
    return (
        f'<section class="{css}">'
        f"<h2>{html.escape(result.name)} <span class=badge>{badge}</span></h2>"
        f"<p class=summary>{html.escape(result.summary)}</p>"
        f"<p class=detail>{html.escape(result.detail)}</p>"
        f"{_raw_block(result)}"
        "</section>"
    )


def build_report(results: list[CheckResult], bind_note: str = "") -> str:
    ok_count = sum(1 for r in results if r.ok)
    failed = len(results) - ok_count
    verdict = "All checks passed" if failed == 0 else f"{failed} check(s) failed"
    sections = "\n".join(_section(result) for result in results)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    note = f"<p class=note>{html.escape(bind_note)}</p>" if bind_note else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>CyberDeck - Network Diagnostics</title>
<style>
  body {{ background:#101316; color:#e6e6e6; font:15px/1.5 system-ui,sans-serif;
         margin:0 auto; padding:20px; max-width:900px; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .meta {{ color:#8b949e; font-size:13px; margin:0 0 18px; }}
  .note {{ color:#d29922; }}
  section {{ border-left:4px solid #2ea043; background:#161b22; margin:0 0 12px;
             padding:10px 14px; border-radius:4px; }}
  section.fail {{ border-left-color:#f85149; }}
  h2 {{ font-size:15px; margin:0 0 6px; font-weight:600; }}
  .badge {{ font-size:11px; letter-spacing:.5px; padding:1px 6px; border-radius:3px;
            background:#2ea043; color:#08130b; vertical-align:middle; }}
  section.fail .badge {{ background:#f85149; color:#1a0605; }}
  p {{ margin:2px 0; }}
  .summary {{ color:#e6e6e6; font-weight:600; }}
  .detail {{ color:#8b949e; font-size:13px; }}
  details {{ margin-top:8px; }}
  summary {{ cursor:pointer; color:#58a6ff; font-size:13px; }}
  pre {{ background:#0d1117; border:1px solid #30363d; border-radius:4px; padding:8px;
         overflow:auto; font-size:12px; color:#c9d1d9; max-height:320px; }}
  code {{ font-family:ui-monospace,monospace; }}
</style>
</head>
<body>
<h1>Snowk CyberDeck - Network Diagnostics</h1>
<p class=meta>Generated {generated} - <strong>{verdict}</strong> - page refreshes every 30 s</p>
{note}
{sections}
</body>
</html>
"""


class ReportHandler(BaseHTTPRequestHandler):
    server_version = "CyberDeck"
    sys_version = ""  # do not advertise the Python version
    timeout = REQUEST_TIMEOUT

    def log_message(self, *args):  # keep the TUI clean
        pass

    def collect(self) -> list[CheckResult]:
        return run_all_checks()

    def _cached_report(self) -> str:
        server = self.server
        # The lock makes this single-flight: concurrent requests wait for the
        # one collector instead of each spawning their own subprocesses.
        with server.cache_lock:
            now = time.monotonic()
            if server.cache_body is not None and now - server.cache_time < CACHE_SECONDS:
                return server.cache_body
            body = build_report(self.collect(), getattr(server, "bind_note", ""))
            server.cache_body = body
            server.cache_time = now
            return body

    def _send(self, status: int, body: str) -> None:
        data = body.encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionError):
            # The client went away (or the server was stopped) - nothing to do.
            pass

    def do_GET(self) -> None:
        if self.path not in ("/", "/index.html"):
            self.send_error(404, "Not found")
            return
        try:
            self._send(200, self._cached_report())
        except Exception as exc:  # noqa: BLE001 - show the error, do not kill the server
            self._send(
                500,
                "<!doctype html><html><body><h1>CyberDeck error</h1>"
                f"<pre>{html.escape(str(exc))}</pre></body></html>",
            )


class ReportServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    bind_note = ""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cache_lock = threading.Lock()
        self.cache_body: str | None = None
        self.cache_time = 0.0


def create_server(
    address: str,
    port: int = DEFAULT_PORT,
    bind_note: str = "",
    handler: type[ReportHandler] = ReportHandler,
) -> ReportServer:
    server = ReportServer((address, port), handler)
    server.bind_note = bind_note
    return server
