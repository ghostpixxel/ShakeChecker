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
import logging
import sys
import time

import win32api
import win32con
import win32event
import winerror
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import paths
from account_store import AccountConfig, CaughtStore, delete_account_data
from battle_log import AsyncChatReader, read_turn_number
from battle_logic import (
    apply_chat_turn,
    battle_end_grace,
    debounce_menu,
    dex_panel_action,
    is_horde_remnant,
    is_in_battle,
)
from battle_reader import (
    BattleState,
    BattleTextReader,
    Calibration,
    Status,
    is_battle_ui_present,
    is_trainer_battle,
    load_calibration,
    read_battle,
    read_caught_icon,
)
from catch_calc import BattleContext, ball_multiplier, catch_probability
from catch_chain import CatchChain
from dex_panel import DexPanel
from dex_session import DexSession, LocationView
from dex_tracker import EncounterData, select_display
from game_time import current_game_minute, is_dusk_ball_night, season_name
from hp_settler import HpSettler
from location_reader import is_cave_location, read_game_clock, read_location
from name_reader import NameReader
from overlay import Overlay, scale_for_window
from settings_store import Settings
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

DATA = paths.DATA_DIR  # bundled, read-only (frozen-aware via paths.py)
SPECIES_PATH = DATA / "species_core.json"
TEMPLATES_DIR = DATA / "templates"
ENCOUNTERS_PATH = DATA / "encounters.json"
LEGENDARIES_PATH = DATA / "legendaries.json"
USERDATA = paths.userdata_dir()  # per-account caught lists (%APPDATA% when frozen)

WAITING_POLL_S = 2.0
IDLE_FRAME_S = 0.5  # ~2 fps
BATTLE_FRAME_S = 0.2  # ~5 fps
# How long the battle-specific signals (enemy bar + menu/action/catch templates)
# must ALL be gone before the battle ends. Short when the battle UI panel is
# already gone (back to the overworld -> clear the catch overlay promptly), but
# long while the dark command panel is still up: a 2-turn move (Fly/Dig/Solarbeam)
# hides the enemy bar with no menu for a couple seconds mid-battle, and that panel
# stays, so we must NOT end the battle then.
BATTLE_END_GRACE_S = 1.0
BATTLE_ANIM_GRACE_S = 4.0
# Trainer battles cycle through several Pokemon with multi-second gaps (faint +
# "sent out") that have no battle signal; a longer grace keeps those gaps from
# ending the battle (which would flash the overlays and re-run trainer detection).
TRAINER_END_GRACE_S = 6.0
# The command menu must hold present/absent this many battle frames before the
# turn counter accepts the change — filters brief template-match flicker during
# multi-target (horde) animations that would otherwise over-count turns.
MENU_STABLE_FRAMES = 2
# The chat ("Turn N started!") is ground truth and corrects the menu count in BOTH
# directions. A LOWER chat reading is only trusted (to fix an over-count) once the
# menu hasn't advanced for this long, so a stale async read right after a real
# turn advance can't briefly drag the count down.
TURN_DOWN_GUARD_S = 3.0
# Location OCR for the dex panel is comparatively expensive and the location
# changes slowly, so refresh it at most this often while walking around (IDLE).
DEX_LOC_INTERVAL_S = 2.5
# Don't hide the dex panel on a single failed location read: a garbled OCR or a
# screen transition briefly makes the HUD unreadable. Keep the last good location
# up until this many consecutive misses (~DEX_LOC_INTERVAL_S apart) confirm we've
# genuinely left to an area not in the index.
DEX_LOC_MISS_HIDE = 3
DEX_SHOWN_MAX = 5  # entries shown before collapsing the rest into "+X"

log = logging.getLogger("shakechecker")


class _LevelFormatter(logging.Formatter):
    """Plain message for INFO (the console output the user reads), '[dbg]'-prefixed
    for DEBUG -- preserving the exact look of the old print()s while letting the log
    level (set by --debug) decide what is shown. Tracebacks (log.exception) are kept."""

    def format(self, record: logging.LogRecord) -> str:
        msg = ("[dbg] " if record.levelno <= logging.DEBUG else "") + record.getMessage()
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        return msg


