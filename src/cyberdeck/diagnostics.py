"""Read-only network checks.

No UI code here and nothing that needs root. Every check returns a
CheckResult instead of raising, so a broken command can never take the
app down.

Each result has a short `summary` for the table (small screens!) and a
longer `detail` for the detail pane.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
from dataclasses import dataclass

COMMAND_TIMEOUT = 3
INTERNET_PING_TARGET = "1.1.1.1"
DNS_TEST_HOST = "pypi.org"

_RTT_RE = re.compile(r"time=([\d.]+)\s*ms")


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    summary: str
    detail: str


def _command_output(args: list[str], timeout: int = COMMAND_TIMEOUT) -> tuple[str | None, str | None]:
    """Run a command with an argument list (never a shell).

    Returns (stdout, error). error is None only on a clean exit 0.
    """
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return None, f"{args[0]} not installed"
    except subprocess.TimeoutExpired:
        return None, f"{args[0]} timed out after {timeout}s"
    except OSError as exc:
        return None, str(exc)

    if proc.returncode != 0:
        reason = proc.stderr.strip() or "failed"
        return proc.stdout, f"exit {proc.returncode}: {reason}"
    return proc.stdout, None


def check_interfaces() -> CheckResult:
    out, err = _command_output(["ip", "-j", "addr", "show"])
    if err:
        return CheckResult("Interfaces", False, "error", err)

    try:
        interfaces = json.loads(out or "[]")
    except json.JSONDecodeError:
        return CheckResult("Interfaces", False, "error", "could not parse ip output")

    up = []
    for iface in interfaces:
        # loopback reports operstate UNKNOWN, so treat it as up too.
        if iface.get("operstate") not in ("UP", "UNKNOWN"):
            continue
        ipv4 = [a["local"] for a in iface.get("addr_info", []) if a.get("family") == "inet"]
        if ipv4 and iface.get("ifname") != "lo":
            up.append(f"{iface['ifname']} {', '.join(ipv4)}")

    if not up:
        return CheckResult("Interfaces", False, "0 up", "No interface is up with an IPv4 address.")
    return CheckResult("Interfaces", True, f"{len(up)} up", "; ".join(up))


def check_default_route() -> tuple[CheckResult, str | None]:
    """Returns the result and the gateway address (None if there is no route)."""
    out, err = _command_output(["ip", "-j", "route", "show", "default"])
    if err:
        return CheckResult("Default route", False, "error", err), None

    try:
        routes = json.loads(out or "[]")
    except json.JSONDecodeError:
        return CheckResult("Default route", False, "error", "could not parse ip output"), None

    if not routes:
        return CheckResult("Default route", False, "none", "No default route."), None

    route = routes[0]
    gateway = route.get("gateway")
    detail = (
        f"via {gateway or '(no gateway)'} dev {route.get('dev', '?')}"
        f" proto {route.get('protocol', '?')}"
    )
    return CheckResult("Default route", gateway is not None, gateway or "no gateway", detail), gateway


def check_dns_config() -> CheckResult:
    try:
        with open("/etc/resolv.conf", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError as exc:
        return CheckResult("DNS servers", False, "error", str(exc))

    servers = [
        line.split()[1]
        for line in lines
        if line.strip().startswith("nameserver") and len(line.split()) > 1
    ]
    if not servers:
        return CheckResult("DNS servers", False, "0 servers", "No nameserver in /etc/resolv.conf.")
    return CheckResult("DNS servers", True, f"{len(servers)} server(s)", ", ".join(servers))


def _ping(name: str, host: str) -> CheckResult:
    out, err = _command_output(["ping", "-c", "1", "-W", "1", "-n", host])
    # A non-zero exit here means "no reply", which is a result, not an error.
    if err and not err.startswith("exit"):
        return CheckResult(name, False, "error", err)
    if err:
        return CheckResult(name, False, "no reply", f"{host} did not answer.")

    match = _RTT_RE.search(out or "")
    if not match:
        return CheckResult(name, True, "reply", f"{host} replied (no timing reported).")
    return CheckResult(name, True, f"{match.group(1)} ms", f"{host} replied in {match.group(1)} ms.")


def check_gateway_ping(gateway: str | None) -> CheckResult:
    if not gateway:
        return CheckResult("Ping gateway", False, "no gateway", "No default gateway to ping.")
    return _ping("Ping gateway", gateway)


def check_internet_ping() -> CheckResult:
    return _ping(f"Ping {INTERNET_PING_TARGET}", INTERNET_PING_TARGET)


def check_dns_resolution() -> CheckResult:
    # getaddrinfo() has no timeout argument; the scan runs in a worker
    # thread so a slow resolver delays the report but never freezes the UI.
    name = f"Resolve {DNS_TEST_HOST}"
    try:
        infos = socket.getaddrinfo(DNS_TEST_HOST, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        return CheckResult(name, False, "failed", f"{DNS_TEST_HOST}: {exc.strerror or exc}")
    except OSError as exc:
        return CheckResult(name, False, "failed", str(exc))

    if not infos:
        return CheckResult(name, False, "failed", f"{DNS_TEST_HOST} returned no address.")
    address = infos[0][4][0]
    return CheckResult(name, True, address, f"{DNS_TEST_HOST} -> {address}")


def check_listening_ports() -> CheckResult:
    out, err = _command_output(["ss", "-ltn"])
    if err:
        return CheckResult("Listening ports", False, "error", err)

    ports = []
    for line in (out or "").splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 4 and fields[0] == "LISTEN":
            ports.append(fields[3])

    if not ports:
        return CheckResult("Listening ports", True, "none", "No listening TCP ports.")
    return CheckResult("Listening ports", True, f"{len(ports)} listening", ", ".join(ports))


def run_all_checks() -> list[CheckResult]:
    route_result, gateway = check_default_route()
    return [
        check_interfaces(),
        route_result,
        check_dns_config(),
        check_gateway_ping(gateway),
        check_internet_ping(),
        check_dns_resolution(),
        check_listening_ports(),
    ]
