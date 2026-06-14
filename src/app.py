"""ShakeChecker console app: WAITING -> IDLE -> BATTLE state machine.

Watches the PokeMMO window and prints per-ball catch probabilities for the
current wild battle. HP%, HP colour and enemy status are read from the screen;
the species (base catch rate) is given on the command line:

    python src/app.py --species Onix       # status auto-detected from screen
    python src/app.py --species Onix --status slp   # override the detection
    python src/app.py --rate 45            # raw base catch rate instead
    python src/app.py --list-windows       # diagnose window detection
"""

from __future__ import annotations

import argparse
import enum
import io
import json
import sys
import time
from pathlib import Path

from battle_log import read_battle_text, read_turn_number
from battle_reader import (
    BattleState,
    Calibration,
    Status,
    is_battle_ui_present,
    load_calibration,
    read_battle,
)
from catch_calc import BattleContext, ball_multiplier, catch_probability
from hp_settler import HpSettler
from name_reader import NameReader
from turn_tracker import TurnTracker
from window_capture import (
    WINDOW_TITLE,
    WindowCapture,
    find_pokemmo_hwnd,
    fold_confusables,
    get_client_rect,
    is_window_alive,
    iter_visible_windows,
    set_dpi_awareness,
    title_matches,
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "src" / "data"
SPECIES_PATH = DATA / "species_core.json"

WAITING_POLL_S = 2.0
IDLE_FRAME_S = 0.5  # ~2 fps
BATTLE_FRAME_S = 0.2  # ~5 fps
# Battle membership follows the battle command panel (see is_battle_ui_present),
# which is stable through intro/animations. Only end the battle once that panel
# has been gone continuously for this long (covers fade-out transitions).
BATTLE_END_GRACE_S = 1.5
# Chat OCR is comparatively expensive; turns change slowly, so poll it at most
# this often rather than every battle frame.
CHAT_OCR_INTERVAL_S = 1.0
# The in-viewport text box (command menu + catch banner) is OCR'd at most this
# often. Fast enough to catch the menu each turn and the ~1.5-2s catch banner.
BATTLE_TEXT_OCR_INTERVAL_S = 0.3


class AppState(enum.Enum):
    WAITING = "waiting"
    IDLE = "idle"
    BATTLE = "battle"


def load_balls() -> list[dict]:
    return json.loads((DATA / "balls.json").read_text("utf-8"))["balls"]


def load_status_rates() -> dict[str, float]:
    return json.loads((DATA / "status_rates.json").read_text("utf-8"))["rates"]


def lookup_species(name: str) -> dict:
    entries = json.loads(SPECIES_PATH.read_text("utf-8"))
    for e in entries:
        if e["name"].lower() == name.lower():
            return e
    raise SystemExit(f"unknown species: {name!r}")


def battle_context(
    enemy: dict, turns_completed: int = 0, turns_asleep: int = 0, dusk_active: bool = False
) -> BattleContext:
    """Build the conditional-ball context from a resolved enemy dict.

    turns_completed/turns_asleep default to 0 until the turn counter lands, so
    Quick Ball reads x5, Timer Ball x1 and Dream Ball x1 — all correct for the
    first turn with no accumulated sleep."""
    return BattleContext(
        turns_completed=turns_completed,
        turns_asleep=turns_asleep,
        enemy_types=tuple(enemy.get("types") or ()),
        enemy_level=enemy.get("level") or 1,
        dusk_active=dusk_active,
    )


def format_line(name: str, hp_pct: float, status: str, probs: list[tuple[str, float]]) -> str:
    balls = "  ".join(f"{ball} {100 * p:5.1f}%" for ball, p in probs)
    return f"{name:12.12s} HP {hp_pct:5.1f}% [{status}]  {balls}"


def ball_probs(
    hp_pct: float, base_rate: int, status_rate: float, balls: list[dict], ctx: BattleContext
) -> list[tuple[str, float]]:
    return [
        (
            b["name"],
            catch_probability(hp_pct / 100.0, base_rate, ball_multiplier(b, ctx), status_rate),
        )
        for b in balls
    ]


def resolve_enemy(
    species_override: dict | None,
    name_reader: NameReader | None,
    frame_bgr,
    bar,
) -> dict | None:
    """Enemy dict ({name, catch_rate, types, level}) for a bar: the override if
    given, else OCR. None when the name can't be read."""
    if species_override is not None:
        return species_override
    assert name_reader is not None
    return name_reader.read(frame_bgr, bar)


def analyze_image(
    image_path: str,
    species_override: dict | None,
    status_override: str | None,
    cal: Calibration,
) -> None:
    """Offline mode: run the full pipeline on a single PNG and print the result.

    Lets you verify reader + probabilities + output format without the live
    game (same code path the live loop uses)."""
    import cv2

    frame = cv2.imread(image_path)
    if frame is None:
        raise SystemExit(f"cannot read image: {image_path!r}")
    status_rates = load_status_rates()
    balls = load_balls()
    name_reader = None if species_override else NameReader(cal.name, SPECIES_PATH)
    reading = read_battle(frame, cal)
    print(f"{image_path}")
    print(f"  state: {reading.state.value}  (bars detected: {len(reading.bars)})")
    if reading.state is BattleState.MULTI:
        print("  -> horde/double battle: ignored in v1 (overlay would stay hidden)")
    for i, bar in enumerate(reading.bars):
        status = status_override or bar.status.value
        enemy = resolve_enemy(species_override, name_reader, frame, bar)
        label = enemy["name"] if enemy else "?"
        tag = f"bar {i}: " if len(reading.bars) > 1 else ""
        print(
            f"  {tag}{label}  HP {bar.hp_pct:.1f}% ({bar.color.value})  status: {bar.status.value}"
        )
        if reading.state is BattleState.SINGLE and enemy is not None:
            turn = read_turn_number(frame, cal.chat)
            turns_completed = turn - 1 if turn else 0
            ctx = battle_context(enemy, turns_completed=turns_completed)
            probs = ball_probs(bar.hp_pct, enemy["catch_rate"], status_rates[status], balls, ctx)
            turn_note = f"[turn {turn}] " if turn else "[turn ?] "
            print("  " + turn_note + format_line(label, bar.hp_pct, status, probs))


def list_windows() -> None:
    """Diagnostic: print every visible top-level window and mark PokeMMO
    matches, so window-detection problems can be seen directly."""
    set_dpi_awareness()
    windows = iter_visible_windows()
    matches = 0
    print(
        f"{len(windows)} visible top-level windows (looking for titles starting with "
        f"{WINDOW_TITLE!r}):\n"
    )
    for hwnd, title in windows:
        is_match = title_matches(title)
        rect = get_client_rect(hwnd)
        size = (
            f"{rect.width}x{rect.height} @ ({rect.left},{rect.top})" if rect else "no client rect"
        )
        mark = " <-- MATCH" if is_match else ""
        if is_match:
            matches += 1
        print(f"  hwnd={hwnd:>10}  {size:28s}  {title!r}{mark}")
        folded = fold_confusables(title)
        if is_match and folded != title:
            cps = " ".join(f"U+{ord(c):04X}" for c in title)
            print(f"             title uses non-ASCII homoglyphs; folds to {folded!r}")
            print(f"             codepoints: [{cps}]")
    picked = find_pokemmo_hwnd()
    print(f"\n{matches} title match(es). find_pokemmo_hwnd() -> {picked}")
    if picked is not None:
        print(f"  selected client rect: {get_client_rect(picked)}")


def run(species_override: dict | None, status_override: str | None, cal: Calibration) -> None:
    balls = load_balls()
    status_rates = load_status_rates()
    name_reader = None if species_override else NameReader(cal.name, SPECIES_PATH)
    capture = WindowCapture()
    state = AppState.WAITING
    hwnd: int | None = None
    last_seen_battle = 0.0
    last_chat_ocr = 0.0
    last_battle_text_ocr = 0.0
    last_line = ""
    cached: dict | None = None  # enemy for the current battle
    turns = TurnTracker()
    hp = HpSettler()
    caught_handled = False  # printed the catch message this battle

    species_src = f"override {species_override['name']}" if species_override else "OCR from screen"
    status_src = f"override {status_override}" if status_override else "detected from screen"
    print(f"species: {species_src}, status: {status_src}")
    print("waiting for PokeMMO window...")

    while True:
        if state is AppState.WAITING:
            hwnd = find_pokemmo_hwnd()
            if hwnd is None:
                time.sleep(WAITING_POLL_S)
                continue
            print("PokeMMO window found")
            state = AppState.IDLE

        assert hwnd is not None
        rect = get_client_rect(hwnd)
        if rect is None:
            if not is_window_alive(hwnd):
                print("window lost, waiting...")
                state = AppState.WAITING
                hwnd = None
            time.sleep(WAITING_POLL_S)
            continue

        frame = capture.grab(rect)
        now = time.monotonic()

        reading = read_battle(frame, cal)
        has_bar = reading.state in (BattleState.SINGLE, BattleState.MULTI)
        # Enter a battle only when an enemy HP bar is actually detected (the
        # login screen / menus have a dark bottom panel too, so the panel alone
        # is not enough). Once in battle, the panel keeps us in through intro and
        # attack animations when the bar momentarily vanishes.
        panel = is_battle_ui_present(frame, cal.battle_ui)

        if has_bar or (state is AppState.BATTLE and panel):
            last_seen_battle = now
            if state is not AppState.BATTLE:
                state = AppState.BATTLE
                cached = None  # new battle: re-identify the species
                last_line = ""
                turns.reset()
                hp.reset()
                last_chat_ocr = 0.0
                last_battle_text_ocr = 0.0
                caught_handled = False
                print("battle detected")

            asleep = reading.state is BattleState.SINGLE and reading.bars[0].status is Status.SLP

            # poll the chat (throttled) for the EXACT turn number when visible.
            # The catch is NOT read here: the chat log lags ~1s and at the catch
            # moment still shows the previous battle's catch line.
            if now - last_chat_ocr >= CHAT_OCR_INTERVAL_S:
                turns.observe(read_turn_number(frame, cal.chat), asleep)
                last_chat_ocr = now

            # One OCR of the in-viewport text box (throttled) drives both the
            # chat-independent turn counter (command menu reappears each turn) and
            # catch detection ("Gotcha! / X was caught!"). Both belong to the
            # current frame; see [battle_text] in calibration.toml.
            if now - last_battle_text_ocr >= BATTLE_TEXT_OCR_INTERVAL_S:
                last_battle_text_ocr = now
                bt = read_battle_text(frame, cal.battle_text)
                turns.observe_menu(bt.menu_present)
                if bt.caught and not caught_handled and cached is not None:
                    print(f"caught {cached['name']}!")
                    caught_handled = True
                    last_line = ""

            if caught_handled:
                pass  # enemy caught: stop updating; battle ends when the UI clears
            elif reading.state is BattleState.SINGLE:
                bar = reading.bars[0]
                hp_pct = hp.update(bar.hp_pct)  # wait for the bar to settle
                status = status_override or bar.status.value
                if species_override is not None:
                    cached = species_override
                elif cached is None:
                    assert name_reader is not None
                    sp = name_reader.read(frame, bar)
                    if sp is not None:
                        cached = sp
                        print(f"identified: {sp['name']} (catch rate {sp['catch_rate']})")

                turn_note = f"turn {turns.turns_completed + 1}"
                if turns.turns_asleep:
                    turn_note += f", asleep {turns.turns_asleep}"
                if cached is None:
                    line = f"{'?':12.12s} HP {hp_pct:5.1f}% [{status}]  ({turn_note})"
                else:
                    ctx = battle_context(
                        cached,
                        turns_completed=turns.turns_completed,
                        turns_asleep=turns.turns_asleep,
                    )
                    probs = ball_probs(
                        hp_pct, cached["catch_rate"], status_rates[status], balls, ctx
                    )
                    line = f"[{turn_note}] " + format_line(cached["name"], hp_pct, status, probs)
                if line != last_line:
                    print(line)
                    last_line = line
            elif reading.state is BattleState.MULTI and last_line != "multi":
                print("multiple enemy bars (horde/double): ignored in v1")
                last_line = "multi"
            # NO_BATTLE while the overlay is up = intro/animation: keep last line
        elif state is AppState.BATTLE and now - last_seen_battle > BATTLE_END_GRACE_S:
            state = AppState.IDLE
            last_line = ""
            cached = None
            # after a catch we already printed "caught X!"; don't also say ended
            if not caught_handled:
                print("battle ended")
            caught_handled = False

        time.sleep(BATTLE_FRAME_S if state is AppState.BATTLE else IDLE_FRAME_S)


def main() -> None:
    # Ball names contain non-ASCII (Poké Ball); force UTF-8 on the Windows console.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="ShakeChecker console output")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--species", help="override the auto-detected species, e.g. Onix")
    group.add_argument("--rate", type=int, help="override with a raw base catch rate")
    parser.add_argument(
        "--status",
        default=None,
        choices=sorted(load_status_rates()),
        help="override the auto-detected enemy status (default: read from screen)",
    )
    parser.add_argument(
        "--image",
        help="offline mode: analyze a single PNG (e.g. a fixture) instead of the live window",
    )
    parser.add_argument(
        "--list-windows",
        action="store_true",
        help="diagnostic: list visible windows and PokeMMO matches, then exit",
    )
    args = parser.parse_args()

    if args.list_windows:
        list_windows()
        return

    # species is read from the screen by default; --species/--rate override it
    species_override: dict | None = None
    if args.species is not None:
        species_override = lookup_species(args.species)
    elif args.rate is not None:
        species_override = {"name": f"rate {args.rate}", "catch_rate": args.rate, "types": []}

    cal = load_calibration(ROOT / "calibration.toml")

    if args.image:
        analyze_image(args.image, species_override, args.status, cal)
        return

    set_dpi_awareness()
    try:
        run(species_override, args.status, cal)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
