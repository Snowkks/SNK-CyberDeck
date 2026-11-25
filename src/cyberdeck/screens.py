from __future__ import annotations

import threading
import time
from functools import partial
from datetime import datetime

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
)

from cyberdeck import web
from cyberdeck.diagnostics import (
    CheckResult,
    run_all_checks,
    run_host_checks,
    validate_host,
)
from cyberdeck.llm import LLMError, is_available, stream_explanation
from cyberdeck import tailnet
from cyberdeck.ports import check_port, validate_port


def _status_text(ok: bool) -> Text:
    # ANSI names, not hex: the Linux console has 16 colours.
    return Text("OK", style="ansi_green") if ok else Text("FAIL", style="ansi_red")


def _detail_for(results: list[CheckResult], index: int) -> str:
    if not results or not 0 <= index < len(results):
        return "Highlight a row to see the full result."
    result = results[index]
    return f"{result.name}: {result.detail}"


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
        ("t", "test_host", "Test host"),
        ("w", "web_ui", "Web UI"),
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
        # Bumped on every rescan so a slow, superseded scan cannot overwrite
        # fresher results when it finally finishes.
        self._generation = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="results", cursor_type="row", zebra_stripes=True)
        yield Static(
            "Highlight a row to see the full result.", id="detail-pane", markup=False
        )
        yield Horizontal(
            Button("Explain", id="explain", variant="primary"),
            Button("Test host", id="test-host"),
            Button("Web UI", id="web-ui"),
            Button("Rescan", id="rescan"),
            Button("Back", id="back"),
            id="buttons",
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Check", "Status", "Summary")
        self.action_rescan()

    def on_unmount(self) -> None:
        self.workers.cancel_group(self, "scan")

    def action_dismiss_screen(self) -> None:
        self.dismiss()

    def action_rescan(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        table.add_row("Scanning...", "", "")
        self._results = []
        self._generation += 1
        generation = self._generation
        self.query_one("#detail-pane", Static).update("Running checks...")
        # thread=True keeps pings and DNS off the UI thread; exclusive=True
        # cancels an in-flight scan if "r" is pressed again. exit_on_error
        # keeps a failed check from taking the whole app down.
        self.run_worker(
            partial(self._scan, generation),
            thread=True,
            exclusive=True,
            group="scan",
            exit_on_error=False,
        )
        table.focus()

    def action_explain(self) -> None:
        if not self._results:
            self.query_one("#detail-pane", Static).update(
                "Wait for the scan to finish first."
            )
            return
        self.app.push_screen(ExplainScreen(self._results))

    def action_test_host(self) -> None:
        self.app.push_screen(HostTestScreen())

    def action_web_ui(self) -> None:
        self.app.push_screen(WebUIScreen())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "explain":
            self.action_explain()
        elif event.button.id == "test-host":
            self.action_test_host()
        elif event.button.id == "web-ui":
            self.action_web_ui()
        elif event.button.id == "rescan":
            self.action_rescan()
        elif event.button.id == "back":
            self.dismiss()

    def _scan(self, generation: int) -> None:
        results = run_all_checks()
        self.app.call_from_thread(self._show_results, generation, results)

    def _show_results(self, generation: int, results: list[CheckResult]) -> None:
        # A superseded scan, or one whose screen is gone, must not touch the UI.
        if generation != self._generation or not self.is_attached:
            return
        table = self.query_one(DataTable)
        table.clear()
        self._results = results
        for result in results:
            table.add_row(result.name, _status_text(result.ok), result.summary)
        table.move_cursor(row=0)
        self._update_pane(0)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._update_pane(event.cursor_row)

    def _update_pane(self, index: int) -> None:
        self.query_one("#detail-pane", Static).update(_detail_for(self._results, index))


class HostTestScreen(Screen):
    """Ping and resolve a host the user types in."""

    BINDINGS = [("escape", "dismiss_screen", "Back")]

    CSS = """
    #host-input {
        margin: 0 0 1 0;
    }
    DataTable {
        height: 1fr;
    }
    #host-detail {
        height: 4;
        padding: 0 1;
        border: ascii $accent;
    }
    """

    PROMPT = "Type a hostname or IP address, then press Enter."

    def __init__(self) -> None:
        super().__init__()
        self._results: list[CheckResult] = []
        self._generation = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="e.g. pypi.org or 192.168.1.1", id="host-input")
        yield DataTable(id="host-results", cursor_type="row")
        yield Static(self.PROMPT, id="host-detail", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("Check", "Status", "Summary")
        self.query_one(Input).focus()

    def on_unmount(self) -> None:
        self.workers.cancel_group(self, "host")

    def action_dismiss_screen(self) -> None:
        self.dismiss()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        host = event.value.strip()
        error = validate_host(host)
        if error:
            self.query_one("#host-detail", Static).update(error)
            return

        table = self.query_one(DataTable)
        table.clear()
        table.add_row("Checking...", "", "")
        self._results = []
        self._generation += 1
        generation = self._generation
        self.query_one("#host-detail", Static).update(f"Checking {host}...")
        # partial() so the worker gets the host; thread=True so a slow
        # resolver cannot freeze the UI.
        self.run_worker(
            partial(self._run, generation, host),
            thread=True,
            exclusive=True,
            group="host",
            exit_on_error=False,
        )

    def _run(self, generation: int, host: str) -> None:
        results = run_host_checks(host)
        self.app.call_from_thread(self._show_results, generation, results)

    def _show_results(self, generation: int, results: list[CheckResult]) -> None:
        if generation != self._generation or not self.is_attached:
            return
        table = self.query_one(DataTable)
        table.clear()
        self._results = results
        for result in results:
            table.add_row(result.name, _status_text(result.ok), result.summary)
        table.move_cursor(row=0)
        self._update_pane(0)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._update_pane(event.cursor_row)

    def _update_pane(self, index: int) -> None:
        self.query_one("#host-detail", Static).update(_detail_for(self._results, index))


class SubmenuScreen(Screen):
    """The Network Diagnostics submenu between the launcher and the tools."""

    BINDINGS = [("escape", "dismiss_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield ListView(
            ListItem(Label("Local TUI"), id="local-tui"),
            ListItem(Label("Port Check"), id="port-check"),
            ListItem(Label("Start Tailnet Web UI"), id="web-ui"),
            ListItem(Label("Back"), id="back"),
            id="diagnostics-menu",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#diagnostics-menu", ListView).focus()

    def action_dismiss_screen(self) -> None:
        self.dismiss()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.id == "local-tui":
            self.app.push_screen(DiagnosticsScreen())
        elif event.item.id == "port-check":
            self.app.push_screen(PortCheckScreen())
        elif event.item.id == "web-ui":
            self.app.push_screen(WebUIScreen())
        elif event.item.id == "back":
            self.dismiss()


class PortCheckScreen(Screen):
    """One TCP port check: host + port, then the result."""

    BINDINGS = [("escape", "dismiss_screen", "Back")]

    CSS = """
    #port-host, #port-number {
        margin: 0 0 1 0;
    }
    #port-verdict {
        padding: 0 1;
    }
    #port-detail {
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._generation = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Host (e.g. 192.168.1.10)", id="port-host")
        yield Input(placeholder="Port (1-65535)", id="port-number")
        yield Static(
            "Enter a host and a port, then press Enter.",
            id="port-verdict",
            markup=False,
        )
        yield Static("", id="port-detail", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#port-host", Input).focus()

    def on_unmount(self) -> None:
        self.workers.cancel_group(self, "port")

    def action_dismiss_screen(self) -> None:
        self.dismiss()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        host = self.query_one("#port-host", Input).value.strip()
        port = self.query_one("#port-number", Input).value.strip()

        error = validate_host(host) or validate_port(port)
        if error:
            self.query_one("#port-verdict", Static).update(f"Invalid: {error}")
            self.query_one("#port-detail", Static).update("")
            return

        self._generation += 1
        self.query_one("#port-verdict", Static).update(
            f"Connecting to {host}:{port}..."
        )
        self.query_one("#port-detail", Static).update("")
        self.run_worker(
            partial(self._run, self._generation, host, port),
            thread=True,
            exclusive=True,
            group="port",
            exit_on_error=False,
        )

    def _run(self, generation: int, host: str, port: str) -> None:
        result = check_port(host, port)
        self.app.call_from_thread(self._show_result, generation, result)

    def _show_result(self, generation: int, result: CheckResult) -> None:
        if generation != self._generation or not self.is_attached:
            return
        verdict = (
            Text("OPEN", style="ansi_green")
            if result.ok
            else Text(result.summary.upper(), style="ansi_red")
        )
        verdict.append(f"  {result.name}", style="default")
        self.query_one("#port-verdict", Static).update(verdict)
        self.query_one("#port-detail", Static).update(result.detail)


class TailnetScreen(Screen):
    """Show Tailscale state and toggle it on/off."""

    BINDINGS = [
        ("escape", "back", "Back"),
        ("t", "toggle", "Toggle"),
    ]

    CSS = """
    #tailnet-info {
        padding: 1 2;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._armed = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Checking Tailscale...", id="tailnet-info", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()

    def on_unmount(self) -> None:
        # The menu label shows the state; keep it current after any exit path.
        self.app.refresh_tailnet_label()

    def action_back(self) -> None:
        self.dismiss()

    def _refresh(self, extra: str = "") -> None:
        state, detail = tailnet.status()
        lines = [f"Tailscale: {state.upper()}", detail, ""]
        if state == "up":
            lines += [
                "Press t to disconnect.",
                "Warning: that drops tailnet SSH sessions.",
            ]
        elif state == "down":
            lines += ["Press t to connect."]
        else:
            lines += ["Check it from a shell: tailscale status"]
        lines += [
            "",
            "Uses sudo - the terminal is handed back for the password prompt.",
        ]
        if extra:
            lines += ["", extra]
        self._armed = False
        self.query_one("#tailnet-info", Static).update("\n".join(lines))

    def action_toggle(self) -> None:
        state, _detail = tailnet.status()

        if state == "up" and not self._armed:
            self._armed = True
            self.query_one("#tailnet-info", Static).update(
                "Disconnecting drops tailnet connections, including SSH.\n\n"
                "Press t again to confirm, Esc to cancel."
            )
            return

        self._armed = False
        turn_on = state != "up"
        self.query_one("#tailnet-info", Static).update(
            f"Turning Tailscale {'on' if turn_on else 'off'}... (sudo may ask for a password)"
        )
        # suspend() gives the terminal back so sudo can read the password;
        # without it the prompt would be swallowed by the TUI.
        with self.app.suspend():
            ok, message = tailnet.set_state(turn_on)
        self._refresh("" if ok else message)


class WebUIScreen(Screen):
    """Run the HTML report server until the user leaves this screen."""

    BINDINGS = [("escape", "stop_and_back", "Stop")]

    CSS = """
    #web-info {
        padding: 1 2;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._server = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Starting the web UI...", id="web-info", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self._start()

    def action_stop_and_back(self) -> None:
        self.dismiss()

    def on_unmount(self) -> None:
        # Also runs when the app quits, so the port is always released.
        self._stop()

    def _start(self) -> None:
        address = web.tailnet_address()
        note = ""
        if address is None:
            address = "127.0.0.1"
            note = (
                "Tailscale not detected. Reach it with: ssh -L 8080:127.0.0.1:8080 <pi>"
            )

        try:
            server = web.create_server(address, bind_note=note)
        except OSError as exc:
            self.query_one("#web-info", Static).update(
                f"Could not start the web UI on {address}: {exc}"
            )
            return

        self._server = server
        threading.Thread(
            target=server.serve_forever, daemon=True, name="cyberdeck-web"
        ).start()

        lines = [f"Web UI running:  http://{address}:{server.server_address[1]}/", ""]
        if note:
            lines += [note, ""]
        lines += [
            "Open that address from any device on your tailnet.",
            "It is a detailed report: every check plus the raw command output.",
            "The page refreshes every 30 seconds.",
            "Press Esc to stop the server.",
        ]
        self.query_one("#web-info", Static).update("\n".join(lines))

    def _stop(self) -> None:
        server = self._server
        if server is None:
            return
        self._server = None
        server.shutdown()
        server.server_close()


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
        # Textual cannot cancel a thread worker, so the worker checks this
        # between chunks and stops reading from Ollama itself.
        self._cancel = threading.Event()
        self._waiting = False
        self._wait_started = 0.0
        self._timer = None

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static(self.WAITING_TEXT, id="explain-text", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        # The model can take seconds to answer on a Pi; never block the UI.
        self.run_worker(
            self._stream,
            thread=True,
            exclusive=True,
            group="explain",
            exit_on_error=False,
        )

    def action_cancel(self) -> None:
        self._cancel.set()
        self.workers.cancel_group(self, "explain")
        self.dismiss()

    def on_unmount(self) -> None:
        self._stop_waiting()
        self._cancel.set()
        self.workers.cancel_group(self, "explain")

    def _stream(self) -> None:
        try:
            self._run_stream()
        except LLMError as exc:
            self._show("AI unavailable", str(exc))
        except Exception as exc:  # noqa: BLE001 - never crash the TUI on this
            self._show("AI error", str(exc))

    def _run_stream(self) -> None:
        available, reason = is_available()
        if not available:
            self._show("AI unavailable", reason)
            return

        self._show("", "")
        self.app.call_from_thread(self._begin_waiting)
        received = False
        generator = stream_explanation(self.results, cancel_event=self._cancel)
        try:
            for chunk in generator:
                received = True
                self.app.call_from_thread(self._append, chunk)
        finally:
            # Closing the generator closes the HTTP response immediately
            # instead of leaving the socket open until GC.
            generator.close()

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
        if text:
            self._stop_waiting()
        self._text = text
        self.query_one("#explain-text", Static).update(text)

    def _begin_waiting(self) -> None:
        # A Pi needs seconds to prefill the prompt. Show that instead of a
        # blank screen, and count up so a stall is recognisable.
        if not self.is_attached:
            return
        self._waiting = True
        self._wait_started = time.monotonic()
        self._timer = self.set_interval(0.5, self._tick)

    def _tick(self) -> None:
        if not self.is_attached or not self._waiting:
            return
        elapsed = int(time.monotonic() - self._wait_started)
        self.query_one("#explain-text", Static).update(
            f"Waiting for the model... {elapsed}s"
        )

    def _stop_waiting(self) -> None:
        self._waiting = False
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _append(self, chunk: str) -> None:
        if not self.is_attached:
            return
        if self._waiting:
            self._stop_waiting()
        self._text += chunk
        self.query_one("#explain-text", Static).update(self._text)
        self.query_one(VerticalScroll).scroll_end(animate=False)


class TimeToolsScreen(Screen):
    """Clock, countdown, Pomodoro, and stopwatch."""

    BINDINGS = [
        ("escape", "dismiss_screen", "Back"),
        ("space", "toggle_timer", "Start/Pause"),
        ("r", "reset_timer", "Reset"),
        ("p", "pomodoro", "Pomodoro"),
        ("c", "countdown", "Countdown"),
        ("s", "stopwatch", "Stopwatch"),
        ("l", "lap", "Lap"),
    ]

    CSS = """
    #clock {
        height: 4;
        content-align: center middle;
        border: ascii $accent;
        margin: 1 0;
    }

    #mode {
        height: 2;
        content-align: center middle;
        text-style: bold;
    }

    #timer {
        height: 3;
        content-align: center middle;
        text-style: bold;
    }

    #help {
        height: 2;
        content-align: center middle;
    }

    #minutes {
        width: 20;
        margin: 0 1;
    }

    #laps {
        height: 1fr;
        border: ascii $accent;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()

        self.mode = "pomodoro"
        self.running = False

        self.work_minutes = 25
        self.break_minutes = 5
        self.is_break = False

        self.remaining_seconds = 25 * 60
        self.deadline = 0.0

        self.elapsed_seconds = 0.0
        self.started_at = 0.0
        self.lap_count = 0

    def compose(self) -> ComposeResult:
        yield Header()

        yield Static("", id="clock")

        yield Static(
            "POMODORO - WORK - PAUSED",
            id="mode",
        )

        yield Static("25:00", id="timer")

        yield Static(
            "Space start/pause | r reset | p Pomodoro | c Countdown | s Stopwatch",
            id="help",
        )

        yield Input(
            value="25",
            placeholder="Minutes",
            id="minutes",
        )

        yield Static(
            "Stopwatch laps will appear here.",
            id="laps",
            markup=False,
        )

        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(0.2, self.refresh_display)
        self.refresh_display()

    def action_dismiss_screen(self) -> None:
        self.dismiss()

    def action_toggle_timer(self) -> None:
        if self.running:
            self.pause_timer()
        else:
            self.start_timer()

    def start_timer(self) -> None:
        if self.mode == "stopwatch":
            self.started_at = time.monotonic()
        else:
            self.deadline = time.monotonic() + self.remaining_seconds

        self.running = True

    def pause_timer(self) -> None:
        if self.mode == "stopwatch":
            self.elapsed_seconds += time.monotonic() - self.started_at
        else:
            self.remaining_seconds = max(
                0,
                int(self.deadline - time.monotonic()),
            )

        self.running = False

    def action_reset_timer(self) -> None:
        self.running = False

        if self.mode == "stopwatch":
            self.elapsed_seconds = 0
            self.started_at = 0
            self.lap_count = 0
            self.query_one("#laps", Static).update(
                "Stopwatch reset. Press l to record laps."
            )
            return

        if self.mode == "countdown":
            minutes = self.read_minutes(default=5)
            self.remaining_seconds = minutes * 60
            return

        self.is_break = False
        self.remaining_seconds = self.work_minutes * 60

    def action_pomodoro(self) -> None:
        self.running = False
        self.mode = "pomodoro"
        self.is_break = False
        self.work_minutes = self.read_minutes(default=25)
        self.remaining_seconds = self.work_minutes * 60

        self.query_one("#minutes", Input).value = str(self.work_minutes)

        self.query_one("#laps", Static).update("Pomodoro uses work and break phases.")

    def action_countdown(self) -> None:
        self.running = False
        self.mode = "countdown"

        minutes = self.read_minutes(default=5)
        self.remaining_seconds = minutes * 60

        self.query_one("#minutes", Input).value = str(minutes)

        self.query_one("#laps", Static).update(
            "Enter minutes, press Enter, then Space to start."
        )

    def action_stopwatch(self) -> None:
        self.running = False
        self.mode = "stopwatch"
        self.elapsed_seconds = 0
        self.started_at = 0
        self.lap_count = 0

        self.query_one("#laps", Static).update("Stopwatch ready. Press Space to start.")

    def action_lap(self) -> None:
        if self.mode != "stopwatch":
            return

        current = self.elapsed_seconds

        if self.running:
            current += time.monotonic() - self.started_at

        self.lap_count += 1

        laps = self.query_one("#laps", Static)
        previous = str(laps.renderable)

        line = f"Lap {self.lap_count}: {self.format_time(current)}"

        if self.lap_count == 1:
            laps.update(line)
        else:
            laps.update(f"{previous}\n{line}")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        minutes = self.read_minutes(default=5)

        if self.mode == "pomodoro":
            self.work_minutes = minutes

            if not self.is_break:
                self.remaining_seconds = minutes * 60

        elif self.mode == "countdown":
            self.remaining_seconds = minutes * 60

        self.running = False

    def read_minutes(self, default: int) -> int:
        try:
            value = int(self.query_one("#minutes", Input).value)

            if value < 1:
                return default

            return min(value, 999)

        except ValueError:
            return default

    @staticmethod
    def format_time(seconds: float) -> str:
        seconds = max(0, int(seconds))

        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)

        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        return f"{minutes:02d}:{seconds:02d}"

    def refresh_display(self) -> None:
        if not self.is_attached:
            return

        now = datetime.now().astimezone()

        self.query_one("#clock", Static).update(
            now.strftime("%A, %d %B %Y\n%H:%M:%S %Z")
        )

        if self.mode == "stopwatch":
            elapsed = self.elapsed_seconds

            if self.running:
                elapsed += time.monotonic() - self.started_at

            timer_text = self.format_time(elapsed)
            mode_text = "STOPWATCH - RUNNING" if self.running else "STOPWATCH - PAUSED"

        else:
            if self.running:
                self.remaining_seconds = max(
                    0,
                    int(self.deadline - time.monotonic()),
                )

            if self.remaining_seconds == 0 and self.running:
                self.running = False

                if self.mode == "pomodoro":
                    self.is_break = not self.is_break

                    if self.is_break:
                        self.remaining_seconds = self.break_minutes * 60
                    else:
                        self.remaining_seconds = self.work_minutes * 60

            timer_text = self.format_time(self.remaining_seconds)

            if self.mode == "pomodoro":
                phase = "BREAK" if self.is_break else "WORK"
                state = "RUNNING" if self.running else "PAUSED"
                mode_text = f"POMODORO - {phase} - {state}"
            else:
                state = "RUNNING" if self.running else "PAUSED"
                mode_text = f"COUNTDOWN - {state}"

        self.query_one("#mode", Static).update(mode_text)
        self.query_one("#timer", Static).update(timer_text)
