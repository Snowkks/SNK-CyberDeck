"""A plain HTML report of the diagnostics, reachable from your tailnet.

Reuses diagnostics.run_all_checks(), so the TUI and this page can never
disagree. No framework, no new dependency: http.server + string templates.

Security note: the page has no login. When bound to the tailscale0 address
only tailnet devices can reach it, and Tailscale ACLs decide which ones.
"""

from __future__ import annotations

import html
import ipaddress
import platform
import socket
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cyberdeck.diagnostics import CheckResult, run_all_checks
from cyberdeck.llm import LLMError, is_available, stream_explanation

DEFAULT_PORT = 8080
TAILSCALE_TIMEOUT = 2

# One report costs ~6 subprocesses and a DNS lookup on a Pi. Browsers
# refresh every 30 s, so a 10 s cache cuts the work by ~3x.
CACHE_SECONDS = 10
# AI generation is expensive on a Pi: keep the answer for a few minutes.
AI_CACHE_SECONDS = 300
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


def _uptime_text() -> str:
    try:
        with open("/proc/uptime", encoding="utf-8") as handle:
            seconds = int(float(handle.read().split()[0]))
    except (OSError, ValueError, IndexError):
        return "unknown"
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    return f"{days}d {hours}h {minutes}m" if days else f"{hours}h {minutes}m"


def _primary_ip() -> str:
    # UDP connect sends nothing but lets the kernel pick the outgoing address.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            return probe.getsockname()[0]
    except OSError:
        return "unknown"


def system_info() -> dict[str, str]:
    """Best-effort, stdlib-only facts about this machine."""
    return {
        "hostname": platform.node() or "unknown",
        "kernel": platform.release(),
        "machine": platform.machine(),
        "uptime": _uptime_text(),
        "primary_ip": _primary_ip(),
    }


def _raw_block(result: CheckResult) -> str:
    if not result.command and not result.raw:
        return ""
    return (
        "<details>"
        f"<summary>{html.escape(result.name)}"
        f" <span class=cmd>$ {html.escape(result.command)}</span></summary>"
        f"<pre><code>{html.escape(result.raw) or '(no output)'}</code></pre>"
        "</details>"
    )


def _row(result: CheckResult) -> str:
    css = "ok" if result.ok else "fail"
    badge = "OK" if result.ok else "FAIL"
    return (
        "<tr>"
        f"<td>{html.escape(result.name)}</td>"
        f'<td><span class="pill {css}">{badge}</span></td>'
        f"<td>{html.escape(result.summary)}</td>"
        f'<td class=detail>{html.escape(result.detail)}</td>'
        "</tr>"
    )


def _system_block(info: dict[str, str]) -> str:
    items = "".join(
        f"<div><dt>{html.escape(key)}</dt><dd>{html.escape(value)}</dd></div>"
        for key, value in info.items()
    )
    return f'<dl class=sysinfo>{items}</dl>'


def _ai_card(ai_text: str | None, ai_error: str | None) -> str:
    if ai_text:
        return (
            '<section class=card>'
            "<h2>AI explanation</h2>"
            f"<pre class=ai>{html.escape(ai_text)}</pre>"
            '<p class=meta><a class=btn href="/?ai=1">Regenerate (slow on a Pi)</a></p>'
            "</section>"
        )
    if ai_error:
        return (
            '<section class="card ai-off">'
            "<h2>AI explanation</h2>"
            f"<p>{html.escape(ai_error)}</p>"
            '<p class=meta><a class=btn href="/?ai=1">Try again</a></p>'
            "</section>"
        )
    return (
        '<section class="card ai-off">'
        "<h2>AI explanation</h2>"
        "<p>Ask the local model to read these results and suggest what to check next. "
        "It needs Ollama running and can take a while on a Pi.</p>"
        '<p class=meta><a class=btn href="/?ai=1">Explain with AI</a></p>'
        "</section>"
    )


