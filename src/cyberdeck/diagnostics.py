"""Read-only network checks.

No UI code here and nothing that needs root. Every check returns a
CheckResult instead of raising, so a broken command can never take the
app down.

Each result has a short `summary` for the table (small screens!) and a
longer `detail` for the detail pane.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import subprocess
from dataclasses import dataclass

COMMAND_TIMEOUT = 3
INTERNET_PING_TARGET = "1.1.1.1"
DNS_TEST_HOST = "pypi.org"
MAX_HOST_LENGTH = 253

_RTT_RE = re.compile(r"time=([\d.]+)\s*ms")

# Letters, digits, dots, dashes, colons (IPv6) and brackets ([::1]).
# Anything else - spaces, ';', '$', quotes - is rejected before a command
# ever sees the value.
_HOST_CHARS_RE = re.compile(r"[A-Za-z0-9._:\[\]-]+")


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    summary: str
    detail: str
    # Filled in where a check can show the command it ran and its output.
    # The TUI ignores these; the web report prints them.
    command: str = ""
    raw: str = ""


def _command_output(args: list[str], timeout: int = COMMAND_TIMEOUT) -> tuple[str | None, str | None]:
    """Run a command with an argument list (never a shell).

    Returns (stdout, error). error is None only on a clean exit 0.
    """
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # a bare console may use LANG=C; never crash on bytes
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
        return CheckResult("Interfaces", False, "0 up", "No interface is up with an IPv4 address.",
                            command="ip -j addr show", raw=out or "")
    return CheckResult("Interfaces", True, f"{len(up)} up", "; ".join(up),
                        command="ip -j addr show", raw=out or "")


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
        return CheckResult("Default route", False, "none", "No default route.",
                            command="ip -j route show default", raw=out or ""), None

    route = routes[0]
    gateway = route.get("gateway")
    detail = (
        f"via {gateway or '(no gateway)'} dev {route.get('dev', '?')}"
        f" proto {route.get('protocol', '?')}"
    )
    return CheckResult("Default route", gateway is not None, gateway or "no gateway", detail,
                        command="ip -j route show default", raw=out or ""), gateway


def check_dns_config() -> CheckResult:
    try:
        with open("/etc/resolv.conf", encoding="utf-8") as handle:
            content = handle.read()
    except OSError as exc:
        return CheckResult("DNS servers", False, "error", str(exc))

    lines = content.splitlines()

    servers = [
        line.split()[1]
        for line in lines
        if line.strip().startswith("nameserver") and len(line.split()) > 1
    ]
    if not servers:
        return CheckResult("DNS servers", False, "0 servers", "No nameserver in /etc/resolv.conf.",
                            command="cat /etc/resolv.conf", raw=content)
    return CheckResult("DNS servers", True, f"{len(servers)} server(s)", ", ".join(servers),
                        command="cat /etc/resolv.conf", raw=content)


def _ping(name: str, host: str, wait: int = 1) -> CheckResult:
    command = f"ping -c 1 -W {wait} -n {host}"
    out, err = _command_output(
        ["ping", "-c", "1", "-W", str(wait), "-n", host], timeout=COMMAND_TIMEOUT + wait
    )
    # A non-zero exit here means "no reply", which is a result, not an error.
    if err and not err.startswith("exit"):
        return CheckResult(name, False, "error", err, command=command)
    if err:
        return CheckResult(name, False, "no reply", f"{host} did not answer.",
                            command=command, raw=out or "")

    match = _RTT_RE.search(out or "")
    if not match:
        return CheckResult(name, True, "reply", f"{host} replied (no timing reported).",
                            command=command, raw=out or "")
    return CheckResult(name, True, f"{match.group(1)} ms", f"{host} replied in {match.group(1)} ms.",
                        command=command, raw=out or "")


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
    addresses = ", ".join(sorted({info[4][0] for info in infos}))
    return CheckResult(name, True, address, f"{DNS_TEST_HOST} -> {address}",
                        command=f"python: socket.getaddrinfo({DNS_TEST_HOST!r}, 443)", raw=addresses)


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
        return CheckResult("Listening ports", True, "none", "No listening TCP ports.",
                            command="ss -ltn", raw=out or "")
    return CheckResult("Listening ports", True, f"{len(ports)} listening", ", ".join(ports),
                        command="ss -ltn", raw=out or "")


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


def validate_host(value: str) -> str | None:
    """Return an error message for an unusable host, or None if it is fine.

    This is a security boundary: ping parses its own arguments, so a value
    like "-f" or "--flood" would be treated as an option and flood the
    network. Such values never reach subprocess.
    """
    host = (value or "").strip()
    if not host:
        return "Enter a hostname or IP address."
    if len(host) > MAX_HOST_LENGTH:
        return f"Host is too long (max {MAX_HOST_LENGTH} characters)."
    if host.startswith("-"):
        return "Host cannot start with '-' (it would be read as a ping option)."
    if not _HOST_CHARS_RE.fullmatch(host):
        return "Host may only contain letters, digits, dots, dashes and colons."
    return None


def check_host_ping(host: str) -> CheckResult:
    error = validate_host(host)
    if error:
        return CheckResult(f"Ping {host or '(empty)'}", False, "invalid", error)
    # wait=2: a remote host deserves more patience than a local gateway.
    return _ping(f"Ping {host}", host.strip(), wait=2)


def check_host_resolution(host: str) -> CheckResult:
    error = validate_host(host)
    if error:
        return CheckResult(f"Resolve {host or '(empty)'}", False, "invalid", error)

    host = host.strip()
    name = f"Resolve {host}"
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        return CheckResult(name, True, str(literal), f"{host} is already a literal IP address.")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return CheckResult(name, False, "failed", f"{host}: {exc.strerror or exc}")
    except UnicodeError:
        # Names like "." or "a..b" have empty DNS labels; the idna codec
        # raises instead of failing the lookup, which would kill the worker.
        return CheckResult(name, False, "failed", f"{host} is not a valid DNS name.")
    except OSError as exc:
        return CheckResult(name, False, "failed", str(exc))

    addresses = []
    for info in infos:
        address = info[4][0]
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        return CheckResult(name, False, "failed", f"{host} returned no address.")

    shown = ", ".join(addresses[:4]) + (f" (+{len(addresses) - 4} more)" if len(addresses) > 4 else "")
    return CheckResult(name, True, addresses[0], f"{host} -> {shown}",
                        command=f"python: socket.getaddrinfo({host!r}, None)",
                        raw="\n".join(addresses))


def run_host_checks(host: str) -> list[CheckResult]:
    return [check_host_ping(host), check_host_resolution(host)]
