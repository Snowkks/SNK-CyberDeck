"""Large, keyboard-first clock and countdown screen."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Static

DEFAULT_POMODORO_MINUTES = 25
DEFAULT_COUNTDOWN_MINUTES = 5

DIGIT_GLYPHS = {
    "0": ("#####", "#   #", "#   #", "#   #", "#   #", "#   #", "#####"),
    "1": ("  ## ", " ### ", "  ## ", "  ## ", "  ## ", "  ## ", "#####"),
    "2": ("#####", "    #", "    #", "#####", "#    ", "#    ", "#####"),
    "3": ("#####", "    #", "    #", "#####", "    #", "    #", "#####"),
    "4": ("#   #", "#   #", "#   #", "#####", "    #", "    #", "    #"),
    "5": ("#####", "#    ", "#    ", "#####", "    #", "    #", "#####"),
    "6": ("#####", "#    ", "#    ", "#####", "#   #", "#   #", "#####"),
    "7": ("#####", "    #", "    #", "    #", "    #", "    #", "    #"),
    "8": ("#####", "#   #", "#   #", "#####", "#   #", "#   #", "#####"),
    "9": ("#####", "#   #", "#   #", "#####", "    #", "    #", "#####"),
    ":": ("     ", "  ## ", "  ## ", "     ", "  ## ", "  ## ", "     "),
}


class TimerMode(Enum):
    """Modes available from the timer screen."""

    LOCAL_TIME = "LOCAL TIME"
    POMODORO = "POMODORO"
    COUNTDOWN = "COUNTDOWN"


@dataclass
class TimerState:
    """Mutable state for the active timer."""

    mode: TimerMode = TimerMode.LOCAL_TIME
    running: bool = False
    duration_seconds: int = DEFAULT_POMODORO_MINUTES * 60
    started_at: float | None = None
    work_minutes: int = DEFAULT_POMODORO_MINUTES
    break_minutes: int = DEFAULT_COUNTDOWN_MINUTES
    is_break: bool = False


class TimerScreen(Screen):
    """Show one large local clock or countdown."""

    BINDINGS = [
        Binding("escape", "close", "Back"),
        Binding("space", "toggle", "Start/Pause"),
        Binding("r", "reset", "Reset"),
        Binding("l", "local_time", "Local time"),
        Binding("p", "pomodoro", "Pomodoro"),
        Binding("c", "countdown", "Countdown"),
        Binding("d", "edit_duration", "Edit minutes"),
        Binding("enter", "dismiss_alert", "Dismiss alert"),
    ]

    CSS = """
    Screen {
        align: center middle;
    }

    #panel {
        width: 78;
        height: auto;
        max-height: 90%;
        border: ascii $accent;
        padding: 1 2;
    }

    #panel.alert {
        border: heavy $warning;
    }

    #title, #mode, #date, #instructions {
        width: 100%;
        text-align: center;
    }

    #title {
        color: $text-muted;
    }

    #mode {
        margin-top: 1;
        color: $accent;
        text-style: bold;
    }

    #display {
        width: 100%;
        height: 10;
        margin: 1 0;
        content-align: center middle;
        text-align: center;
        color: $text;
        text-style: bold;
    }

    #date, #instructions {
        color: $text-muted;
    }

    #duration {
        width: 22;
        margin: 1 0 0 0;
    }

    #instructions {
        margin-top: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.state = TimerState()
        self.alert_message = ""
        self.alert_until = 0.0

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="panel"):
            yield Static("SNK TIMER", id="title")
            yield Static("", id="mode")
            yield Static("", id="display")
            yield Static("", id="date")
            yield Input(
                value="25",
                placeholder="Minutes",
                id="duration",
            )
            yield Static(
                "l local time   p pomodoro   c countdown   d edit minutes   space start/pause   r reset",
                id="instructions",
            )
        yield Footer()

    def on_mount(self) -> None:
        # Keep the mode keys active when the screen opens. The duration field
        # becomes available after selecting a countdown mode.
        self.set_duration_visibility(False)
        self.set_focus(None)
        self.set_interval(0.2, self.refresh_screen)
        self.refresh_screen()

    def action_close(self) -> None:
        """Return to the launcher."""

        self.dismiss()

    def action_local_time(self) -> None:
        """Show the current local time."""

        self.clear_alert()
        self.state = TimerState(mode=TimerMode.LOCAL_TIME)
        self.set_duration_input("")
        self.set_duration_visibility(False)
        self.set_focus(None)
        self.refresh_screen()

    def action_pomodoro(self) -> None:
        """Start a fresh Pomodoro work phase."""

        self.clear_alert()
        minutes = self.read_minutes(default=DEFAULT_POMODORO_MINUTES)
        self.state = TimerState(
            mode=TimerMode.POMODORO,
            duration_seconds=minutes * 60,
            work_minutes=minutes,
        )
        self.set_duration_input(str(minutes))
        self.set_duration_visibility(True)
        self.refresh_screen()

    def action_countdown(self) -> None:
        """Prepare a custom countdown."""

        self.clear_alert()
        minutes = self.read_minutes(default=DEFAULT_COUNTDOWN_MINUTES)
        self.state = TimerState(
            mode=TimerMode.COUNTDOWN,
            duration_seconds=minutes * 60,
        )
        self.set_duration_input(str(minutes))
        self.set_duration_visibility(True)
        self.refresh_screen()

    def action_toggle(self) -> None:
        """Start or pause the active countdown."""

        if self.state.mode == TimerMode.LOCAL_TIME:
            return
        if self.state.running:
            self.pause()
        else:
            self.start()
        self.refresh_screen()

    def action_edit_duration(self) -> None:
        """Focus the minutes field for a custom duration."""

        if self.state.mode != TimerMode.LOCAL_TIME:
            self.query_one("#duration", Input).focus()

    def action_dismiss_alert(self) -> None:
        """Dismiss the visible completion alert."""

        if self.alert_message:
            self.clear_alert()

    def action_reset(self) -> None:
        """Reset the active mode without changing its selected mode."""

        self.clear_alert()
        self.state.running = False
        self.state.started_at = None
        if self.state.mode == TimerMode.POMODORO:
            self.state.is_break = False
            self.state.duration_seconds = self.state.work_minutes * 60
        elif self.state.mode == TimerMode.COUNTDOWN:
            self.state.duration_seconds = (
                self.read_minutes(default=DEFAULT_COUNTDOWN_MINUTES) * 60
            )
        self.refresh_screen()

    def start(self) -> None:
        """Start the active countdown."""

        if self.state.duration_seconds > 0:
            self.state.started_at = time.monotonic()
            self.state.running = True

    def pause(self) -> None:
        """Save the remaining time and pause."""

        self.state.duration_seconds = self.remaining_seconds()
        self.state.started_at = None
        self.state.running = False

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        """Apply the minutes field to the selected countdown mode."""

        if self.state.mode == TimerMode.LOCAL_TIME:
            return

        default = (
            DEFAULT_POMODORO_MINUTES
            if self.state.mode == TimerMode.POMODORO
            else DEFAULT_COUNTDOWN_MINUTES
        )
        minutes = self.read_minutes(default=default)
        self.state.running = False
        self.state.started_at = None
        self.state.duration_seconds = minutes * 60
        if self.state.mode == TimerMode.POMODORO:
            self.state.work_minutes = minutes
            self.state.is_break = False
        self.set_focus(None)
        self.refresh_screen()

    def read_minutes(self, default: int = DEFAULT_POMODORO_MINUTES) -> int:
        """Read and clamp the duration input."""

        try:
            value = int(self.query_one("#duration", Input).value)
        except ValueError:
            return default
        return max(1, min(value, 999))

    def set_duration_input(self, value: str) -> None:
        """Update the duration field when changing modes."""

        self.query_one("#duration", Input).value = value

    def set_duration_visibility(self, visible: bool) -> None:
        """Show the minutes field only for countdown modes."""

        duration = self.query_one("#duration", Input)
        duration.styles.display = "block" if visible else "none"
        duration.can_focus = visible

    def remaining_seconds(self) -> int:
        """Return the active countdown's remaining whole seconds."""

        if not self.state.running or self.state.started_at is None:
            return max(0, self.state.duration_seconds)
        elapsed = time.monotonic() - self.state.started_at
        return max(0, int(self.state.duration_seconds - elapsed))

    def refresh_screen(self) -> None:
        """Refresh the large display and handle completed timers."""

        now = datetime.now().astimezone()
        self.query_one("#date", Static).update(now.strftime("%A, %d %B %Y  %Z"))

        if self.state.mode == TimerMode.LOCAL_TIME:
            value = now.strftime("%H:%M:%S")
            mode = TimerMode.LOCAL_TIME.value
        else:
            self.handle_finished_timer()

            if self.alert_is_active():
                self.query_one("#mode", Static).update("TIMER ALERT")
                self.query_one("#display", Static).update(
                    f"\n\n{self.alert_message}\n\nPress Enter"
                )
                return

            value = self.format_time(self.remaining_seconds())
            mode = self.active_timer_label()

        self.query_one("#mode", Static).update(mode)
        self.query_one("#display", Static).update(self.big_time(value))

    def alert_is_active(self) -> bool:
        """Return whether the completion message should remain visible."""

        if not self.alert_message:
            return False
        if time.monotonic() < self.alert_until:
            return True
        self.clear_alert()
        return False

    def show_alert(self, message: str) -> None:
        """Show a short visual and terminal notification."""

        self.alert_message = message
        self.alert_until = time.monotonic() + 3
        self.query_one("#panel").add_class("alert")
        sys.stdout.write("\a")
        sys.stdout.flush()

    def clear_alert(self) -> None:
        """Clear the current notification."""

        self.alert_message = ""
        self.alert_until = 0.0
        self.query_one("#panel").remove_class("alert")

    def active_timer_label(self) -> str:
        """Return the label shown above an active countdown."""

        label = self.state.mode.value
        if self.state.mode == TimerMode.POMODORO:
            phase = "BREAK" if self.state.is_break else "WORK"
            label = f"{label} / {phase}"
        status = "RUNNING" if self.state.running else "PAUSED"
        return f"{label}  ·  {status}"

    def handle_finished_timer(self) -> None:
        """Stop a countdown or advance Pomodoro to its next phase."""

        if not self.state.running or self.remaining_seconds() > 0:
            return

        if self.state.mode == TimerMode.POMODORO:
            self.state.is_break = not self.state.is_break
            phase_minutes = (
                self.state.break_minutes
                if self.state.is_break
                else self.state.work_minutes
            )
            self.state.duration_seconds = phase_minutes * 60
            self.state.started_at = time.monotonic()
            message = "BREAK START" if self.state.is_break else "WORK START"
            self.show_alert(message)
        else:
            self.state.duration_seconds = 0
            self.state.started_at = None
            self.state.running = False
            self.show_alert("TIME'S UP")

    @staticmethod
    def format_time(seconds: int) -> str:
        """Format seconds as MM:SS or HH:MM:SS."""

        seconds = max(0, seconds)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    @staticmethod
    def big_time(value: str) -> str:
        """Render a time string as clear, portable ASCII digits."""

        rows = [""] * 7
        for character in value:
            pattern = DIGIT_GLYPHS.get(character, ("     ",) * 7)
            rows = [f"{line}  {part}" for line, part in zip(rows, pattern)]

        return "\n".join(rows).rstrip()
