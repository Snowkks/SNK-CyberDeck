"""Tailscale on/off, shelling out to the real `tailscale` command.

Status is read without privileges. Changing state uses sudo, which is why
the caller must wrap `set_state()` in `app.suspend()` so sudo can prompt
for a password on the real terminal.

Setup note for the Pi: `tailscale up`/`down` need root unless an operator
is configured. This module deliberately uses sudo; the alternative is a
one-time `sudo tailscale set --operator=$USER`.
"""

from __future__ import annotations

import json
import subprocess

COMMAND_TIMEOUT = 5
TOGGLE_TIMEOUT = 120

_UP_STATES = {"Running"}


def status(timeout: int = COMMAND_TIMEOUT) -> tuple[str, str]:
    """Return ("up"|"down"|"unknown", human-readable detail)."""
    try:
        proc = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return "unknown", "tailscale is not installed."
    except subprocess.TimeoutExpired:
        return "unknown", f"tailscale status timed out after {timeout}s."
    except OSError as exc:
        return "unknown", str(exc)

    if proc.returncode != 0:
        return "unknown", (proc.stderr or "tailscale status failed").strip()

    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return "unknown", "could not parse tailscale status."
    if not isinstance(payload, dict):
        return "unknown", "unexpected tailscale status output."

    backend = payload.get("BackendState", "")
    if backend in _UP_STATES:
        return "up", f"BackendState: {backend}"
    return "down", f"BackendState: {backend or 'unknown'}"


def set_state(up: bool, timeout: int = TOGGLE_TIMEOUT) -> tuple[bool, str]:
    """Run `sudo tailscale up|down`. Inherits the terminal so sudo can prompt.

    Call this inside `app.suspend()`.
    """
    command = ["sudo", "tailscale", "up" if up else "down"]
    try:
        proc = subprocess.run(command, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        return False, f"{exc.filename or 'command'} not found."
    except subprocess.TimeoutExpired:
        return False, f"tailscale timed out after {timeout}s."
    except OSError as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, f"exit {proc.returncode} - see the message above."
    return True, f"Tailscale turned {'on' if up else 'off'}."
