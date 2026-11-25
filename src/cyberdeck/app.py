"""Main CyberDeck launcher."""

from datetime import datetime
import socket

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from cyberdeck.screens import SubmenuScreen, TailnetScreen
from cyberdeck.timer import TimerScreen
from cyberdeck.tools import grouped_tools


class CyberDeckApp(App):
    """Small keyboard-first launcher for the CyberDeck tools."""

    TITLE = "SNK CYBERDECK"
    SUB_TITLE = "LOCAL TOOLKIT"
    theme = "ansi-dark"

    CSS = """
    Screen {
        align: center middle;
    }

    #shell {
        width: 76;
        height: auto;
        max-height: 90%;
        border: ascii $accent;
        padding: 1 2;
    }

    #intro {
        height: auto;
        margin-bottom: 1;
    }

    #title {
        color: $accent;
        text-style: bold;
    }

    #subtitle, #status, #hint {
        color: $text-muted;
    }

    #menu {
        height: auto;
        max-height: 14;
        border: none;
    }

    .category {
        color: $accent;
        text-style: bold;
        padding: 1 0 0 0;
    }

    ListItem {
        height: 2;
        padding: 0 1;
    }

    #status {
        height: 1;
        margin-top: 1;
    }

    #hint {
        height: 1;
    }
    """

    BINDINGS = [("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="shell"):
            yield Vertical(
                Label("SNK CYBERDECK", id="title"),
                Label("A compact toolkit for systems and networks", id="subtitle"),
                id="intro",
            )
            yield ListView(*self.create_menu_items(), id="menu")
            yield Static("", id="status")
            yield Static("↑ ↓ move   Enter open   q quit", id="hint")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#menu", ListView).focus()
        self.refresh_status()
        self.set_interval(1, self.refresh_status)

    def create_menu_items(self) -> list[ListItem]:
        """Build the launcher from the tool catalog."""

        items: list[ListItem] = []
        for category, tools in grouped_tools().items():
            items.append(ListItem(Label(category), disabled=True, classes="category"))
            items.extend(
                ListItem(Label(tool.name), id=tool.identifier)
                for tool in tools
            )
        return items

    def refresh_status(self) -> None:
        """Show a quiet, useful machine status line."""

        now = datetime.now().astimezone()
        self.query_one("#status", Static).update(
            f"{socket.gethostname()}  ·  {now.strftime('%H:%M:%S %Z')}"
        )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Open the selected tool."""

        destinations = {
            "timer": TimerScreen,
            "network": SubmenuScreen,
            "tailscale": TailnetScreen,
        }
        screen = destinations.get(event.item.id)
        if screen is not None:
            self.push_screen(screen())
        elif event.item.id == "quit":
            self.exit()
