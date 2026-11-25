from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Tool:
    """A tool displayed in the CyberDeck menu."""

    identifier: str
    name: str
    category: str
    description: str
    shortcut: str


TOOLS: Final[list[Tool]] = [
    Tool(
        identifier="timer",
        name="Time and Timers",
        category="SYSTEM",
        description="Clock, Pomodoro, countdown, and stopwatch.",
        shortcut="t",
    ),
    Tool(
        identifier="network",
        name="Network Diagnostics",
        category="NETWORK",
        description="Inspect local network information.",
        shortcut="n",
    ),
    Tool(
        identifier="tailscale",
        name="Tailscale",
        category="NETWORK",
        description="Connect or disconnect the tailnet.",
        shortcut="t",
    ),
    Tool(
        identifier="quit",
        name="Exit CyberDeck",
        category="SYSTEM",
        description="Close the application.",
        shortcut="q",
    ),
]


def grouped_tools() -> dict[str, list[Tool]]:
    """Group tools by category."""

    result: dict[str, list[Tool]] = {}

    for tool in TOOLS:
        result.setdefault(tool.category, []).append(tool)

    return result