def setup_logging(debug: bool) -> None:
    """Route the loop's events to the console, or to a log file when there is no
    console. A windowless (console=False) PyInstaller build has sys.stdout == None,
    so a StreamHandler(sys.stdout) would crash on the first log call; fall back to
    %APPDATA%/ShakeChecker/shakechecker.log so issues stay diagnosable. --debug
    raises the level to DEBUG in either case."""
    log.handlers.clear()
    log.setLevel(logging.DEBUG if debug else logging.INFO)
    log.propagate = False  # don't double-print via the root logger
    handler: logging.Handler
    if sys.stdout is not None:
        handler = logging.StreamHandler(sys.stdout)
    else:
        try:
            logfile = paths.userdata_dir() / "shakechecker.log"
            logfile.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(logfile, mode="w", encoding="utf-8")
        except OSError:
            handler = logging.NullHandler()
    handler.setFormatter(_LevelFormatter())
    log.addHandler(handler)


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
    enemy: dict,
    turns_completed: int = 0,
    turns_asleep: int = 0,
    enemy_asleep: bool = False,
    dusk_active: bool = False,
    repeat_chain: int = 0,
) -> BattleContext:
    """Build the conditional-ball context from a resolved enemy dict.

    turns_completed/turns_asleep default to 0 until the turn counter lands, so
    Quick Ball reads x5, Timer Ball x1 and Dream Ball x1 — all correct for the
    first turn with no accumulated sleep. repeat_chain is the current same-species
    catch streak (0 unless this enemy matches the active Repeat Ball chain)."""
    return BattleContext(
        turns_completed=turns_completed,
        turns_asleep=turns_asleep,
        enemy_asleep=enemy_asleep,
        enemy_types=tuple(enemy.get("types") or ()),
        enemy_level=enemy.get("level") or 1,
        dusk_active=dusk_active,
        repeat_chain=repeat_chain,
    )


def dex_panel_text(view: LocationView | None) -> str:
    """The console form of the dex panel: a header with the still-needed count,
    then up to DEX_SHOWN_MAX rows. Uncaught species come first by dex id ('+X' for
    the rest); once those fit, the tail is padded with the rarest already-caught
    species (marked ✓) so the notable rares stay visible. '' if no location."""
    if view is None:
        return ""
    needed = sum(1 for e in view.entries if not e.caught)
    header = (
        f"[dex] {view.route} ({view.region}) {view.period.value} {season_name(view.season)}"
        f" — {needed} needed"
    )
    rows, hidden = select_display(view.entries, DEX_SHOWN_MAX)
    if not rows:
        return header + "\n  (all caught here!)"
    lines = [header]
    for e in rows:
        check = " ✓" if e.caught else ""
        lines.append(f"  #{e.id:<4} {e.name} [{e.rarity}]{_ways_note(e.ways)}{check}")
    if hidden > 0:
        lines.append(f"  +{hidden}")
    return "\n".join(lines)


def _ways_note(ways: tuple[str, ...]) -> str:
    """Parenthesised non-default encounter ways for an entry, e.g. ' (Water)',
    ' (Good Rod/Old Rod)', ' (Lure)', ' (Grass Pheno)'. Empty for plain
    grass/cave walking (dex_tracker.encounter_tag already dropped those)."""
    return f" ({'/'.join(ways)})" if ways else ""


def format_line(
    name: str, hp_pct: float, status: str, probs: list[tuple[str, float | None]]
) -> str:
    balls = "  ".join(f"{ball} {'??' if p is None else f'{100 * p:5.1f}%'}" for ball, p in probs)
    return f"{name:12.12s} HP {hp_pct:5.1f}% [{status}]  {balls}"


