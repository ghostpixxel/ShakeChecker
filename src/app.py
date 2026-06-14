"""ShakeChecker: WAITING -> IDLE -> BATTLE state machine driving the overlay.

Watches the PokeMMO window and, during a wild battle, shows per-ball catch
probabilities in a click-through overlay docked to the game window (and mirrors
them to the console as a debug log). Species, HP%, status and turn are read from
the screen; everything can be overridden from the command line:

    python src/app.py                      # auto: identify species via OCR
    python src/app.py --species Onix        # override the detected species
    python src/app.py --species Onix --status slp   # override the detection too
    python src/app.py --rate 45             # raw base catch rate instead
    python src/app.py --image fixtures/x.png  # offline: analyse one PNG (no overlay)
    python src/app.py --list-windows        # diagnose window detection
"""

from __future__ import annotations

import argparse
import enum
import io
import json
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from battle_log import read_turn_number
from battle_reader import (
    BattleState,
    BattleTextReader,
    Calibration,
    Status,
    is_battle_ui_present,
    is_trainer_battle,
    load_calibration,
    read_battle,
)
from catch_calc import BattleContext, ball_multiplier, catch_probability
from hp_settler import HpSettler
from location_reader import is_cave_location, read_location
from name_reader import NameReader
from overlay import Overlay, scale_for_window
from status_settler import StatusSettler
from turn_tracker import TurnTracker
from window_capture import (
    WINDOW_TITLE,
    WindowCapture,
    find_pokemmo_hwnd,
    fold_confusables,
    get_client_rect,
    get_window_rect,
    is_window_alive,
    iter_visible_windows,
    set_dpi_awareness,
    title_matches,
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "src" / "data"
SPECIES_PATH = DATA / "species_core.json"
TEMPLATES_DIR = DATA / "templates"

WAITING_POLL_S = 2.0
IDLE_FRAME_S = 0.5  # ~2 fps
BATTLE_FRAME_S = 0.2  # ~5 fps
# Battle membership follows the battle command panel (see is_battle_ui_present),
# which is stable through intro/animations. Only end the battle once that panel
# has been gone continuously for this long (covers fade-out transitions).
BATTLE_END_GRACE_S = 1.5
# Chat OCR gives the EXACT turn the instant "Turn N started!" prints, so poll it
# briskly — the overlay's turn should update within a fraction of a second of the
# new turn, not a full second later. (No over-count risk: the chat is exact.)
CHAT_OCR_INTERVAL_S = 0.4
# When the chat keeps reading nothing (minimized / wrong tab) back off to this so
# the wasted OCR doesn't slow the loop; the menu fallback then drives the turns.
CHAT_OCR_IDLE_S = 2.0


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
            dusk = is_cave_location(read_location(frame, cal.location))
            ctx = battle_context(enemy, turns_completed=turns_completed, dusk_active=dusk)
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


class LiveLoop:
    """One poll step per QTimer tick: capture -> read -> update overlay + console.

    Driven by a QTimer (not a blocking while-loop) so the Qt event loop keeps
    running between steps and the overlay's animated sprite plays. State that the
    old loop kept in locals lives on the instance. The console output is retained
    as a debug log alongside the overlay.
    """

    def __init__(
        self,
        species_override: dict | None,
        status_override: str | None,
        cal: Calibration,
        overlay: Overlay,
        debug: bool = False,
    ) -> None:
        self.species_override = species_override
        self.status_override = status_override
        self.cal = cal
        self.overlay = overlay
        self.debug = debug
        self._dbg_menu = False  # last logged command-menu state
        self.balls = load_balls()
        self.status_rates = load_status_rates()
        self.name_reader = None if species_override else NameReader(cal.name, SPECIES_PATH)
        self.battle_text = BattleTextReader(cal.battle_text, TEMPLATES_DIR)
        self.capture = WindowCapture()

        self.state = AppState.WAITING
        self.hwnd: int | None = None
        self.last_seen_battle = 0.0
        self.last_chat_ocr = 0.0
        self.last_line = ""
        self.cached: dict | None = None  # enemy for the current battle
        self.turns = TurnTracker()
        self.hp = HpSettler()
        self.status = StatusSettler()
        self.caught_handled = False
        self.dusk_active = False  # cave/night -> Dusk Ball boost
        self._loc_read = False  # location OCR'd this battle yet
        self._chat_misses = 0  # consecutive chat reads with no turn (chat hidden?)
        self._is_trainer = False  # trainer battle -> overlay hidden
        self._trainer_decided = False  # trainer vs wild settled this battle

    def start(self) -> None:
        species_src = (
            f"override {self.species_override['name']}"
            if self.species_override
            else "OCR from screen"
        )
        status_src = f"override {self.status_override}" if self.status_override else "from screen"
        print(f"species: {species_src}, status: {status_src}")
        print("waiting for PokeMMO window...")
        QTimer.singleShot(0, self.step)

    def step(self) -> None:
        interval_s = self._tick()
        QTimer.singleShot(int(interval_s * 1000), self.step)

    def _frame_interval(self) -> float:
        return BATTLE_FRAME_S if self.state is AppState.BATTLE else IDLE_FRAME_S

    def _tick(self) -> float:
        if self.state is AppState.WAITING:
            self.hwnd = find_pokemmo_hwnd()
            if self.hwnd is None:
                return WAITING_POLL_S
            print("PokeMMO window found")
            self.state = AppState.IDLE

        assert self.hwnd is not None
        # Capture the FULL window (matches the full-window fixtures the CV regions
        # are calibrated on); dock the overlay to the client area (below the HUD).
        win_rect = get_window_rect(self.hwnd)
        client_rect = get_client_rect(self.hwnd)
        if win_rect is None or client_rect is None:
            if not is_window_alive(self.hwnd):
                print("window lost, waiting...")
                self.state = AppState.WAITING
                self.hwnd = None
                self.overlay.hide_battle()
            return WAITING_POLL_S

        frame = self.capture.grab(win_rect)
        now = time.monotonic()
        reading = read_battle(frame, self.cal)
        has_bar = reading.state in (BattleState.SINGLE, BattleState.MULTI)
        # Enter a battle only when an enemy HP bar is actually detected (the login
        # screen / menus have a dark bottom panel too). Once in battle, the panel
        # keeps us in through intro/attack animations when the bar momentarily
        # vanishes.
        panel = is_battle_ui_present(frame, self.cal.battle_ui)

        if has_bar or (self.state is AppState.BATTLE and panel):
            self.last_seen_battle = now
            if self.state is not AppState.BATTLE:
                self._enter_battle()
            self._battle_step(frame, reading, client_rect, now)
        elif self.state is AppState.BATTLE and now - self.last_seen_battle > BATTLE_END_GRACE_S:
            self.state = AppState.IDLE
            self.last_line = ""
            self.cached = None
            if not self.caught_handled:  # after a catch we already said "caught X!"
                print("battle ended")
            self.caught_handled = False
            self.overlay.hide_battle()

        return self._frame_interval()

    def _enter_battle(self) -> None:
        self.state = AppState.BATTLE
        self.cached = None  # new battle: re-identify the species
        self.last_line = ""
        self.turns.reset()
        self.hp.reset()
        self.status.reset()
        self.last_chat_ocr = 0.0
        self.caught_handled = False
        self.dusk_active = False
        self._loc_read = False
        self._chat_misses = 0
        self._is_trainer = False
        self._trainer_decided = False
        print("battle detected")

    def _battle_step(self, frame, reading, rect, now: float) -> None:
        # Read the location once per battle (it never changes mid-battle) to set
        # the Dusk Ball cave boost. Retry until a non-empty name is read (the first
        # battle frame can be mid-transition).
        if not self._loc_read:
            loc = read_location(frame, self.cal.location)
            if loc:
                self.dusk_active = is_cave_location(loc)
                self._loc_read = True
                note = " (cave -> Dusk Ball boosted)" if self.dusk_active else ""
                print(f"location: {loc}{note}")

        asleep = reading.state is BattleState.SINGLE and reading.bars[0].status is Status.SLP

        # poll the chat for the EXACT turn number when visible. Back off when the
        # chat keeps reading nothing (minimized / wrong tab) so the wasted OCR
        # doesn't slow the loop and the menu fallback stays responsive.
        chat_interval = CHAT_OCR_INTERVAL_S if self._chat_misses < 4 else CHAT_OCR_IDLE_S
        if now - self.last_chat_ocr >= chat_interval:
            chat_turn = read_turn_number(frame, self.cal.chat)
            self._chat_misses = 0 if chat_turn is not None else self._chat_misses + 1
            before = self.turns.turns_completed
            self.turns.observe(chat_turn, asleep)
            if self.debug and self.turns.turns_completed > before:
                print(f"[dbg] chat -> turn {self.turns.turns_completed + 1} (read {chat_turn})")
            self.last_chat_ocr = now

        # Template-match the in-viewport text box EVERY frame (~10 ms): drives the
        # chat-independent turn counter (command menu reappears each turn) and catch
        # detection ("Gotcha!"). See [battle_text] / BattleTextReader.
        bt = self.battle_text.read(frame)
        before = self.turns.turns_completed
        self.turns.observe_menu(bt.menu_present)
        # Decide trainer vs wild ONCE per battle, and only while the command menu is
        # up: then the scene is static, so the party-icon strip below the bar is
        # reliable. Checking during animations gave false positives.
        stable = bt.menu_present and reading.state is BattleState.SINGLE
        if stable and not self._trainer_decided:
            self._is_trainer = is_trainer_battle(frame, reading.bars[0], self.cal.trainer)
            self._trainer_decided = True
            if self._is_trainer:
                print("trainer battle: overlay hidden")
        if self.debug:
            if bt.menu_present != self._dbg_menu:
                print(f"[dbg] command menu {'DETECTED' if bt.menu_present else 'gone'}")
                self._dbg_menu = bt.menu_present
            if self.turns.turns_completed > before:
                print(f"[dbg] menu -> turn {self.turns.turns_completed + 1}")
        if bt.caught and not self.caught_handled and self.cached is not None:
            print(f"caught {self.cached['name']}!")
            self.caught_handled = True
            self.last_line = ""

        if self.caught_handled:
            return  # enemy caught: stop updating; battle ends when the UI clears
        if reading.state is BattleState.SINGLE:
            if self._is_trainer:
                self.overlay.hide_battle()  # trainer: nothing catchable
            else:
                self._update_single(frame, reading.bars[0], rect)
        elif reading.state is BattleState.MULTI and self.last_line != "multi":
            # horde / double: wait until a single wild Pokemon remains
            print("multiple enemy bars (horde): waiting for one to remain")
            self.last_line = "multi"
            self.overlay.hide_battle()
        # NO_BATTLE while in battle = intro/animation: keep the overlay as is

    def _update_single(self, frame, bar, rect) -> None:
        hp_pct = self.hp.update(bar.hp_pct)  # wait for the bar to settle
        # debounce the status so the catch animation's blue ball flash can't
        # briefly flip it (e.g. PSN -> FRZ); a real change still gets through.
        status = self.status_override or self.status.update(bar.status.value)
        if self.species_override is not None:
            self.cached = self.species_override
        elif self.cached is None:
            assert self.name_reader is not None
            sp = self.name_reader.read(frame, bar)
            if sp is not None:
                self.cached = sp
                print(f"identified: {sp['name']} (catch rate {sp['catch_rate']})")
        elif self.cached.get("level") is None and self.name_reader is not None:
            # species known but the level OCR missed it that frame; keep trying
            # (a clearer later frame usually yields it). Drives the Nest Ball.
            sp = self.name_reader.read(frame, bar)
            if sp is not None and sp.get("level") is not None and sp["name"] == self.cached["name"]:
                self.cached["level"] = sp["level"]

        turn_note = f"turn {self.turns.turns_completed + 1}"
        if self.turns.turns_asleep:
            turn_note += f", asleep {self.turns.turns_asleep}"
        if self.cached is None:
            line = f"{'?':12.12s} HP {hp_pct:5.1f}% [{status}]  ({turn_note})"
        else:
            ctx = battle_context(
                self.cached,
                turns_completed=self.turns.turns_completed,
                turns_asleep=self.turns.turns_asleep,
                dusk_active=self.dusk_active,
            )
            probs = ball_probs(
                hp_pct, self.cached["catch_rate"], self.status_rates[status], self.balls, ctx
            )
            line = f"[{turn_note}] " + format_line(self.cached["name"], hp_pct, status, probs)
            self.overlay.apply_scale(scale_for_window(rect.height))
            self.overlay.show_battle(
                self.cached.get("id", -1),
                self.cached["name"],
                self.cached["catch_rate"],
                self.turns.turns_completed + 1,
                dict(probs),
                level=self.cached.get("level"),
                status=status,
                hp_pct=hp_pct,
            )
            self.overlay.dock_to(rect.left, rect.top, rect.width)
        if line != self.last_line:
            print(line)
            self.last_line = line


def run(
    species_override: dict | None,
    status_override: str | None,
    cal: Calibration,
    debug: bool = False,
) -> None:
    app = QApplication(sys.argv[:1])
    overlay = Overlay([b["name"] for b in load_balls()])
    loop = LiveLoop(species_override, status_override, cal, overlay, debug=debug)
    loop.start()
    sys.exit(app.exec())


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
    parser.add_argument(
        "--debug",
        action="store_true",
        help="log how each turn is counted (chat vs command menu) and menu detection",
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
        run(species_override, args.status, cal, debug=args.debug)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
