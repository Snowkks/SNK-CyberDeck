"""The CyberDeck launcher: a menu of tools."""

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Label, ListItem, ListView

from cyberdeck.screens import DiagnosticsScreen


class CyberDeckApp(App):
    """Main menu. Each ListItem id maps to one action."""

    TITLE = "Snowk CyberDeck"
    SUB_TITLE = "v0.1.0"

    # ansi-dark only uses the 16 ANSI colours, which is all the Linux
    # console on the Pi's 7" panel can display.
    theme = "ansi-dark"

    CSS = """
    ListView {
        width: 34;
        height: auto;
        margin: 1 0;
        border: ascii $accent;
    }
    ListItem {
        padding: 0 1;
    }
    """

    BINDINGS = [("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield ListView(
            ListItem(Label("Network Diagnostics"), id="network-diagnostics"),
            ListItem(Label("Quit"), id="quit"),
            id="menu",
        )
        yield Footer()

    def on_mount(self) -> None:
        # Without focus the arrow keys would not reach the list.
        self.query_one("#menu", ListView).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.id == "network-diagnostics":
            self.push_screen(DiagnosticsScreen())
        elif event.item.id == "quit":
            self.exit()