def ball_probs(
    hp_pct: float, base_rate: int | None, status_rate: float, balls: list[dict], ctx: BattleContext
) -> list[tuple[str, float | None]]:
    """Catch probability per ball. base_rate is None for species with no known
    catch rate (roaming Latias/Latios/Mesprit/Cresselia) -> every prob is None
    (the overlay/console then show "??")."""
    if base_rate is None:
        return [(b["name"], None) for b in balls]
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
        dex_panel: DexPanel | None = None,
    ) -> None:
        self.species_override = species_override
        self.status_override = status_override
        self.cal = cal
        self.overlay = overlay
        self.dex = dex  # None if the dex data couldn't be loaded
        self.dex_panel = dex_panel  # overworld "missing here" overlay
        self.balls = load_balls()
        self.settings = Settings.load(USERDATA)  # which balls the overlay shows
        self.overlay.set_hidden_names(self._hidden_ball_names())
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
        # Repeat Ball catch chain: consecutive catches of the SAME species without
        # interruption. Spans battles (NOT reset in _enter_battle); see _on_catch.
        self._chain = CatchChain()
        self.dusk_active = False  # cave/night -> Dusk Ball boost
        self._loc_read = False  # location OCR'd this battle yet
        self._is_trainer = False  # trainer battle -> overlay hidden
        self._trainer_decided = False  # trainer vs wild settled this battle
        self._ot_checked = False  # enemy's OT-caught icon checked this battle
        self._was_horde = False  # read_battle horde hint (read every tick, so init here)
        self._last_loc_check = 0.0  # last IDLE location OCR (throttle)
        self._loc_miss_streak = 0  # consecutive unmatched location reads (debounce hide)
        self._dex_log = ""  # last printed dex panel text (console dedup)
        self._last_hud = ""  # last HUD location seen (to refresh the panel on a toggle)
        if self.dex_panel is not None and self.dex is not None:
            self.dex_panel.on_toggle_caught = self._dex_toggle_caught
            self.dex_panel.on_select_profile = self._dex_use_profile
            self.dex_panel.on_create_profile = self._dex_use_profile
            self.dex_panel.on_delete_profile = self._dex_delete_profile
            self.dex_panel.get_profiles = self._dex_profiles
            self.dex_panel.on_toggle_ball = self._toggle_ball
            self.dex_panel.on_set_all_balls = self._set_all_balls
            self.dex_panel.get_ball_state = self._ball_state
            self.dex_panel.get_keep_caught = lambda: self.settings.keep_caught
            self.dex_panel.on_toggle_keep_caught = self._toggle_keep_caught

    def start(self) -> None:
        log.info(f"ShakeChecker v{paths.APP_VERSION}")
        species_src = (
            f"override {self.species_override['name']}"
            if self.species_override
            else "OCR from screen"
        )
        status_src = f"override {self.status_override}" if self.status_override else "from screen"
        log.info(f"species: {species_src}, status: {status_src}")
        log.info("waiting for PokeMMO window...")
        QTimer.singleShot(0, self.step)

    def step(self) -> None:
        # One bad frame (a transient capture/OCR hiccup) must never kill the loop:
        # if _tick raised, the next singleShot would never be scheduled and the
        # overlay would freeze for good. Log it and carry on at the normal cadence.
        try:
            interval_s = self._tick()
        except Exception:
            log.exception("tick failed; continuing")
            interval_s = self._frame_interval()
        QTimer.singleShot(int(interval_s * 1000), self.step)

    def _frame_interval(self) -> float:
        return BATTLE_FRAME_S if self.state is AppState.BATTLE else IDLE_FRAME_S

    def _tick(self) -> float:
        if self.state is AppState.WAITING:
            self.hwnd = find_pokemmo_hwnd()
            if self.hwnd is None:
                return WAITING_POLL_S
            log.info("PokeMMO window found")
            self.state = AppState.IDLE

        assert self.hwnd is not None
        # Capture the FULL window (matches the full-window fixtures the CV regions
        # are calibrated on); dock the overlay to the client area (below the HUD).
        win_rect = get_window_rect(self.hwnd)
        client_rect = get_client_rect(self.hwnd)
        if win_rect is None or client_rect is None:
            if not is_window_alive(self.hwnd):
                log.info("window lost, waiting...")
                self.state = AppState.WAITING
                self.hwnd = None
                self.overlay.hide_battle()
                if self.dex_panel is not None:
                    self.dex_panel.hide_panel()
            return WAITING_POLL_S

        frame = self.capture.grab(win_rect)
        now = time.monotonic()
        # Pass the horde hint so a horde narrowed to ONE bar still reads its status
        # at the horde (right-side) badge offset; full hordes auto-detect by spread.
        reading = read_battle(frame, self.cal, horde=self._was_horde)
        if reading.is_horde:
            self._was_horde = True
        # Membership uses battle-SPECIFIC signals only: the enemy HP bar plus the
        # menu/action/catch templates. (The old dark-panel signal false-positives
        # in a dark CAVE overworld, so the battle never ended there.) During an
        # attack animation the bar vanishes but the "X used Y!" text shows; brief
        # gaps are covered by the end grace. The panel (ui_present) only tunes the
        # grace; it never extends in_battle, so a dark cave still ends the battle.
        bt = self.battle_text.read(frame)
        in_battle = is_in_battle(reading.state, bt)
        ui_present = is_battle_ui_present(frame, self.cal.battle_ui)
        grace = battle_end_grace(
            self._is_trainer,
            ui_present,
            trainer_s=TRAINER_END_GRACE_S,
            anim_s=BATTLE_ANIM_GRACE_S,
            normal_s=BATTLE_END_GRACE_S,
        )
        if in_battle:
            self.last_seen_battle = now
            if self.state is not AppState.BATTLE:
                self._enter_battle()
            self._battle_step(frame, reading, bt, client_rect, now)
        elif self.state is AppState.BATTLE and now - self.last_seen_battle > grace:
            self.state = AppState.IDLE
            self.last_line = ""
            self.cached = None
            if not self._caught_printed:  # after a catch we already said "caught X!"
                log.info("battle ended")
            self._caught_printed = False
            self.overlay.hide_battle()
            # Show the dex panel at once, from the pre-battle location (you can't
            # move during a battle, so it's still valid) -- no wait for the next
            # throttled OCR tick. _last_loc_check is reset so OCR re-confirms soon.
            self._last_loc_check = 0.0
            if self.dex_panel is not None and self.dex is not None and self._last_hud:
                view = self.dex.on_location(self._last_hud)
                if view is not None:
                    self.dex_panel.apply_scale(scale_for_window(client_rect.height))
                    self.dex_panel.show_here(view)
                    self.dex_panel.dock_to(client_rect.left, client_rect.top, client_rect.width)

        # Walking around (not in battle): refresh the "missing here" dex panel from
        # the HUD location on a throttle (location OCR is slow, location changes
        # slowly). Skipped during battles, where the location is read once instead.
        # Drive the overworld dex panel only in the IDLE state -- NOT merely when
        # "not in_battle" this frame: during a battle animation the bar/menu can
        # briefly vanish while the state is still BATTLE, which would flash the
        # dex panel over the catch overlay.
        dex_due = now - self._last_loc_check >= DEX_LOC_INTERVAL_S
        if self.state is AppState.IDLE and self.dex is not None and dex_due:
            self._last_loc_check = now
            view = self._update_dex(read_location(frame, self.cal.location))
            action, self._loc_miss_streak = dex_panel_action(
                view is not None, self._loc_miss_streak, hide_after=DEX_LOC_MISS_HIDE
            )
            if self.dex_panel is not None:
                if view is not None:  # matched -> show (action == "show")
                    self.dex_panel.apply_scale(scale_for_window(client_rect.height))
                    self.dex_panel.show_here(view)
                    self.dex_panel.dock_to(client_rect.left, client_rect.top, client_rect.width)
                elif action == "hide":  # several misses in a row -> truly left the area
                    self.dex_panel.hide_panel()
                # "keep": a transient miss -> leave the last good panel on screen

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
        self._menu_raw = False  # last raw menu_present
        self._menu_streak = 0  # frames the raw menu_present has held
        self._menu_stable = False  # debounced menu_present fed to the turn counter
        self._was_horde = False  # battle has been a spread horde (status badge on the right)
        self._last_advance = 0.0  # monotonic time the turn count last went up
        self._last_chat_turn = 0  # last turn read from chat (for the diagnostic log)
        if self.dex_panel is not None:  # overworld panel out of the way during battle
            self.dex_panel.hide_panel()
        log.info("battle detected")

    def _battle_step(self, frame, reading, bt, rect, now) -> None:
        # Read the location + clock ONCE per battle to set the Dusk Ball boost, then
        # freeze it (gated by _loc_read, reset in _enter_battle). This is deliberate:
        # PokeMMO locks the Dusk Ball bonus to the moment the battle STARTED, so if
        # the clock rolls from Night (03:59) to Morning (04:00) mid-battle the boost
        # must NOT drop. Reading once at battle start captures exactly that. Retry
        # until a non-empty name is read (the first battle frame can be mid-transition).
        if not self._loc_read:
            loc = read_location(frame, self.cal.location)
            if loc:
                cave = is_cave_location(loc)
                night = self._is_night(frame)  # Dusk Ball also boosts at night (locked here)
                self.dusk_active = cave or night
                self._loc_read = True
                bits = [b for b, on in (("cave", cave), ("night", night)) if on]
                note = f" ({'+'.join(bits)} -> Dusk Ball boosted)" if bits else ""
                log.info(f"location: {loc}{note}")
                if self.dex is not None:  # same read drives the dex panel
                    self._update_dex(loc)

        asleep = reading.state is BattleState.SINGLE and reading.bars[0].status is Status.SLP

        # Chat ("Turn N started!") is ground truth; the slow OCR runs on a
        # background thread (submit when free, pick up the result when ready) so it
        # never blocks. It corrects the menu count in BOTH directions:
        #  - UP immediately for a missed turn;
        #  - DOWN for a menu over-count, but only once the menu has been quiet for
        #    TURN_DOWN_GUARD_S, so a stale async read just after a real advance
        #    can't briefly drag the live count below the truth.
        # Poll BEFORE submit: consume any finished read first, then start the next
        # one. (Submitting first would replace a just-finished future and lose its
        # result, so the turn never arrived -- the long-standing chat bug.)
        chat_turn = self.chat.poll()
        self.chat.submit(frame)
        if chat_turn is not None and chat_turn != self._last_chat_turn:
            self._last_chat_turn = chat_turn  # shows the chat IS read (dedup the log)
            log.debug("chat: Turn %d  (counter Turn %d)", chat_turn, self.turns.turns_completed + 1)
        outcome = apply_chat_turn(
            self.turns,
            chat_turn,
            asleep=asleep,
            now=now,
            last_advance=self._last_advance,
            down_guard_s=TURN_DOWN_GUARD_S,
        )
        if outcome in ("down", "up"):  # the formatter prefixes '[dbg]' only when --debug
            shown = self.turns.turns_completed + 1
            log.debug("chat corrected %s -> Turn %d", outcome.upper(), shown)

        # `bt` (menu/action/catch templates, ~10 ms) was read in the loop and is
        # passed in: it drives the chat-independent turn counter (command menu
        # reappears each turn) and catch detection ("Gotcha!").
        # Count turns from the command menu + committed-action text in BOTH single
        # and multi. A horde has one of YOUR Pokemon (one menu per turn); a double
        # shows two selection menus, but they aren't separated by an action text,
        # so the action-gating still counts exactly once per turn. This is
        # chat-independent (works with the chat hidden); the chat only corrects up.
        #
        # The only hazard in multi is the menu template flickering during the busy
        # multi-target animation, which would look like the menu reappearing. So
        # debounce the menu signal: only accept a present/absent change after it
        # has held for MENU_STABLE_FRAMES frames.
        self._menu_raw, self._menu_streak, self._menu_stable = debounce_menu(
            bt.menu_present,
            self._menu_raw,
            self._menu_streak,
            self._menu_stable,
            threshold=MENU_STABLE_FRAMES,
        )
        before = self.turns.turns_completed
        self.turns.observe_menu(self._menu_stable, bt.action)
        if self.turns.turns_completed > before:
            self._last_advance = now  # for the chat down-correction guard
            log.debug("menu -> Turn %d", self.turns.turns_completed + 1)
        # Decide trainer vs wild ONCE per battle, and only while the command menu is
        # up: then the scene is static, so the party-icon strip below the bar is
        # reliable. Checking during animations gave false positives.
        stable = bt.menu_present and reading.state is BattleState.SINGLE
        if stable and not self._trainer_decided:
            bar = reading.bars[0]
            # A horde is always wild; skip the trainer check for a horde remnant
            # (see is_horde_remnant). Otherwise run the party-strip trainer check.
            x_frac = bar.x / frame.shape[1]
            if is_horde_remnant(self._was_horde, x_frac, self.cal.hp_bar.remnant_x_frac):
                self._is_trainer = False
            else:
                self._is_trainer = is_trainer_battle(frame, bar, self.cal.trainer)
            self._trainer_decided = True
            if self._is_trainer:
                log.info("trainer battle: overlay hidden")
        # Catch: announce once when the "Gotcha!" banner holds for 2+ frames (a
        # single stray match never triggers it). This does NOT freeze the overlay
        # -- the loop keeps updating so the turn still self-corrects from the chat;
        # the battle ends on its own when the UI clears (grace).
        self._catch_streak = self._catch_streak + 1 if bt.caught else 0
        if self._catch_streak >= 2 and not self._caught_printed and self.cached is not None:
            log.info(f"caught {self.cached['name']}!")
            self._caught_printed = True
            self._on_catch(self.cached)
            # A fresh catch has no OT ball yet (that only shows on already-owned
            # species), so record it here so it drops off the dex list at once.
            if self.dex is not None and self.cached.get("id"):
                self.dex.record_caught(self.cached["id"])

        if reading.state is BattleState.SINGLE:
            # Negative test: only show the catch overlay once we're SURE it's a
            # wild battle (trainer detection has run and said "not trainer"). This
            # avoids briefly flashing the overlay before a trainer is confirmed.
            if self._trainer_decided and not self._is_trainer:
                self._update_single(frame, reading.bars[0], rect)
            else:
                self.overlay.hide_battle()
        elif reading.state is BattleState.MULTI and self.last_line != "multi":
            # horde / double: wait until a single wild Pokemon remains
            log.info("multiple enemy bars (horde): waiting for one to remain")
            self.last_line = "multi"
            self.overlay.hide_battle()
        # NO_BATTLE while in battle = intro/animation: keep the overlay as is

    def _on_catch(self, enemy: dict) -> None:
        """Advance the Repeat Ball catch chain after a successful catch: +1 if it
        matches the active chain species, else restart the chain at this species."""
        sid = enemy.get("id")
        if sid is None:
            return
        length = self._chain.record_catch(sid)
        log.debug("repeat chain: %s x%d", enemy.get("name"), length)

    def _chain_for(self, enemy: dict | None) -> int:
        """The current catch chain length that applies to THIS enemy: the running
        chain if it's the same species, else 0 (Repeat Ball shows 1x for a fresh
        species)."""
        return self._chain.length_for(enemy.get("id") if enemy else None)

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
                rate_str = "??" if sp["catch_rate"] is None else sp["catch_rate"]
                log.info(f"identified: {sp['name']} (catch rate {rate_str})")
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
                log.info(f"dex: recorded OT-caught {self.cached['name']}")

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
                enemy_asleep=status == "slp",
                dusk_active=self.dusk_active,
                repeat_chain=self._chain_for(self.cached),
            )
            probs = ball_probs(
                hp_pct, self.cached["catch_rate"], self.status_rates[status], self.balls, ctx
            )
            line = f"[{turn_note}] " + format_line(self.cached["name"], hp_pct, status, probs)
            self.overlay.apply_scale(scale_for_window(rect.height))
            # Pass only known probabilities to the overlay; for an unknown rate
            # this is empty and show_battle renders "??" from catch_rate=None.
            overlay_probs = {name: p for name, p in probs if p is not None}
            self.overlay.show_battle(
                self.cached.get("id", -1),
                self.cached["name"],
                self.cached["catch_rate"],
                self.turns.turns_completed + 1,
                overlay_probs,
                level=self.cached.get("level"),
                status=status,
                hp_pct=hp_pct,
            )
            self.overlay.dock_to(rect.left, rect.top, rect.width)
        if line != self.last_line:
            log.info(line)
            self.last_line = line

    def _is_night(self, frame) -> bool:
        """Is it within the Dusk Ball night window (21:00-07:59 game time)? Reads
        the HUD clock the player sees; falls back to the deterministic UTC game
        time if the clock can't be read."""
        minute = read_game_clock(frame, self.cal.hud_time)
        if minute is None:
            minute = current_game_minute()
        return is_dusk_ball_night(minute)

    def _update_dex(self, hud_name: str) -> LocationView | None:
        """Resolve the HUD location to a view and log the panel when it changes.
        Returns the view (or None) so the caller can drive the overlay panel."""
        if self.dex is None or not hud_name:
            return None
        view = self.dex.on_location(hud_name)
        if view is not None:
            self._last_hud = hud_name  # remember only locations we could resolve
        panel = dex_panel_text(view)
        if panel and panel != self._dex_log:
            log.info(panel)
            self._dex_log = panel
        return view

    def _refresh_dex_panel(self) -> None:
        """Re-render the panel for the current location (after a toggle/profile
        change) so the moved species and counts update immediately."""
        if self.dex is None or self.dex_panel is None or not self._last_hud:
            return
        view = self.dex.on_location(self._last_hud)
        if view is not None:
            self.dex_panel.show_here(view)

    def _dex_toggle_caught(self, dex_id: int) -> None:
        if self.dex is None:
            return
        now = self.dex.toggle_caught(dex_id)
        log.info(f"dex: {'marked' if now else 'un-marked'} #{dex_id} as caught")
        self._refresh_dex_panel()

    def _dex_use_profile(self, name: str) -> None:
        """Switch to (or create) an account profile and reload its caught list."""
        cfg = AccountConfig.load(USERDATA)
        account = cfg.use(name)
        if self.dex is not None:
            self.dex.set_caught(CaughtStore.for_account(USERDATA, account))
        log.info(f"dex: active account '{account}'")
        self._refresh_dex_panel()

    def _dex_delete_profile(self, name: str) -> None:
        """Delete a profile and its caught list; if it was active, switch to a
        remaining one (or a fresh 'default')."""
        cfg = AccountConfig.load(USERDATA)
        cfg.delete(name)
        delete_account_data(USERDATA, name)
        account = cfg.active or cfg.use("default")
        if self.dex is not None:
            self.dex.set_caught(CaughtStore.for_account(USERDATA, account))
        log.info(f"dex: deleted profile '{name}', active now '{account}'")
        self._refresh_dex_panel()

    def _dex_profiles(self) -> tuple[str | None, list[str]]:
        cfg = AccountConfig.load(USERDATA)
        return cfg.active, cfg.accounts

    # --- ball picker (settings -> which balls the overlay shows) ---

    def _hidden_ball_names(self) -> set[str]:
        """Hidden ball NAMES for the overlay (it keys by name; settings store ids)."""
        return {b["name"] for b in self.balls if b["id"] in self.settings.hidden_balls}

    def _ball_state(self) -> tuple[list[tuple[str, str]], set[str]]:
        return [(b["id"], b["name"]) for b in self.balls], set(self.settings.hidden_balls)

    def _toggle_ball(self, ball_id: str) -> None:
        self.settings.toggle_ball(ball_id)
        self.overlay.set_hidden_names(self._hidden_ball_names())

    def _set_all_balls(self, visible: bool) -> None:
        self.settings.set_all_balls([b["id"] for b in self.balls], visible)
        self.overlay.set_hidden_names(self._hidden_ball_names())

    def _toggle_keep_caught(self) -> None:
        now = self.settings.toggle_keep_caught()
        log.info(f"dex: keep-caught {'on' if now else 'off'}")
        self._refresh_dex_panel()