def build_report(
    results: list[CheckResult],
    bind_note: str = "",
    info: dict[str, str] | None = None,
    ai_text: str | None = None,
    ai_error: str | None = None,
) -> str:
    ok_count = sum(1 for r in results if r.ok)
    failed = len(results) - ok_count
    css = "ok" if failed == 0 else "fail"
    verdict = "All checks passed" if failed == 0 else f"{failed} check(s) failed"
    rows = "\n".join(_row(result) for result in results)
    raw_blocks = "\n".join(block for block in (_raw_block(r) for r in results) if block)
    if not raw_blocks:
        raw_blocks = "<p class=empty>No raw output available.</p>"
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    note = f"<p class=note>{html.escape(bind_note)}</p>" if bind_note else ""
    system = _system_block(info or system_info())
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>CyberDeck - Network Diagnostics</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ background:#0d1117; color:#e6edf3; margin:0;
         font:15px/1.5 system-ui,-apple-system,sans-serif; }}
  .wrap {{ max-width:1000px; margin:0 auto; padding:20px 16px 40px; }}
  .banner {{ border-radius:10px; padding:18px 20px; margin-bottom:16px;
             border:1px solid #30363d; background:#161b22; }}
  .banner.ok {{ border-color:#238636; background:linear-gradient(180deg,#12261a,#0f1a14); }}
  .banner.fail {{ border-color:#da3633; background:linear-gradient(180deg,#2a1416,#1a0f10); }}
  .verdict {{ font-size:24px; font-weight:700; letter-spacing:.3px;
              text-transform:uppercase; }}
  .banner.ok .verdict {{ color:#3fb950; }}
  .banner.fail .verdict {{ color:#f85149; }}
  .counts {{ font-size:14px; color:#8b949e; margin-top:2px; }}
  .meta {{ font-size:12px; color:#6e7681; margin-top:10px; }}
  .note {{ border-left:3px solid #d29922; padding:8px 12px; color:#d29922;
           background:#161b22; border-radius:4px; }}
  h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.6px;
        color:#8b949e; margin:0; padding:12px 14px 0; }}
  .card {{ background:#161b22; border:1px solid #30363d; border-radius:10px;
           overflow:hidden; margin-top:10px; }}
  .sysinfo {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
              gap:1px; background:#30363d; border:1px solid #30363d;
              border-radius:10px; overflow:hidden; margin:0 0 16px; }}
  .sysinfo > div {{ background:#161b22; padding:10px 14px; }}
  .sysinfo dt {{ font-size:11px; text-transform:uppercase; letter-spacing:.6px;
                 color:#8b949e; }}
  .sysinfo dd {{ margin:2px 0 0; font-size:14px; font-family:ui-monospace,monospace; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th {{ text-align:left; font-size:11px; text-transform:uppercase;
        letter-spacing:.6px; color:#8b949e; padding:10px 12px;
        border-bottom:1px solid #30363d; background:#12171d; }}
  td {{ padding:10px 12px; border-bottom:1px solid #21262d; vertical-align:top; }}
  tr:last-child td {{ border-bottom:none; }}
  tr:hover td {{ background:#1a2029; }}
  td.detail {{ color:#8b949e; font-size:13px; }}
  .pill {{ display:inline-block; min-width:44px; text-align:center;
           padding:1px 8px; border-radius:999px; font-size:11px;
           font-weight:700; letter-spacing:.5px; }}
  .pill.ok {{ background:#1f6f34; color:#d7f5df; }}
  .pill.fail {{ background:#8e2323; color:#ffdede; }}
  details {{ border-bottom:1px solid #21262d; }}
  details:last-of-type {{ border-bottom:none; }}
  summary {{ cursor:pointer; padding:11px 14px; color:#e6edf3; font-size:14px; }}
  summary:hover {{ background:#1a2029; }}
  .cmd {{ color:#6e7681; font-family:ui-monospace,SFMono-Regular,monospace;
          font-size:12px; margin-left:6px; }}
  pre {{ margin:0; padding:12px 14px; background:#0a0d11; overflow:auto;
         max-height:420px; border-top:1px solid #21262d; }}
  pre.ai {{ white-space:pre-wrap; border-top:none; }}
  code {{ font-family:ui-monospace,SFMono-Regular,monospace; font-size:12px;
          color:#c9d1d9; white-space:pre; }}
  .empty {{ color:#6e7681; font-size:13px; padding:12px 14px; margin:0; }}
  .ai-off p {{ margin:8px 14px 0; color:#8b949e; font-size:14px; }}
  .ai-off .meta {{ margin:8px 14px 12px; }}
  .btn {{ display:inline-block; padding:6px 12px; border-radius:6px;
          border:1px solid #30363d; background:#21262d; color:#e6edf3;
          text-decoration:none; font-size:13px; }}
  .btn:hover {{ background:#30363d; }}
  footer {{ color:#6e7681; font-size:12px; text-align:center; margin-top:28px; }}
  @media (max-width: 620px) {{
    .verdict {{ font-size:19px; }}
    th, td {{ padding:8px 9px; }}
    td.detail {{ display:none; }}
  }}
</style>
</head>
<body>
<div class=wrap>
  <div class="banner {css}">
    <div class=verdict>{verdict}</div>
    <div class=counts>{ok_count} / {len(results)} checks OK</div>
    <div class=meta>Generated {generated} &middot; refreshes every 30 s</div>
  </div>
  {note}
  {system}
  <div class=card>
    <table>
      <thead><tr><th>Check</th><th>Status</th><th>Summary</th><th>Detail</th></tr></thead>
      <tbody>
{rows}
      </tbody>
    </table>
  </div>
  <h2 style="padding-left:0">Commands and raw output</h2>
  <div class=card>
{raw_blocks}
  </div>
  {_ai_card(ai_text, ai_error)}
  <footer>Snowk CyberDeck &middot; Network Diagnostics</footer>
</div>
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

    def _results(self) -> list[CheckResult]:
        server = self.server
        # The lock makes this single-flight: concurrent requests wait for the
        # one collector instead of each spawning their own subprocesses.
        with server.cache_lock:
            now = time.monotonic()
            if server.results is None or now - server.results_time >= CACHE_SECONDS:
                server.results = self.collect()
                server.results_time = now
            return server.results

    def _explanation(self, results: list[CheckResult]) -> tuple[str | None, str | None]:
        server = self.server
        with server.ai_lock:
            if server.ai_text and time.monotonic() - server.ai_time < AI_CACHE_SECONDS:
                return server.ai_text, None

            available, reason = is_available()
            if not available:
                return None, reason  # not cached: the service may come back
            try:
                text = "".join(stream_explanation(results)).strip()
            except LLMError as exc:
                return None, str(exc)
            except OSError as exc:
                return None, str(exc)
            if not text:
                return None, "The model returned no text."
            server.ai_text = text
            server.ai_time = time.monotonic()
            return text, None

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
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path not in ("/", "/index.html"):
            self.send_error(404, "Not found")
            return

        try:
            results = self._results()
            ai_text = ai_error = None
            if "ai" in urllib.parse.parse_qs(parsed.query):
                ai_text, ai_error = self._explanation(results)
            body = build_report(results, getattr(self.server, "bind_note", ""), ai_text=ai_text, ai_error=ai_error)
            self._send(200, body)
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
        self.results: list[CheckResult] | None = None
        self.results_time = 0.0
        self.ai_lock = threading.Lock()
        self.ai_text: str | None = None
        self.ai_time = 0.0


def create_server(
    address: str,
    port: int = DEFAULT_PORT,
    bind_note: str = "",
    handler: type[ReportHandler] = ReportHandler,
) -> ReportServer:
    server = ReportServer((address, port), handler)
    server.bind_note = bind_note
    return server
