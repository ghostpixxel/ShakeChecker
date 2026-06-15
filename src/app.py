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

from account_store import AccountConfig, CaughtStore
from battle_log import AsyncChatReader, read_turn_number
from battle_reader import (
    BattleState,
    BattleTextReader,
    Calibration,
    Status,
    is_trainer_battle,
    load_calibration,
    read_battle,
    read_caught_icon,
)
from catch_calc import BattleContext, ball_multiplier, catch_probability
from dex_session import DexSession, LocationView
from dex_tracker import EncounterData
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
ENCOUNTERS_PATH = DATA / "encounters.json"
LEGENDARIES_PATH = DATA / "legendaries.json"
USERDATA = ROOT / "userdata"  # per-account caught lists + active-account config

WAITING_POLL_S = 2.0
IDLE_FRAME_S = 0.5  # ~2 fps
BATTLE_FRAME_S = 0.2  # ~5 fps
# End the battle once the battle-specific signals (enemy bar + menu/action/catch
# templates) have all been gone this long — covers brief animation gaps without
# lingering too long after a faint/flee.
BATTLE_END_GRACE_S = 2.0
# Location OCR for the dex panel is comparatively expensive and the location
# changes slowly, so refresh it at most this often while walking around (IDLE).
DEX_LOC_INTERVAL_S = 2.5
DEX_SHOWN_MAX = 5  # entries shown before collapsing the rest into "+X"


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


def dex_panel_text(view: LocationView | None) -> str:
    """The console form of the dex 'missing here' panel: a header line plus up to
    DEX_SHOWN_MAX species by dex id, then '+X' for the rest. '' if no location."""
    if view is None:
        return ""
    header = (
        f"[dex] {view.route} ({view.region}) {view.period.value} S{view.season}"
        f" — {len(view.missing)} needed"
    )
    if not view.missing:
        return header + "\n  (all caught here!)"
    shown = view.missing[:DEX_SHOWN_MAX]
    lines = [header, *[f"  #{m.id:<4} {m.name}" for m in shown]]
    extra = len(view.missing) - len(shown)
    if extra > 0:
        lines.append(f"  +{extra}")
    return "\n".join(lines)


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
        dex: DexSession | None = None,
    ) -> None:
        self.species_override = species_override
        self.status_override = status_override
        self.cal = cal
        self.overlay = overlay
        self.dex = dex  # None if the dex data couldn't be loaded
        self.balls = load_balls()
        self.status_rates = load_status_rates()
        self.name_reader = None if species_override else NameReader(cal.name, SPECIES_PATH)
        self.battle_text = BattleTextReader(cal.battle_text, TEMPLATES_DIR)
        self.chat = AsyncChatReader(cal.chat)  # background turn-OCR (correction only)
        self.capture = WindowCapture()

        self.state = AppState.WAITING
        self.hwnd: int | None = None
        self.last_seen_battle = 0.0
        self.last_line = ""
        self.cached: dict | None = None  # enemy for the current battle
        self.turns = TurnTracker()
        self.hp = HpSettler()
        self.status = StatusSettler()
        self._caught_printed = False  # printed "caught X!" this battle
        self._catch_streak = 0  # consecutive frames the catch banner was seen
        self.dusk_active = False  # cave/night -> Dusk Ball boost
        self._loc_read = False  # location OCR'd this battle yet
        self._is_trainer = False  # trainer battle -> overlay hidden
        self._trainer_decided = False  # trainer vs wild settled this battle
        self._ot_checked = False  # enemy's OT-caught icon checked this battle
        self._last_loc_check = 0.0  # last IDLE location OCR (throttle)
        self._dex_panel = ""  # last printed dex panel (dedup)

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
        # Membership uses battle-SPECIFIC signals only: the enemy HP bar plus the
        # menu/action/catch templates. (The old dark-panel signal false-positives
        # in a dark CAVE overworld, so the battle never ended there.) During an
        # attack animation the bar vanishes but the "X used Y!" text shows; brief
        # gaps are covered by the end grace.
        bt = self.battle_text.read(frame)
        in_battle = has_bar or bt.menu_present or bt.action or bt.caught

        if in_battle:
            self.last_seen_battle = now
            if self.state is not AppState.BATTLE:
                self._enter_battle()
            self._battle_step(frame, reading, bt, client_rect)
        elif self.state is AppState.BATTLE and now - self.last_seen_battle > BATTLE_END_GRACE_S:
            self.state = AppState.IDLE
            self.last_line = ""
            self.cached = None
            if not self._caught_printed:  # after a catch we already said "caught X!"
                print("battle ended")
            self._caught_printed = False
            self.overlay.hide_battle()

        # Walking around (not in battle): refresh the "missing here" dex panel from
        # the HUD location on a throttle (location OCR is slow, location changes
        # slowly). Skipped during battles, where the location is read once instead.
        dex_due = now - self._last_loc_check >= DEX_LOC_INTERVAL_S
        if not in_battle and self.dex is not None and dex_due:
            self._last_loc_check = now
            self._update_dex(read_location(frame, self.cal.location))

        return self._frame_interval()

    def _enter_battle(self) -> None:
        self.state = AppState.BATTLE
        self.cached = None  # new battle: re-identify the species
        self.last_line = ""
        self.turns.reset()
        self.hp.reset()
        self.status.reset()
        self.chat.reset()  # drop any in-flight turn OCR from the previous battle
        self._caught_printed = False
        self._catch_streak = 0
        self.dusk_active = False
        self._loc_read = False
        self._is_trainer = False
        self._trainer_decided = False
        self._ot_checked = False
        print("battle detected")

    def _battle_step(self, frame, reading, bt, rect) -> None:
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
                if self.dex is not None:  # same read drives the dex panel
                    self._update_dex(loc)

        asleep = reading.state is BattleState.SINGLE and reading.bars[0].status is Status.SLP

        # Chat turn is a CORRECTION only: the slow OCR runs on a background thread
        # (submit when free, pick up the result when ready) so it never blocks. It
        # only raises the count (a missed turn); the fast menu drives the live value.
        self.chat.submit(frame)
        chat_turn = self.chat.poll()
        if chat_turn is not None:
            self.turns.observe(chat_turn, asleep)

        # `bt` (menu/action/catch templates, ~10 ms) was read in the loop and is
        # passed in: it drives the chat-independent turn counter (command menu
        # reappears each turn) and catch detection ("Gotcha!").
        # Count menu turns only in a SINGLE battle. During a horde (MULTI) the
        # multi-target attack/faint animation makes the menu flicker repeatedly,
        # which would over-count; the chat tracks the real turn through the horde,
        # and menu counting resumes once one Pokemon remains.
        if reading.state is BattleState.SINGLE:
            self.turns.observe_menu(bt.menu_present, bt.action)
        # Decide trainer vs wild ONCE per battle, and only while the command menu is
        # up: then the scene is static, so the party-icon strip below the bar is
        # reliable. Checking during animations gave false positives.
        stable = bt.menu_present and reading.state is BattleState.SINGLE
        if stable and not self._trainer_decided:
            self._is_trainer = is_trainer_battle(frame, reading.bars[0], self.cal.trainer)
            self._trainer_decided = True
            if self._is_trainer:
                print("trainer battle: overlay hidden")
        # Catch: announce once when the "Gotcha!" banner holds for 2+ frames (a
        # single stray match never triggers it). This does NOT freeze the overlay
        # -- the loop keeps updating so the turn still self-corrects from the chat;
        # the battle ends on its own when the UI clears (grace).
        self._catch_streak = self._catch_streak + 1 if bt.caught else 0
        if self._catch_streak >= 2 and not self._caught_printed and self.cached is not None:
            print(f"caught {self.cached['name']}!")
            self._caught_printed = True

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

        # Record the enemy as OT-caught once per battle, the moment its species is
        # known and the red/white Poke Ball icon shows next to the name. This grows
        # the caught list as you play and removes the species from "missing here".
        if (
            self.dex is not None
            and not self._ot_checked
            and self.cached is not None
            and self.cached.get("id")
            and read_caught_icon(frame, bar, self.cal.caught_icon)
        ):
            self._ot_checked = True
            if self.dex.record_caught(self.cached["id"]):
                print(f"dex: recorded OT-caught {self.cached['name']}")

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

    def _update_dex(self, hud_name: str) -> None:
        """Refresh the 'missing here' panel from a HUD location name, printing it
        only when it actually changes (location, region or list)."""
        if self.dex is None or not hud_name:
            return
        panel = dex_panel_text(self.dex.on_location(hud_name))
        if panel and panel != self._dex_panel:
            print(panel)
            self._dex_panel = panel