def build_dex(account_override: str | None) -> DexSession | None:
    """Build the dex session for the active account, or None if the encounter
    data is missing. The active account is chosen manually and remembered: an
    explicit --account wins, else the last used one, else a 'default' profile."""
    if not ENCOUNTERS_PATH.exists():
        log.info("dex: encounters.json not found (run scripts/update_data.py) — dex disabled")
        return None
    data = EncounterData.load(ENCOUNTERS_PATH, LEGENDARIES_PATH)
    cfg = AccountConfig.load(USERDATA)
    account = cfg.resolve_active(account_override)
    if account is None:
        account = cfg.use("default")
        log.info("dex: no account set — using 'default' (pass --account NAME per character)")
    caught = CaughtStore.for_account(USERDATA, account)
    log.info(f"dex: account '{account}' — {len(caught.caught)} species marked caught")
    return DexSession(data, caught)


SINGLE_INSTANCE_NAME = "ShakeChecker_SingleInstance_Mutex"


def acquire_single_instance(name: str = SINGLE_INSTANCE_NAME) -> int | None:
    """Acquire a process-wide lock so only ONE ShakeChecker runs at a time. Returns
    the mutex handle (the caller must keep a reference for the whole process
    lifetime) or None if another instance already holds it. A Windows named mutex is
    released by the kernel when the owning process exits -- even on a crash -- so
    there is no stale lock to clean up."""
    handle = win32event.CreateMutex(None, False, name)
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        win32api.CloseHandle(handle)  # our handle is a 2nd ref to the existing mutex
        return None
    return handle


