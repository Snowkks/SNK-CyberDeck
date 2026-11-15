"""TCP port checks.

No UI, no shell, no privileges: the check is the TCP handshake itself.
"""

from __future__ import annotations

import socket
import time

from cyberdeck.diagnostics import CheckResult, validate_host

DEFAULT_TIMEOUT = 2
MAX_PORT = 65535


def validate_port(value: str) -> str | None:
    """Return an error message for an unusable port, or None."""
    text = (value or "").strip()
    if not text:
        return "Enter a port number (1-65535)."
    if not text.isdigit():
        return "Port must be a number."
    port = int(text)
    if not 1 <= port <= MAX_PORT:
        return f"Port must be between 1 and {MAX_PORT}."
    return None


def check_port(host: str, port: str | int, timeout: float = DEFAULT_TIMEOUT) -> CheckResult:
    """Try a TCP connection and report what the network did.

    open     - the handshake completed, something is listening
    closed   - the peer answered with RST (nothing listening)
    filtered - no answer at all (firewall or no route)
    """
    name = f"tcp {host}:{port}"
    host_error = validate_host(host)
    if host_error:
        return CheckResult(name, False, "invalid", host_error)
    port_error = validate_port(str(port))
    if port_error:
        return CheckResult(name, False, "invalid", port_error)

    host = host.strip()
    number = int(port)
    command = f"python: socket.create_connection(({host!r}, {number}), timeout={timeout})"
    started = time.monotonic()
    try:
        with socket.create_connection((host, number), timeout=timeout) as connection:
            peer = connection.getpeername()[0]
    except ConnectionRefusedError:
        return CheckResult(name, False, "closed", f"{host}:{number} refused the connection (nothing listening).",
                            command=command)
    except TimeoutError:
        return CheckResult(name, False, "filtered", f"{host}:{number} did not answer within {timeout}s (firewall or no route).",
                            command=command)
    except socket.gaierror as exc:
        return CheckResult(name, False, "unresolved", f"{host}: {exc.strerror or exc}", command=command)
    except OSError as exc:
        return CheckResult(name, False, "error", str(exc), command=command)

    elapsed_ms = (time.monotonic() - started) * 1000
    return CheckResult(
        name,
        True,
        "open",
        f"{host} accepted a TCP connection on port {number} ({peer}, {elapsed_ms:.0f} ms).",
        command=command,
    )
