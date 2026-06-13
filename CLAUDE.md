# ShakeChecker

Passive screen-reading overlay for PokeMMO (Windows). Named after the shake checks in the catch formula. Shows live catch probabilities per ball during wild battles, and (later) missing Pokédex entries per route. **Strictly read-only: the tool captures pixels and displays an overlay. It never sends input, never reads process memory, never modifies the game client.**

## Hard constraints

- Windows 10/11 only. Python 3.11+.
- No input automation of any kind (no key presses, no mouse clicks into the game). This is non-negotiable.
- No memory reading, no injection, no network interception. Screen capture only.
- PokeMMO runs in windowed or borderless mode (not exclusive fullscreen).
- All screen regions are defined **relative to the game window's client area** (percentages), never absolute pixels. The game window can be any size and can move at runtime.
- Set process DPI awareness (`SetProcessDpiAwareness`) at startup before any coordinate work.

## Stack

- `mss` — screen capture (capture only the PokeMMO client area region)
- `opencv-python` — HP bar measurement, template matching (status icons, battle detection)
- `rapidocr-onnxruntime` — OCR for Pokémon name and location banner (fallback: pytesseract)
- `PyQt6` — overlay window (frameless, always-on-top, click-through via pywin32 extended window styles)
- `pywin32` — window enumeration, client rect, click-through
- `pydantic` + JSON/TOML config for calibration data and ball/species tables

## Architecture

```text
src/
  window_capture.py   # find PokeMMO hwnd, track client rect, grab frames
  battle_reader.py    # detect battle state, read name (OCR), HP%, status icon, turn counter
  catch_calc.py       # pure functions: catch probability per ball
  overlay.py          # PyQt6 click-through overlay, docks to game window
  data/
    catch_rates.json  # species -> base catch rate (Gen 5 data)
    balls.json        # ball -> bonus rules
  app.py              # state machine + main loop
fixtures/             # PNG screenshots for tests (see Testing)
tests/
```

### State machine (app.py)

```text
WAITING  -> poll every 2s for a visible window titled "PokeMMO". Found -> IDLE
IDLE     -> ~2 fps: check location-banner region (future dex feature);
            template-match enemy HP bar frame. Match -> BATTLE
BATTLE   -> ~5 fps: OCR enemy name once (then cache for the battle),
            measure HP% from bar pixel width + color mask,
            template-match status icon, increment turn counter on player action,
            recompute probabilities, update overlay.
            HP bar template gone for >1s -> IDLE (reset turn counter, cache)
```

Re-read the game window rect every ~1s; reposition overlay accordingly. Window minimized/closed -> hide overlay, go back to WAITING.

### Battle scope (v1)

- Single wild encounters only.
- Detect and **ignore** horde battles (multiple HP bars) and trainer battles (hide overlay — nothing is catchable).

## Catch probability (catch_calc.py)