def run(
    species_override: dict | None,
    status_override: str | None,
    cal: Calibration,
    account: str | None = None,
    debug: bool = False,
) -> None:
    setup_logging(debug)
    # Only one instance may run: a second would draw a duplicate overlay over the
    # first (looks like ghosting). Hold the lock for the whole process via `lock`.
    lock = acquire_single_instance()
    if lock is None:
        log.info("ShakeChecker is already running; this instance will exit")
        win32api.MessageBox(
            0,
            "ShakeChecker is already running.",
            "ShakeChecker",
            win32con.MB_OK | win32con.MB_ICONINFORMATION,
        )
        return
    app = QApplication(sys.argv[:1])
    # The overlay and dex panels hide themselves between battles, so don't quit when
    # no window is visible -- the app lives in the tray and is quit from there.
    app.setQuitOnLastWindowClosed(False)
    icon = QIcon(str(paths.DATA_DIR / "shakechecker.ico"))
    app.setWindowIcon(icon)

    overlay = Overlay([b["name"] for b in load_balls()])
    dex = build_dex(account)
    dex_panel = DexPanel() if dex is not None else None
    loop = LiveLoop(species_override, status_override, cal, overlay, dex=dex, dex_panel=dex_panel)

    # Tray presence: a windowless build has no taskbar entry, so the tray icon is how
    # the user sees it's running and how they quit it (right-click -> Quit).
    tray = QSystemTrayIcon(icon)
    tray.setToolTip(f"ShakeChecker v{paths.APP_VERSION}")
    menu = QMenu()
    quit_action = QAction("Quit ShakeChecker", menu)
    quit_action.triggered.connect(app.quit)
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.show()
    tray.showMessage(
        "ShakeChecker",
        f"v{paths.APP_VERSION} is running. Right-click the tray icon to quit.",
        QSystemTrayIcon.MessageIcon.Information,
        4000,
    )

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
    parser.add_argument(
        "--debug",
        action="store_true",
        help="verbose turn-counter diagnostics (chat reads, menu advances)",
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

    cal = load_calibration(paths.CALIBRATION_PATH)

    if args.image:
        analyze_image(args.image, species_override, args.status, cal)
        return

    set_dpi_awareness()
    try:
        run(species_override, args.status, cal, account=args.account, debug=args.debug)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
