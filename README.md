# ShakeChecker

A passive, **read-only** screen-reading overlay for [PokeMMO](https://pokemmo.com/)
on Windows. It watches the game window during wild battles and shows the live
catch probability for every Poké Ball, plus a "missing here" Pokédex panel while
you walk. Named after the shake checks in the catch formula.

> **Strictly read-only.** ShakeChecker only captures pixels from the screen and
> draws an overlay. It **never** sends input, **never** reads process memory,
> **never** injects code, and **never** touches the network or the game client.
> Nothing about it automates play.

![status](https://github.com/ghostpixxel/ShakeChecker/actions/workflows/ci.yml/badge.svg)

**Website & FAQ:** <https://ghostpixxel.github.io/ShakeChecker/> ·
**[Download the latest release](https://github.com/ghostpixxel/ShakeChecker/releases/latest)**

## Features

- **Live catch rates** per ball during single wild encounters, using PokeMMO's
  Gen 3/4 catch formula — recomputed each frame from the enemy's HP %, status and
  turn count read off the screen.
- **Conditional balls** modelled: Quick / Timer / Nest / Net / Dusk (cave & night)
  / Dream (scales with sleep turns) / Repeat (scales with the catch chain).
- **Hordes & doubles** handled: the overlay waits until a single catchable mon
  remains, and reads its status correctly even as a horde narrows to one.
- **Dex "missing here" tracker**: reads your location from the HUD and lists the
  species still missing on that route, per PokeMMO account.
- **Click-through overlay** docked to the game window (hover to interact).
- **Resolution-independent**: works at any window size/aspect; resize mid-battle.

## Requirements

- Windows 10/11
- Python 3.11+
- PokeMMO running in **windowed or borderless** mode (not exclusive fullscreen),
  client language **English**.

## Setup

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"      # runtime + dev tools (pytest, ruff, mypy)
```

## Usage

```powershell
python src\app.py                       # auto: identify the species via OCR
python src\app.py --species Onix        # override the detected species
python src\app.py --species Onix --status slp   # override the status too
python src\app.py --rate 45             # use a raw base catch rate instead
python src\app.py --account MyChar      # pick the dex caught-list account (remembered)
python src\app.py --debug               # verbose turn-counter diagnostics
python src\app.py --image fixtures\x.png  # offline: analyse one PNG, no overlay
python src\app.py --list-windows        # diagnose window detection, then exit
```

Catch-list data is stored per account under `userdata/` (which character is
active is remembered between runs).

## Windows SmartScreen & antivirus

ShakeChecker is **unsigned** (code-signing certificates are an annual cost that
is hard to justify for a free fan tool), so on first launch Windows SmartScreen
shows *"Windows protected your PC"* → click **More info → Run anyway**. A couple
of antivirus engines may also flag it: that is a known **heuristic false
positive** for PyInstaller-bundled Python apps, not actual malware.

If you would rather verify than trust:

- The full source is in this repo. It only captures the screen and draws an
  overlay, it never sends inputs, reads game memory, or touches the network.
- **VirusTotal scan** (`v1.2.0`):
  [4 / 70 detections](https://www.virustotal.com/gui/file/d09275af91f548dafc4feb24c7b0f33c099487274a76fd7054b3166707ee49cc),
  all heuristic/ML (Arctic Wolf, SecureAge, SentinelOne, McAfee). The mainstream
  engines (BitDefender, ESET, Avast, AVG, CrowdStrike, Fortinet, Google) all pass.
  The few hits are the usual false positives for unsigned PyInstaller builds.
- Or build the `.exe` yourself (see [Building a release](#building-a-release-exe)).

## Architecture

A `WAITING → IDLE → BATTLE` state machine drives everything, one step per
`QTimer` tick (no blocking loop, so the overlay stays responsive).

```text
src/
  window_capture.py  find the PokeMMO window, track its rect, grab frames (mss)
  battle_reader.py   detect battle state; read HP %, status, name region, bars (OpenCV)
  battle_log.py      async chat OCR -> "Turn N started!"
  battle_logic.py    PURE loop decisions: turn correction, end-grace, in-battle
  catch_calc.py      PURE catch-probability math + per-ball multipliers
  catch_chain.py     PURE Repeat Ball catch-chain state
  turn_tracker.py    turn + consecutive-sleep counters (menu + chat)
  game_time.py       PURE PokeMMO time/season (UTC-anchored, deterministic)
  location_reader.py HUD location/clock OCR
  name_reader.py     enemy-name OCR + fuzzy match against the species list
  dex_tracker.py     encounter data -> "missing here" ordering
  dex_session.py     location -> dex view, wired to the caught store
  account_store.py   per-account caught lists + active-account config
  overlay.py         PyQt6 click-through catch overlay
  dex_panel.py       PyQt6 click-through dex panel
  app.py             the state machine + I/O wiring (LiveLoop)
calibration.toml     every CV region/threshold, each naming the fixture that justifies it
fixtures/            PNG screenshots + expected.json ground truth (tests run on these)
```

The CV/OCR readers and the math are **pure and injectable** — they take a frame
(or values) plus calibration and return data, with no capture or global state.
That is what lets the whole pipeline be tested against fixture PNGs rather than
the live game. See [CLAUDE.md](CLAUDE.md) for the detailed design, the catch
formula, and the data sources.

## Development

```powershell
ruff check                # lint
ruff format               # format (CI runs --check)
mypy                      # type-check
pytest -q                 # run the test suite (fixture-driven)
```

All four run in CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) on every
push to `main` and every pull request, on a Windows runner.

Development is **fixture-driven**: CV/OCR logic is built and tested against the
PNGs in `fixtures/` (with ground truth in `fixtures/expected.json`), never against
the live game. `rapidocr-onnxruntime` is pinned so OCR results are reproducible.
Refresh the vendored game data with `python scripts/update_data.py`.

## Building a release (.exe)

Package into a standalone Windows app (no Python needed by the user):

```powershell
pip install -e ".[build]"
pyinstaller ShakeChecker.spec
```

This produces `dist/ShakeChecker/` containing `ShakeChecker.exe` plus everything
bundled. Zip that folder and attach it to a **GitHub Release** — users download the
zip, extract, and run `ShakeChecker.exe`. Per-account data is written to
`%APPDATA%/ShakeChecker`.

Note: the exe is unsigned, so on first launch Windows SmartScreen shows
"Windows protected your PC" → **More info → Run anyway**. This is normal for
unsigned indie tools.

## Disclaimer

ShakeChecker is a fan-made, read-only tool and is not affiliated with, endorsed,
or sponsored by PokeMMO. Pokémon and all related names, sprites and trademarks
belong to Nintendo, Game Freak and The Pokémon Company. This is an unofficial
project; all rights to their respective owners.
