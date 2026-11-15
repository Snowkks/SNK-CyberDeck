# Snowk CyberDeck

A toolbox of small Linux, networking and security tools, with a terminal UI launcher.

## Setup

```sh
python3 -m venv .venv          # needs Python >= 3.10
./.venv/bin/pip install -r requirements.txt
```

## Run

```sh
./cyberdeck
```

- `↑` / `↓` move the highlight, `Enter` selects.
- `Network Diagnostics` shows a read-only network snapshot.
  `r` rescan, `e` explain with AI, `Esc` back (or the on-screen buttons).
  The bottom pane shows the full detail of the highlighted row.
- `q` or `Quit` exits.

## Layout

```
cyberdeck                    launcher (uses .venv, adds src/ to PYTHONPATH)
src/cyberdeck/app.py         main menu
src/cyberdeck/diagnostics.py read-only checks (no UI, no root)
src/cyberdeck/llm.py         optional Ollama client (stdlib urllib)
src/cyberdeck/screens.py     diagnostics, AI explain, placeholder screens
```

## AI explanations (optional)

`e` on the diagnostics screen sends the check results to a local model and
streams back a short explanation. CyberDeck works fine without it: if nothing
is listening, the screen says `LLM offline` and how to fix it.

Setup on the Pi (needs sudo and ~2 GB disk; not done by the app):

```sh
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:1.5b
ollama run qwen2.5:1.5b --verbose "hi"    # measure tokens/sec
```

Configuration via environment variables:

| variable | default |
|---|---|
| `CYBERDECK_OLLAMA_URL` | `http://127.0.0.1:11434` |
| `CYBERDECK_LLM_MODEL` | `qwen2.5:1.5b` |

Notes:

- The prompt is capped (~200 chars per check) because the Pi must prefill the
  whole thing before the first token appears; a full port list would cost ~20 s.
- Answers are advisory text only. Suggested commands are never executed.
- A 1.5B model is often fluent and sometimes wrong; the checks stay the source
  of truth.

## Tests

```sh
./.venv/bin/python -m unittest discover -s tests -t .
```

34 tests, ~10 s, offline-safe: command output is mocked and Ollama is a local
stub server (`tests/stub_ollama.py`), so results do not depend on this
machine's network, on `ping`/`ip` being installed, or on a model being
present. Run them before trusting a change on the Pi.

## Display notes (Pi 5, 7" 1024x600 panel)

The UI is written for the Linux console, not a desktop terminal:

- ASCII only (`OK` / `FAIL`, `+--+` borders) — console bitmap fonts do not
  reliably contain `✓`/`✗` or rounded corners.
- The `ansi-dark` theme uses only the 16 ANSI colours a console can show.
- Layout is designed for 80x24 and grows from there. Console font choice sets
  the character grid: 8x16 gives 128x37, 12x24 gives about 85x25, 16x32 is too
  cramped at 64x18.
- Touchscreens feed `/dev/input`, not the text console. Taps only reach the app
  if `gpm` is installed and running; otherwise use the keyboard.
- Over SSH, use `ssh -t` (the app needs a real terminal). Without `-t` it exits
  with "no terminal".
