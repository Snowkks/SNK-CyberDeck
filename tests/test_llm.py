"""Offline-safe tests for the Ollama client.

HTTP goes to a local stub server, never to a real model.
"""

import unittest
from unittest import mock

from cyberdeck import llm
from cyberdeck.diagnostics import CheckResult

from tests.stub_ollama import StubOllama

OFFLINE_URL = "http://127.0.0.1:9"  # discard port: connection refused


def results() -> list[CheckResult]:
    return [
        CheckResult("Interfaces", True, "1 up", "wlan0 192.168.1.20"),
        CheckResult("Ping gateway", False, "no reply", "192.168.1.1 did not answer."),
    ]


class BuildPromptTests(unittest.TestCase):
    def test_one_line_per_check_with_status(self):
        prompt = llm.build_prompt(results())
        self.assertEqual(prompt.count("\n"), 1)
        self.assertIn("1. Interfaces: OK", prompt)
        self.assertIn("2. Ping gateway: FAIL", prompt)

    def test_long_detail_is_capped(self):
        long_result = CheckResult("Ports", True, "99 listening", ", ".join(f"0.0.0.0:{p}" for p in range(99)))
        prompt = llm.build_prompt([long_result])
        self.assertLessEqual(len(prompt.splitlines()[0]), len("1. Ports: OK - 99 listening ()") + llm.MAX_DETAIL_CHARS + 3)
        self.assertIn("...", prompt)

    def test_short_detail_is_untouched(self):
        prompt = llm.build_prompt([CheckResult("DNS", True, "1 server(s)", "10.0.0.1")])
        self.assertIn("(10.0.0.1)", prompt)

    def test_prompt_is_pure_ascii(self):
        # The Linux console font cannot be trusted with anything else.
        prompt = llm.build_prompt(results())
        self.assertTrue(all(ord(char) < 128 for char in prompt))


class IsAvailableTests(unittest.TestCase):
    def test_model_present(self):
        with StubOllama() as stub, mock.patch.object(llm, "OLLAMA_URL", stub.url):
            self.assertEqual(llm.is_available(), (True, ""))

    def test_model_missing(self):
        with StubOllama(models=("other-model:latest",)) as stub, mock.patch.object(
            llm, "OLLAMA_URL", stub.url
        ):
            available, reason = llm.is_available()
        self.assertFalse(available)
        self.assertIn("not pulled", reason)
        self.assertIn(f"ollama pull {llm.MODEL}", reason)

    def test_service_offline(self):
        with mock.patch.object(llm, "OLLAMA_URL", OFFLINE_URL):
            available, reason = llm.is_available()
        self.assertFalse(available)
        self.assertIn("LLM offline", reason)
        self.assertIn("ollama serve", reason)


class StreamExplanationTests(unittest.TestCase):
    def test_chunks_stream_in_order(self):
        with StubOllama(chunks=("Check ", "the ", "gateway.")) as stub, mock.patch.object(
            llm, "OLLAMA_URL", stub.url
        ):
            text = "".join(llm.stream_explanation(results()))
        self.assertEqual(text, "Check the gateway.")

    def test_request_uses_pi_friendly_options(self):
        with StubOllama() as stub, mock.patch.object(llm, "OLLAMA_URL", stub.url):
            "".join(llm.stream_explanation(results()))
        payload = stub.generate_requests[0]
        self.assertIs(payload["stream"], True)
        self.assertEqual(payload["options"]["temperature"], 0.2)
        self.assertEqual(payload["options"]["num_predict"], 180)
        self.assertEqual(payload["model"], llm.MODEL)
        self.assertIn("ONLY those results", payload["system"])

    def test_http_404_becomes_model_hint(self):
        with StubOllama(generate_status=404) as stub, mock.patch.object(llm, "OLLAMA_URL", stub.url):
            with self.assertRaises(llm.LLMError) as caught:
                list(llm.stream_explanation(results()))
        self.assertIn("not pulled", str(caught.exception))

    def test_service_offline_raises_llmerror(self):
        with mock.patch.object(llm, "OLLAMA_URL", OFFLINE_URL):
            with self.assertRaises(llm.LLMError) as caught:
                list(llm.stream_explanation(results()))
        self.assertIn("LLM offline", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