PokeMMO uses the **Gen 3/4 formula** (verified against the open-source PokeMMO Hub implementation, `src/hooks/useCatchRate.jsx` in <https://github.com/PokeMMO-Tools/pokemmo-hub> — port it 1:1). With `p = currentHP / maxHP` (read as fraction from the HP bar; max HP cancels out):

```text
x = ((3 - 2p) / 3) * base_catch_rate * ball_rate * status_rate
if x > 255: P(catch) = 100%
else:
    y = 65536 / (255 / x) ** 0.25
    P(catch) = (y / 65536) ** 4      # four shake checks
```

Reference check: Bulbasaur (rate 45), 100% HP, Sleep, Poke Ball -> x = 30, P = 11.8% (matches pokemmohub.com).

- `status_rate` (PokeMMO custom, NOT mainline): Sleep ×2, Freeze ×2, Paralysis ×1.5, none ×1. **Open question:** Poison/Burn are absent from the Hub list — verify in-game/forum whether they count ×1.5 or ×1; keep configurable.
- `ball_rate` (PokeMMO custom values from Hub source, flat — all in `balls.json`):
  - Poke ×1, Great ×1.5, Ultra ×2, Heal ×1.25, Luxury ×2, Net ×3.5, Nest ×4, Dusk ×2.5, Quick ×5, Timer ×4, Repeat ×2.5, Dream ×4
  - The Hub models all balls as **flat multipliers** (no turn scaling for Timer, no turn-1 condition for Quick, no type condition for Net). **Open question:** whether PokeMMO applies conditions in-game or genuinely flattened these — verify via forum/wiki before shipping conditional logic. v1: show flat values, identical to Hub.
- No level bonus, no critical capture (not present in the Hub implementation).

`catch_calc.py` must be pure (no I/O) and fully unit-tested, including the Bulbasaur reference case above.

## Data sources

- **Base catch rates:** `src/data/catchRates.json` from <https://github.com/PokeMMO-Tools/pokemmo-hub> (611 entries, `{id, rate}`; dex id = National Dex order). Vendor a copy into `src/data/`.
- **Species names/ids:** `monster.json` from the same repo (originates from the official PokeMMO client dump) or <https://github.com/PokeMMOZone/PokeMMO-Data> (`pokemon-data.json`).
- **Encounter/location data (milestone 4):** `location-data.json` / `location-types.json` from <https://github.com/PokeMMOZone/PokeMMO-Data> — PokeMMO-specific spawns; do NOT use vanilla PokeAPI encounter tables.
- **Update path:** PokeMMO client -> Settings -> Utilities -> Dump Moddable Resources -> Pokedex Data. Write a small `scripts/update_data.py` that refreshes the vendored JSONs from the repos.

## Reading details (battle_reader.py)

- **HP%:** crop the enemy HP bar region, color-mask green/yellow/red hues, measure filled width / total width. Must work for all three colors.
- **Status:** PokeMMO status badges share the same shape and differ by color. Detect badge **presence** in the status region (pixel activity vs. empty baseline), then classify by dominant hue: yellow=PAR, purple=PSN, red=BRN, blue=FRZ, SLP per its badge color from fixtures. Calibrate hue ranges from the existing SLP/PAR/PSN fixtures; BRN/FRZ hue ranges are extrapolated (no fixtures needed — add a synthetic recolored test case each).
- **Name OCR:** crop name region, upscale 2-3x, OCR, then fuzzy-match (rapidfuzz) against the known species list — never trust raw OCR output. Cache per battle. **Client language is English; all name matching runs against English names from `species_core.json`. Do not build language abstraction in v1** (other languages would later just be an alternative name list keyed by the same ids).
- **Turn counter (optional, only needed if conditional ball logic gets confirmed):** increment when the move-selection UI disappears (player committed an action). Reset on battle start/end.
- Calibration values (region percentages, color ranges, match thresholds) live in a single `calibration.toml`, not scattered constants.

## Overlay (overlay.py)

Frameless, translucent, click-through, always-on-top. Docks to the top-right inside the game window. Layout:

```text
 Traumato          HP 23%   PAR
 ─────────────────────────────
 Pokéball   31%
 Superball  42%
 Hyperball  51%
 Timerball  68%
 Duskball   58%
```

Monospace font, dark background ~70% opacity, probability column right-aligned. Color hint: <30% red, 30-60% yellow, >60% green. Hidden outside battles.

## Testing

Development is fixture-driven: all CV/OCR logic is developed and tested against PNG screenshots in `fixtures/` — never against the live game. Write pytest tests that assert expected HP%, status, and name per fixture (expectations in `fixtures/expected.json`).

Fixture filename convention: `battle_<hp>_<status>_<context>_<resolution>.png`, e.g. `battle_full_none_grass_1720x1440.png`.

## Milestones

1. **Core:** window detection + capture + HP-bar reader + `catch_calc` with ball table. Output to console. Fixture tests green. Validation against PokeMMO Hub done.
2. **Reading:** name OCR + fuzzy matching, status detection, turn counter, battle state machine.
3. **Overlay:** PyQt6 click-through overlay, docking/following, color coding.
4. **Dex tracker:** read current location from the permanent HUD (top-left: location name, money, in-game day/time) via OCR — visible at all times, no banner timing needed. Lookup against `locations_index.json`, personal caught-list, "missing here" panel. **Game time is deterministic, do not OCR it as primary source:** the server clock is anchored to UTC; one PokeMMO day = 6 real hours starting at 00:00/06:00/12:00/18:00 UTC. Formula: `game_minutes = (minutes_since_utc_midnight % 360) * 4`. Periods (game time): Morning 04:00-10:59, Day 11:00-20:59, Night 21:00-03:59. Always compute from UTC, never local time (DST-safe). Verify the anchor once against the HUD clock at first run; optionally cross-check via HUD OCR thereafter. Seasons change monthly. Time period and season matter for spawn filtering.

## Conventions

- Type hints everywhere, `ruff` + `ruff format`.
- No global state except the app state machine; readers and calc are pure/injectable.
- Every magic number (region, threshold, bonus) lives in config files, with a comment naming the fixture that justifies it.
