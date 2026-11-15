# Snowk CyberDeck

A toolbox of small Linux, networking and security tools, with a terminal UI launcher.

## Requirements

- Python >= 3.10 (Raspberry Pi OS Bookworm ships 3.11)
- `ip` and `ss` from iproute2; `ping` from iputils-ping
- `tailscale` only for the web UI
- `ollama` optional, for AI explanations

## Setup

```sh
./install.sh            # asks once about the optional local LLM
./install.sh --no-llm   # Python deps only
```

Options: `--with-llm` (no prompt), `--no-llm`, `--model NAME`, `--help`.
It creates `.venv`, installs `requirements.txt`, and makes `cyberdeck`
executable. Re-running is safe. On a terminal it shows a 4-step progress
bar; when piped it prints plain step lines instead.

For the LLM it will install Ollama only if you approve: the vendor script is
downloaded to `.tmp/`, its sha256 printed, and it runs as root only after a
second `[y/N]`. Declining (or non-interactive use) skips the LLM entirely.

Manual setup, if you prefer:

```sh
python3 -m venv .venv          # needs Python >= 3.10
./.venv/bin/pip install -r requirements.txt
```

## Run

```sh
./cyberdeck
```

- `↑` / `↓` move the highlight, `Enter` selects.
- `Network Diagnostics` opens a submenu: `Local TUI`, `Port Check`,
  `Start Tailnet Web UI`, `Back`.
- `Port Check` asks for a host and a port, then tries a TCP connection:
  `OPEN`, `CLOSED` (refused), `FILTERED` (no answer), `UNRESOLVED` (DNS).
- `q` or `Quit` exits.

## Layout

```
cyberdeck                    launcher (uses .venv, adds src/ to PYTHONPATH)
install.sh                   setup script (venv + optional LLM)
src/cyberdeck/app.py         main menu
src/cyberdeck/diagnostics.py read-only checks + host validation (no UI, no root)
src/cyberdeck/llm.py         optional Ollama client (stdlib urllib)
src/cyberdeck/ports.py       TCP port check (no UI, no root)
src/cyberdeck/screens.py     submenu, diagnostics, host test, web, AI
src/cyberdeck/web.py         HTML report server (stdlib http.server)
```

## Web UI (tailnet)

`w` on the diagnostics screen starts an HTTP report and shows the URL:

    http://<tailscale-ip>:8080/

Get the address with `tailscale ip -4`. The page is a detailed report (every
check, full detail text, raw command output in collapsible blocks) and
refreshes every 30 s. `Esc` stops the server.

- Bound to the `tailscale0` address, not `0.0.0.0`: only tailnet devices can
  reach it. No login; your Tailscale ACLs are the access control.
- Plain HTTP is fine here because Tailscale already encrypts traffic between
  tailnet nodes. Add `tailscale serve --bg 8080` if you want HTTPS and a
  `*.ts.net` name instead.
- If Tailscale is down it binds `127.0.0.1` and prints an `ssh -L` tunnel hint.
- Port 8080; a second instance reports "address already in use".
  The report is cached for 10 s (one scan serves several refreshes) and
  idle clients are dropped after 5 s.

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
