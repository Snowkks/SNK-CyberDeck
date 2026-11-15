"""Screens that are not the main menu.

Written for the Linux console on the 7" panel: ASCII only (no ✓/✗ or
rounded borders, which console bitmap fonts often lack), ANSI-16 colours,
and a layout that fits 80x24 upwards.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from cyberdeck.diagnostics import CheckResult, run_all_checks
from cyberdeck.llm import LLMError, is_available, stream_explanation


class ComingSoonScreen(Screen):
    """Placeholder for a tool that is not implemented yet."""

    def __init__(self, tool_name: str) -> None:
        super().__init__()
        self.tool_name = tool_name

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            f"{self.tool_name}\n\nComing soon.\n\nPress any key to return.",
            id="coming-soon",
        )
        yield Footer()

    def on_key(self) -> None:
        self.dismiss()


class DiagnosticsScreen(Screen):
    """Read-only snapshot of the machine's network state."""

    BINDINGS = [
        ("escape", "dismiss_screen", "Back"),
        ("r", "rescan", "Rescan"),
        ("e", "explain", "Explain with AI"),
    ]

    CSS = """
    DataTable {
        height: 1fr;
    }
    #detail-pane {
        height: 5;
        padding: 0 1;
        border: ascii $accent;
    }
    #buttons {
        height: 1;
        align-horizontal: right;
    }
    #buttons Button {
        height: 1;
        min-width: 12;
        border: none;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._results: list[CheckResult] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="results", cursor_type="row", zebra_stripes=True)
        yield Static("Highlight a row to see the full result.", id="detail-pane", markup=False)
        yield Horizontal(
            Button("Explain", id="explain", variant="primary"),
            Button("Rescan", id="rescan"),
            Button("Back", id="back"),
            id="buttons",
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Check", "Status", "Summary")
        self.action_rescan()

    def action_dismiss_screen(self) -> None:
        self.dismiss()

    def action_rescan(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        table.add_row("Scanning...", "", "")
        self._results = []
        self.query_one("#detail-pane", Static).update("Running checks...")
        # thread=True keeps pings and DNS off the UI thread; exclusive=True
        # cancels an in-flight scan if "r" is pressed again.
        self.run_worker(self._scan, thread=True, exclusive=True, group="scan")
        table.focus()

    def action_explain(self) -> None:
        if not self._results:
            self.query_one("#detail-pane", Static).update("Wait for the scan to finish first.")
            return
        self.app.push_screen(ExplainScreen(self._results))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "explain":
            self.action_explain()
        elif event.button.id == "rescan":
            self.action_rescan()
        elif event.button.id == "back":
            self.dismiss()

    def _scan(self) -> None:
        results = run_all_checks()
        self.app.call_from_thread(self._show_results, results)

    def _show_results(self, results: list[CheckResult]) -> None:
        table = self.query_one(DataTable)
        table.clear()
        self._results = results
        for result in results:
            status = Text("OK", style="ansi_green") if result.ok else Text("FAIL", style="ansi_red")
            table.add_row(result.name, status, result.summary)
        table.move_cursor(row=0)
        self._update_pane(0)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._update_pane(event.cursor_row)

    def _update_pane(self, index: int) -> None:
        pane = self.query_one("#detail-pane", Static)
        if not self._results or not 0 <= index < len(self._results):
            pane.update("Highlight a row to see the full result.")
            return
        result = self._results[index]
        pane.update(f"{result.name}: {result.detail}")


class ExplainScreen(Screen):
    """Ask the local model to explain the scan results."""

    BINDINGS = [("escape", "cancel", "Back")]

    CSS = """
    #explain-text {
        padding: 1 2;
    }
    """

    WAITING_TEXT = "Checking the model..."

    def __init__(self, results: list[CheckResult]) -> None:
        super().__init__()
        self.results = results
        self._text = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static(self.WAITING_TEXT, id="explain-text", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        # The model can take seconds to answer on a Pi; never block the UI.
        self.run_worker(self._stream, thread=True, exclusive=True, group="explain")

    def action_cancel(self) -> None:
        self.workers.cancel_group(self, "explain")
        self.dismiss()

    def _stream(self) -> None:
        available, reason = is_available()
        if not available:
            self._show("AI unavailable", reason)
            return

        self._show("", "")
        received = False
        try:
            for chunk in stream_explanation(self.results):
                received = True
                self.app.call_from_thread(self._append, chunk)
        except LLMError as exc:
            self._show("AI unavailable", str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - never crash the TUI on this
            self._show("AI error", str(exc))
            return

        # Tracked in this thread: _text is updated asynchronously on the UI
        # thread, so reading it here would race the final chunks.
        if not received:
            self._show("AI error", "The model returned no text.")

    def _show(self, title: str, message: str) -> None:
        text = f"{title}\n\n{message}".strip()
        self.app.call_from_thread(self._set_text, text)

    def _set_text(self, text: str) -> None:
        if not self.is_attached:
            return
        self._text = text
        self.query_one("#explain-text", Static).update(text)

    def _append(self, chunk: str) -> None:
        if not self.is_attached:
            return
        self._text += chunk
        self.query_one("#explain-text", Static).update(self._text)
        self.query_one(VerticalScroll).scroll_end(animate=False)
