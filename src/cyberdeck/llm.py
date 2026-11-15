"""Optional local LLM client for Ollama.

CyberDeck works without it. If nothing is listening on the configured
URL, the UI shows a short hint instead of failing.

Only the standard library is used: urllib talks to Ollama's HTTP API, so
no new dependency and no model code inside the TUI process.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterator

from cyberdeck.diagnostics import CheckResult

OLLAMA_URL = os.environ.get("CYBERDECK_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
MODEL = os.environ.get("CYBERDECK_LLM_MODEL", "qwen2.5:1.5b")

# Fast fail when the service is down; a long read timeout is fine because
# a slow token stream on a Pi is not a hung socket.
CONNECT_TIMEOUT = 2
STREAM_TIMEOUT = 120

START_HINT = "start: ollama serve"

SYSTEM_PROMPT = (
    "You are a network troubleshooting helper on a small Linux machine. "
    "You are given the results of automated checks. Using ONLY those results, "
    "explain in at most 3 short sentences what is most likely wrong. Then give "
    "at most one command the user could run to investigate further. If "
    "everything passed, say what that means. Do not invent values or commands "
    "that are not justified by the results. Keep the answer under 80 words."
)


class LLMError(Exception):
    """A problem talking to the model, already phrased for the user."""


MAX_DETAIL_CHARS = 160


def build_prompt(results: list[CheckResult]) -> str:
    """One compact line per check - the Pi has to prefill all of this.

    Details are capped: the listening-ports line alone can be 1200+ chars,
    which would cost ~20s of prefill on a Pi 5 at ~30 tok/s.
    """
    lines = []
    for index, result in enumerate(results, start=1):
        status = "OK" if result.ok else "FAIL"
        detail = result.detail
        if len(detail) > MAX_DETAIL_CHARS:
            detail = detail[: MAX_DETAIL_CHARS - 3] + "..."
        lines.append(f"{index}. {result.name}: {status} - {result.summary} ({detail})")
    return "\n".join(lines)


def _request(path: str, payload: dict | None = None, timeout: int = STREAM_TIMEOUT):
    """Open a connection to Ollama, translating transport errors."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        OLLAMA_URL + path,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise LLMError(
                f"model '{MODEL}' not pulled - run: ollama pull {MODEL}"
            ) from exc
        raise LLMError(f"ollama returned HTTP {exc.code}") from exc
    except urllib.error.URLError:
        raise LLMError(f"LLM offline ({OLLAMA_URL}) - {START_HINT}") from None
    except OSError as exc:
        raise LLMError(str(exc)) from exc


def _default_tag() -> str:
    return MODEL if ":" in MODEL else f"{MODEL}:latest"


def is_available() -> tuple[bool, str]:
    """Check the service and whether the model is pulled.

    Returns (True, "") or (False, reason) - the reason is shown to the user.
    """
    try:
        with _request("/api/tags", timeout=CONNECT_TIMEOUT) as response:
            payload = json.loads(response.read() or b"{}")
    except LLMError as exc:
        return False, str(exc)
    except (json.JSONDecodeError, OSError) as exc:
        return False, f"unexpected reply from {OLLAMA_URL}: {exc}"

    names = [model.get("name", "") for model in payload.get("models", [])]
    if _default_tag() not in names:
        return False, f"model '{MODEL}' not pulled - run: ollama pull {MODEL}"
    return True, ""


def stream_explanation(results: list[CheckResult]) -> Iterator[str]:
    """Yield response text as Ollama produces it."""
    payload = {
        "model": MODEL,
        "system": SYSTEM_PROMPT,
        "prompt": build_prompt(results),
        "stream": True,
        # Low temperature: summarise the given facts, do not be creative.
        # num_predict caps the work: every token costs time on a Pi.
        "options": {"temperature": 0.2, "num_predict": 180},
    }
    with _request("/api/generate", payload) as response:
        try:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("error"):
                    raise LLMError(str(event["error"]))
                chunk = event.get("response", "")
                if chunk:
                    yield chunk
                if event.get("done"):
                    break
        except OSError as exc:
            raise LLMError(f"connection lost: {exc}") from exc
