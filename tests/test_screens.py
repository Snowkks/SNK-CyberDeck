"""Offline-safe UI tests: no real checks, no real model.

The scan function is replaced with fixed results and Ollama is a local
stub server, so these run identically on a laptop and on the Pi.
"""

import unittest
from unittest import mock

from textual.widgets import DataTable, Static

from cyberdeck import screens
from cyberdeck import llm as llm_module
from cyberdeck.app import CyberDeckApp
from cyberdeck.diagnostics import CheckResult
from cyberdeck.screens import DiagnosticsScreen, ExplainScreen

from tests.stub_ollama import StubOllama

FAKE_RESULTS = [
    CheckResult("Interfaces", True, "1 up", "wlan0 192.168.1.20"),
    CheckResult("Ping gateway", False, "no reply", "192.168.1.1 did not answer."),
]
OFFLINE_URL = "http://127.0.0.1:9"


async def open_diagnostics(pilot, app):
    await pilot.press("enter")
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()
    assert isinstance(app.screen, DiagnosticsScreen), app.screen


class DiagnosticsScreenTests(unittest.IsolatedAsyncioTestCase):
    async def test_results_and_status_text(self):
        with mock.patch.object(screens, "run_all_checks", return_value=FAKE_RESULTS):
            app = CyberDeckApp()
            async with app.run_test(size=(85, 25)) as pilot:
                await open_diagnostics(pilot, app)
                table = app.screen.query_one(DataTable)
                self.assertEqual(table.row_count, 2)
                self.assertEqual(table.get_row_at(0)[1].plain, "OK")
                self.assertEqual(table.get_row_at(1)[1].plain, "FAIL")
                self.assertEqual(table.get_row_at(0)[2], "1 up")

    async def test_detail_pane_follows_cursor(self):
        with mock.patch.object(screens, "run_all_checks", return_value=FAKE_RESULTS):
            app = CyberDeckApp()
            async with app.run_test(size=(85, 25)) as pilot:
                await open_diagnostics(pilot, app)
                pane = app.screen.query_one("#detail-pane", Static)
                self.assertIn("wlan0 192.168.1.20", str(pane.content))
                await pilot.press("down")
                await pilot.pause()
                self.assertIn("192.168.1.1 did not answer.", str(pane.content))

    async def test_explain_is_guarded_while_scanning(self):
        # run_worker is disabled so the scan never finishes.
        with mock.patch.object(DiagnosticsScreen, "run_worker", return_value=None):
            app = CyberDeckApp()
            async with app.run_test(size=(85, 25)) as pilot:
                app.push_screen(DiagnosticsScreen())
                await pilot.pause()
                await pilot.press("e")
                await pilot.pause()
                pane = app.screen.query_one("#detail-pane", Static)
                self.assertIn("Wait for the scan", str(pane.content))
                self.assertNotIsInstance(app.screen, ExplainScreen)


class ExplainScreenTests(unittest.IsolatedAsyncioTestCase):
    async def test_streams_answer_then_returns(self):
        with StubOllama(chunks=("Check ", "the ", "gateway.")) as stub:
            with mock.patch.object(screens, "run_all_checks", return_value=FAKE_RESULTS), \
                 mock.patch.object(llm_module, "OLLAMA_URL", stub.url):
                app = CyberDeckApp()
                async with app.run_test(size=(85, 25)) as pilot:
                    await open_diagnostics(pilot, app)
                    await pilot.press("e")
                    await pilot.pause()
                    self.assertIsInstance(app.screen, ExplainScreen)
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    text = str(app.screen.query_one("#explain-text", Static).content)
                    self.assertEqual(text, "Check the gateway.")
                    await pilot.press("escape")
                    await pilot.pause()
                    self.assertIsInstance(app.screen, DiagnosticsScreen)

    async def test_offline_message_is_actionable(self):
        with mock.patch.object(screens, "run_all_checks", return_value=FAKE_RESULTS), \
             mock.patch.object(llm_module, "OLLAMA_URL", OFFLINE_URL):
            app = CyberDeckApp()
            async with app.run_test(size=(85, 25)) as pilot:
                await open_diagnostics(pilot, app)
                await pilot.press("e")
                await pilot.pause()
                await app.workers.wait_for_complete()
                await pilot.pause()
                text = str(app.screen.query_one("#explain-text", Static).content)
                self.assertIn("LLM offline", text)
                self.assertIn("ollama serve", text)

    async def test_prompt_contains_the_scan_results(self):
        with StubOllama() as stub:
            with mock.patch.object(screens, "run_all_checks", return_value=FAKE_RESULTS), \
                 mock.patch.object(llm_module, "OLLAMA_URL", stub.url):
                app = CyberDeckApp()
                async with app.run_test(size=(85, 25)) as pilot:
                    await open_diagnostics(pilot, app)
                    await pilot.press("e")
                    await pilot.pause()
                    await app.workers.wait_for_complete()
        prompt = stub.generate_requests[0]["prompt"]
        self.assertIn("Interfaces: OK", prompt)
        self.assertIn("Ping gateway: FAIL", prompt)
        self.assertTrue(all(ord(c) < 128 for c in prompt))


if __name__ == "__main__":
    unittest.main()