def build_dex(account_override: str | None) -> DexSession | None:
    """Build the dex session for the active account, or None if the encounter
    data is missing. The active account is chosen manually and remembered: an
    explicit --account wins, else the last used one, else a 'default' profile."""
    if not ENCOUNTERS_PATH.exists():
        print("dex: encounters.json not found (run scripts/update_data.py) — dex disabled")
        return None
    data = EncounterData.load(ENCOUNTERS_PATH, LEGENDARIES_PATH)
    cfg = AccountConfig.load(USERDATA)
    account = cfg.resolve_active(account_override)
    if account is None:
        account = cfg.use("default")
        print("dex: no account set — using 'default' (pass --account NAME per character)")
    caught = CaughtStore.for_account(USERDATA, account)
    print(f"dex: account '{account}' — {len(caught.caught)} species marked caught")
    return DexSession(data, caught)


def run(
    species_override: dict | None,
    status_override: str | None,
    cal: Calibration,
    account: str | None = None,
) -> None:
    app = QApplication(sys.argv[:1])
    overlay = Overlay([b["name"] for b in load_balls()])
    loop = LiveLoop(species_override, status_override, cal, overlay, dex=build_dex(account))
    loop.start()
    try:
        code = app.exec()
    finally:
        loop.chat.shutdown()
    sys.exit(code)


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
        "--account",
        help="PokeMMO account/character for the dex caught-list (remembered; "
        "defaults to the last used)",
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
        run(species_override, args.status, cal, account=args.account)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
