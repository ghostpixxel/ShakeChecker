"""Detect battle state and read enemy HP bars from a captured frame.

Detection is structural, not position-based: within a configurable search
region, saturated green/yellow/red fill runs are located and validated
against the fixed bar geometry (218 px inner width, white crosshatch for the
empty part — see fixtures/expected.json _meta). This keeps the reader robust
against window size, resolution and the orange stat-stage boxes the game
draws next to the bar.

Pure/injectable: functions take a BGR frame (numpy) plus a Calibration; no
capture or global state in this module.
"""

from __future__ import annotations

import enum
import tomllib
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel


class HpColor(enum.StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class BattleState(enum.Enum):
    NO_BATTLE = "no_battle"
    SINGLE = "single"
    MULTI = "multi"  # double battle / horde: ignored in v1


class Status(enum.StrEnum):
    NONE = "none"
    SLP = "slp"
    PAR = "par"
    PSN = "psn"
    BRN = "brn"
    FRZ = "frz"


@dataclass(frozen=True)
class BarReading:
    hp_pct: float  # (0, 100]
    color: HpColor
    status: Status
    x: int  # fill start, frame coords
    y: int  # bar middle row, frame coords


@dataclass(frozen=True)
class BattleReading:
    state: BattleState
    bars: tuple[BarReading, ...]
    is_horde: bool = False  # spread (3x/5x) layout -- bars across the top, not stacked


@dataclass(frozen=True)
class BattleText:
    """What the in-viewport bottom text area shows this frame."""

    menu_present: bool  # command menu up == waiting for input (turn start)
    caught: bool  # capture banner ("Gotcha!")
    action: bool  # a committed-action narration ("X used Y!") -> a real turn ran


class HsvCalibration(BaseModel):
    sat_min: int
    val_min: int
    green_h: tuple[int, int]
    yellow_h: tuple[int, int]
    red_h_low: tuple[int, int]
    red_h_high: tuple[int, int]


class EmptyBarCalibration(BaseModel):
    val_min: int
    sat_max: int
    min_light_ratio: float


class FrameCalibration(BaseModel):
    sat_mean_max: float
    val_std_max: float
    val_mean_min: float
    val_mean_max: float
    search_px: int


class HpBarCalibration(BaseModel):
    inner_width_px: int
    width_tolerance_px: int
    full_fill_min_px: int
    search_top: float
    search_bottom: float
    search_left: float
    search_right: float
    min_fill_height_px: int
    max_fill_height_px: int
    min_fill_width_px: int
    merge_y_px: int
    merge_x_px: int
    hsv: HsvCalibration
    frame: FrameCalibration
    empty: EmptyBarCalibration


class StatusHueCalibration(BaseModel):
    yellow: tuple[int, int]
    magenta: tuple[int, int]
    red: tuple[int, int]
    cyan: tuple[int, int]


class StatusCalibration(BaseModel):
    dx0: int
    dx1: int
    dy0: int
    dy1: int
    horde_dx0: int  # status badge x-range for spread HORDE bars (right of the fill)
    horde_dx1: int
    dark_val_max: int
    present_dark_frac: float
    fill_sat_min: int
    fill_val_min: int
    band_frac_min: float
    white_sat_max: int
    white_val_min: int
    white_frac_min: float
    hue: StatusHueCalibration


class NameCalibration(BaseModel):
    dx0: int
    dx1: int
    dy0: int
    dy1: int
    upscale: int
    min_match_score: float


class BattleUiCalibration(BaseModel):
    top: float
    bottom: float
    left: float
    right: float
    dark_val_max: int
    min_dark_frac: float


class ChatCalibration(BaseModel):
    top: float
    bottom: float
    left: float
    right: float
    upscale: int
    # The chat box is fixed pixel size at the window's bottom-left, so `right` (a
    # fraction of WIDTH) over-captures badly on wide windows: at 3438 px, 0.40 is a
    # 1375 px crop that is mostly empty, and OCR downscales it until the small chat
    # text is illegible. Cap the crop width here so it stays a tight, legible strip
    # regardless of window width. 0 disables the cap.
    max_width_px: int = 0

    def crop_x(self, width: int) -> tuple[int, int]:
        """(x0, x1) of the chat crop for a frame this wide, width-capped."""
        x0 = int(width * self.left)
        x1 = int(width * self.right)
        if self.max_width_px:
            x1 = min(x1, x0 + self.max_width_px)
        return x0, x1


class BattleTextCalibration(BaseModel):
    top: float
    bottom: float
    left: float
    right: float
    menu_match_min: float  # template-match score to call the command menu present
    catch_match_min: float  # template-match score to call the catch banner present
    action_match_min: float  # template-match score to call a committed action ("used")


class TrainerCalibration(BaseModel):
    dy0: int
    dy1: int
    width_px: int
    edge_frac_min: float


class LocationCalibration(BaseModel):
    top: float
    bottom: float
    left: float
    right: float
    upscale: int


class CaughtIconCalibration(BaseModel):
    dx0: int
    dx1: int
    dy0: int
    dy1: int
    red_h_low: int
    red_h_high: int
    sat_min: int
    val_min: int
    min_red_px: int


class Calibration(BaseModel):
    hp_bar: HpBarCalibration
    status: StatusCalibration
    name: NameCalibration
    battle_ui: BattleUiCalibration
    chat: ChatCalibration
    battle_text: BattleTextCalibration
    trainer: TrainerCalibration
    location: LocationCalibration
    hud_time: LocationCalibration  # same top-left HUD crop shape, for the clock line
    caught_icon: CaughtIconCalibration


def load_calibration(path: Path | str) -> Calibration:
    with open(path, "rb") as f:
        return Calibration.model_validate(tomllib.load(f))


def _fill_mask(hsv: np.ndarray, cal: HpBarCalibration) -> np.ndarray:
    c = cal.hsv
    mask = np.zeros(hsv.shape[:2], np.uint8)
    for h_lo, h_hi in (c.green_h, c.yellow_h, c.red_h_low, c.red_h_high):
        mask |= cv2.inRange(
            hsv,
            np.array([h_lo, c.sat_min, c.val_min]),
            np.array([h_hi, 255, 255]),
        )
    return mask


def _classify_color(median_hue: float, cal: HsvCalibration) -> HpColor:
    if cal.green_h[0] <= median_hue <= cal.green_h[1]:
        return HpColor.GREEN
    if cal.yellow_h[0] <= median_hue <= cal.yellow_h[1]:
        return HpColor.YELLOW
    return HpColor.RED


def _fill_run(
    hsv: np.ndarray, y: int, x_seed: int, cal: HpBarCalibration
) -> tuple[int, int] | None:
    """Contiguous saturated run on row `y` containing `x_seed` (1 px gaps ok)."""
    row = hsv[y]
    colored = (row[:, 1] >= cal.hsv.sat_min) & (row[:, 2] >= cal.hsv.val_min)
    if not colored[x_seed]:
        return None
    x0 = x_seed
    gap = 0
    while x0 > 0 and gap <= 1:
        gap = gap + 1 if not colored[x0 - 1] else 0
        x0 -= 1
    x0 += gap
    x1 = x_seed
    gap = 0
    limit = min(len(colored) - 1, x_seed + cal.inner_width_px + cal.width_tolerance_px)
    while x1 < limit and gap <= 1:
        gap = gap + 1 if not colored[x1 + 1] else 0
        x1 += 1
    x1 -= gap
    return x0, x1


def _empty_part_is_crosshatch(
    hsv: np.ndarray, y: int, fill_x0: int, fill_x1: int, cal: HpBarCalibration
) -> bool:
    """Validate that the rest of the bar (right of the fill) is the light
    crosshatch pattern, distinguishing a real HP bar from colored scenery."""
    start = fill_x1 + 3
    end = fill_x0 + cal.inner_width_px - 3
    if end > hsv.shape[1]:
        return False
    seg = hsv[y, start:end]
    if len(seg) < 4:
        return True  # nothing left to check; fill width already near-full
    light = (seg[:, 2] >= cal.empty.val_min) & (seg[:, 1] <= cal.empty.sat_max)
    return float(np.mean(light)) >= cal.empty.min_light_ratio


def _inner_width(
    hsv: np.ndarray, fill_top: int, fill_bottom: int, x0: int, cal: HpBarCalibration
) -> int:
    """Measure the bar's actual inner width from its gray frame outline (the thin
    uniform line above/below the fill), which spans exactly fill+empty and is
    bounded by the sky/scene. Lets a narrower horde bar (212 px) read as 100% when
    full instead of fill/218. Falls back to the nominal width if no outline is found."""
    f = cal.frame
    limit = min(x0 + cal.inner_width_px + cal.width_tolerance_px + 4, hsv.shape[1])

    def gray_width(y: int) -> int:
        if not 0 <= y < hsv.shape[0]:
            return 0
        last = x0 - 1
        gap = 0
        x = x0
        while x < limit:
            px = hsv[y, x]
            if px[1] <= 40 and f.val_mean_min <= px[2] <= f.val_mean_max:  # uniform gray
                last = x
                gap = 0
            else:
                gap += 1
                if gap > 2:
                    break
            x += 1
        width = last - x0 + 1
        ok = cal.min_fill_width_px <= width <= cal.inner_width_px + cal.width_tolerance_px
        return width if ok else 0

    # The true outline is the gray line immediately bordering the fill. Scan
    # outward from the fill and take the nearest ring that yields an outline on
    # either side -- a non-adjacent gray line (a neighbouring horde bar's frame,
    # scenery) sits farther away and is ignored. Median guards a one-off misread.
    for dy in range(1, f.search_px + 1):
        ring = [w for w in (gray_width(fill_top - dy), gray_width(fill_bottom + dy)) if w]
        if ring:
            return int(round(float(np.median(ring))))
    return cal.inner_width_px


def _is_frame_outline_row(hsv: np.ndarray, y: int, x0: int, cal: HpBarCalibration) -> bool:
    if not 0 <= y < hsv.shape[0] or x0 + cal.inner_width_px > hsv.shape[1]:
        return False
    seg = hsv[y, x0 : x0 + cal.inner_width_px]
    f = cal.frame
    return (
        float(np.mean(seg[:, 1])) <= f.sat_mean_max
        and float(np.std(seg[:, 2])) <= f.val_std_max
        and f.val_mean_min <= float(np.mean(seg[:, 2])) <= f.val_mean_max
    )


def _has_bar_frame(
    hsv: np.ndarray, fill_top: int, fill_bottom: int, x0: int, cal: HpBarCalibration
) -> bool:
    """The bar's gray outline runs the full inner width directly above and
    below the fill; scenery and icons have no such uniform line."""
    s = cal.frame.search_px
    above = any(_is_frame_outline_row(hsv, y, x0, cal) for y in range(fill_top - s, fill_top))
    if not above:
        return False
    return any(
        _is_frame_outline_row(hsv, y, x0, cal) for y in range(fill_bottom + 1, fill_bottom + s + 1)
    )


def classify_status_box(hsv_box: np.ndarray, cal: StatusCalibration) -> Status:
    """Classify the enemy status from the badge-region HSV crop.

    Presence is decided by the dark border/icon (no-status slot has none);
    a present badge is classified by its fill colour (PAR yellow, PSN magenta,
    BRN red, FRZ cyan) or, lacking a hue, by its white field (SLP)."""
    if hsv_box.size == 0:
        return Status.NONE
    h, s, v = hsv_box[:, :, 0], hsv_box[:, :, 1], hsv_box[:, :, 2]
    if float(np.mean(v < cal.dark_val_max)) < cal.present_dark_frac:
        return Status.NONE

    sat = (s >= cal.fill_sat_min) & (v >= cal.fill_val_min)

    def band(rng: tuple[int, int]) -> float:
        return float(np.mean(sat & (h >= rng[0]) & (h <= rng[1])))

    bands = {
        Status.PAR: band(cal.hue.yellow),
        Status.PSN: band(cal.hue.magenta),
        Status.BRN: band(cal.hue.red),
        Status.FRZ: band(cal.hue.cyan),
    }
    best = max(bands, key=lambda k: bands[k])
    if bands[best] >= cal.band_frac_min:
        return best
    white = float(np.mean((v >= cal.white_val_min) & (s < cal.white_sat_max)))
    if white >= cal.white_frac_min:
        return Status.SLP
    return Status.NONE


def read_status(hsv_band: np.ndarray, x: int, y: int, cal: StatusCalibration) -> Status:
    """Status for a bar whose fill starts at (x, y) in frame coords."""
    y0, y1 = y + cal.dy0, y + cal.dy1
    x0, x1 = x + cal.dx0, x + cal.dx1
    if y0 < 0 or x0 < 0 or y1 > hsv_band.shape[0] or x1 > hsv_band.shape[1]:
        return Status.NONE
    return classify_status_box(hsv_band[y0:y1, x0:x1], cal)


# A horde (3x/5x) spreads its bars horizontally; a single/double keeps them at the
# left (a double stacks them vertically at the SAME x). If the bars' x-range
# exceeds this, it's a spread horde -> the status badge is read at the horde offset.
HORDE_SPREAD_PX = 80

# A single enemy / trainer / wild-double bar sits in the canonical top-left slot; a
# horde spreads its bars across the centre. A lone (or stacked) bar found right of
# this fraction is therefore a horde mon that outlasted its pack -- its status badge
# is on the RIGHT of the fill (horde layout), even though the horizontal-spread test
# can no longer see it. Measured x-fractions across every fixture (ratio 1.31-2.39,
# width 1182-3437 px): singles 0.171-0.188, horde bars 0.318-0.691. The threshold
# sits at the midpoint of that gap so BOTH sides keep ~0.06 of margin at any window
# size -- resolution-independent (a fraction, not pixels). This is only the backup;
# the primary signal is the 560 px+ spread of a fresh horde, which latches reliably
# at every resolution. Mirrors HORDE_REMNANT_X_FRAC in app.py (trainer-skip).
REMNANT_X_FRAC = 0.25


def _bars_spread(bars: list[BarReading]) -> bool:
    """True if 2+ bars are spread horizontally (a horde), vs stacked (a double)."""
    if len(bars) < 2:
        return False
    return bool(max(b.x for b in bars) - min(b.x for b in bars) > HORDE_SPREAD_PX)


def _is_horde_layout(bars: list[BarReading], frame_width: int, horde_hint: bool) -> bool:
    """Whether these bars use the spread-horde layout (status badge RIGHT of the
    fill): the caller's hint, OR 2+ bars spread horizontally, OR ANY bar sitting
    right of the single-enemy slot. The last case catches a horde narrowed to its
    remnant(s) -- one lone bar, or two left-column mons stacked at the centre x --
    which a single/trainer/wild-double (always in the left slot) never is."""
    if horde_hint or _bars_spread(bars):
        return True
    return any(b.x > frame_width * REMNANT_X_FRAC for b in bars)


def read_enemy_bars(
    frame_bgr: np.ndarray, cal: Calibration, horde: bool = False
) -> list[BarReading]:
    """Find and measure all enemy HP bars in the frame, top to bottom.

    `horde` forces the spread-horde status-badge offset (used for a horde narrowed
    to one bar, which can't be told apart by layout); otherwise it's auto-detected
    from the bars' horizontal spread."""
    c = cal.hp_bar
    h, w = frame_bgr.shape[:2]
    y0, y1 = int(h * c.search_top), int(h * c.search_bottom)
    x0, x1 = int(w * c.search_left), int(w * c.search_right)
    roi = frame_bgr[y0:y1, x0:x1]
    hsv_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    # Convert only the top search band to HSV, not the whole frame: every consumer
    # below (fill run, frame outline, inner width, status badge) works at absolute
    # rows that live inside this band, and search_top is 0 so the band starts at
    # row 0 -- coordinates are unchanged. The pad covers the frame-outline scan that
    # reaches search_px rows below the lowest fill. Saves ~70% of the per-frame
    # cvtColor cost (the bottom of the frame is just the scene/menu, never a bar).
    band_h = min(y1 + c.frame.search_px + 2, h)
    hsv_band = cv2.cvtColor(frame_bgr[:band_h], cv2.COLOR_BGR2HSV)

    mask = _fill_mask(hsv_roi, c)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 9), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)

    bars: list[BarReading] = []
    for i in range(1, n):
        bx, by, bw, bh, _area = stats[i]
        if not (c.min_fill_height_px <= bh <= c.max_fill_height_px):
            continue
        if bw < c.min_fill_width_px:
            continue
        # back to frame coordinates, measure on the blob's middle row
        fy = y0 + by + bh // 2
        fx = x0 + bx
        run = _fill_run(hsv_band, fy, fx, c)
        if run is None:
            continue
        rx0, rx1 = run
        fill_w = rx1 - rx0 + 1
        if fill_w > c.inner_width_px + c.width_tolerance_px:
            continue  # wider than a bar can be: scenery / UI panel
        if fill_w < c.full_fill_min_px and not _empty_part_is_crosshatch(hsv_band, fy, rx0, rx1, c):
            continue
        if not _has_bar_frame(hsv_band, y0 + by, y0 + by + bh - 1, rx0, c):
            continue
        hues = hsv_band[fy, rx0 : rx1 + 1, 0].astype(float)
        color = _classify_color(float(np.median(hues)), c.hsv)
        # Size the bar by its OWN inner width (measured from the gray frame outline),
        # not a fixed count: a full but narrower horde bar (212 px) then reads 100%,
        # not 97%. Single bars measure 218 and are unchanged.
        inner = _inner_width(hsv_band, y0 + by, y0 + by + bh - 1, rx0, c)
        hp_pct = min(100.0, 100.0 * fill_w / inner)
        # status read AFTER the layout is known (its badge offset differs in hordes)
        bars.append(BarReading(round(hp_pct, 1), color, Status.NONE, rx0, fy))

    # Merge duplicate detections of the SAME bar (multiple blobs per fill), which
    # land at the same x AND y. Horde bars sit at the same y but different x, so
    # requiring x-proximity too keeps them as separate bars (and counts the horde).
    bars.sort(key=lambda b: (b.y, b.x))
    merged: list[BarReading] = []
    for bar in bars:
        last = merged[-1] if merged else None
        if (
            last is not None
            and abs(bar.y - last.y) <= c.merge_y_px
            and abs(bar.x - last.x) <= c.merge_x_px
        ):
            if bar.hp_pct > last.hp_pct:
                merged[-1] = bar
            continue
        merged.append(bar)

    # Spread horde, the caller's hint, OR a lone remnant sitting at the horde slot
    # -> the status badge is on the RIGHT of the fill, not the left. Read each bar's
    # status with the matching offset.
    s = cal.status
    if _is_horde_layout(merged, w, horde):
        s = s.model_copy(update={"dx0": s.horde_dx0, "dx1": s.horde_dx1})
    return [
        BarReading(b.hp_pct, b.color, read_status(hsv_band, b.x, b.y, s), b.x, b.y) for b in merged
    ]


def is_battle_ui_present(frame_bgr: np.ndarray, cal: BattleUiCalibration) -> bool:
    """True if the battle command panel (dark bottom band) is on screen.

    This is the authoritative in-battle signal: it stays present through the
    intro banner and attack animations, unlike the enemy HP bar."""
    h, w = frame_bgr.shape[:2]
    band = frame_bgr[int(h * cal.top) : int(h * cal.bottom), int(w * cal.left) : int(w * cal.right)]
    if band.size == 0:
        return False
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    return float(np.mean(gray < cal.dark_val_max)) >= cal.min_dark_frac


def is_trainer_battle(frame_bgr: np.ndarray, bar: BarReading, cal: TrainerCalibration) -> bool:
    """True if the enemy bar belongs to a TRAINER battle (nothing catchable).

    A trainer's party indicator — six small icons (grey circles / Poke balls /
    Pokemon icons) — sits in a fixed strip just below the enemy HP bar. Wild
    battles have only the battle scene there. We detect the icons by edge density
    in that strip: trainer fixtures measure well above zero, every wild fixture
    is exactly zero. See [trainer] in calibration.toml."""
    y0, y1 = bar.y + cal.dy0, bar.y + cal.dy1
    x0, x1 = bar.x, bar.x + cal.width_px
    if y0 < 0 or x0 < 0 or y1 > frame_bgr.shape[0] or x1 > frame_bgr.shape[1]:
        return False
    strip = frame_bgr[y0:y1, x0:x1]
    if strip.size == 0:
        return False
    gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    return float(np.mean(edges)) / 255.0 >= cal.edge_frac_min


def read_caught_icon(frame_bgr: np.ndarray, bar: BarReading, cal: CaughtIconCalibration) -> bool:
    """True if the enemy species is OT-caught: a red/white Poke Ball icon sits
    right of its name.

    PokeMMO marks a wild species you have caught with a Poke Ball next to the
    name; only the standard red/white ball means *you* caught it (OT). A species
    obtained by trade/evolution shows a (mostly white) Premier Ball, and an
    un-owned one shows no ball -- both have too few saturated-red pixels to pass.
    The icon's red top half is the signal: measured 61 red px when present vs 0
    absent, with the pink female symbol (hue ~156) outside the red range. The
    search band is relative to the bar (fixed-size UI). See [caught_icon]."""
    h, w = frame_bgr.shape[:2]
    x0, x1 = max(0, bar.x + cal.dx0), min(w, bar.x + cal.dx1)
    y0, y1 = max(0, bar.y + cal.dy0), min(h, bar.y + cal.dy1)
    if x1 <= x0 or y1 <= y0:
        return False
    hsv = cv2.cvtColor(frame_bgr[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    is_red = (hue <= cal.red_h_low) | (hue >= cal.red_h_high)
    red = is_red & (sat >= cal.sat_min) & (val >= cal.val_min)
    return int(np.count_nonzero(red)) >= cal.min_red_px


class BattleTextReader:
    """Detect the command menu and the catch banner by TEMPLATE MATCHING, not OCR.

    The battle UI renders at a fixed pixel size, so the words "FIGHT" (always
    present while the command menu is up) and "Gotcha!" (the catch banner) are
    the same size in every native capture. Matching small grayscale templates of
    them in the in-viewport text band is ~10 ms vs >1 s for OCR, so it can run
    every frame and the turn/catch update immediately. See [battle_text]."""

    def __init__(self, cal: BattleTextCalibration, templates_dir: Path | str) -> None:
        self._cal = cal
        d = Path(templates_dir)
        self._menu_tpl = cv2.imread(str(d / "menu_fight.png"), cv2.IMREAD_GRAYSCALE)
        self._catch_tpl = cv2.imread(str(d / "catch_gotcha.png"), cv2.IMREAD_GRAYSCALE)
        # A committed action shows one of these: "X used Y!" (attack/item, and the
        # enemy's counter), or "Go! Y!" (sending a Pokemon on a switch).
        self._action_tpls = [
            cv2.imread(str(d / "action_used.png"), cv2.IMREAD_GRAYSCALE),
            cv2.imread(str(d / "action_go.png"), cv2.IMREAD_GRAYSCALE),
        ]
        loaded = [self._menu_tpl, self._catch_tpl, *self._action_tpls]
        if any(t is None for t in loaded):
            raise FileNotFoundError(f"missing battle-text templates in {d}")

    def read(self, frame_bgr: np.ndarray) -> BattleText:
        c = self._cal
        h, w = frame_bgr.shape[:2]
        band = frame_bgr[int(h * c.top) : int(h * c.bottom), int(w * c.left) : int(w * c.right)]
        if band.size == 0:
            return BattleText(menu_present=False, caught=False, action=False)
        gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
        action = any(self._match(gray, t) >= c.action_match_min for t in self._action_tpls)
        return BattleText(
            menu_present=self._match(gray, self._menu_tpl) >= c.menu_match_min,
            caught=self._match(gray, self._catch_tpl) >= c.catch_match_min,
            action=action,
        )

    @staticmethod
    def _match(gray: np.ndarray, tpl: np.ndarray) -> float:
        if gray.shape[0] < tpl.shape[0] or gray.shape[1] < tpl.shape[1]:
            return 0.0
        return float(cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED).max())


def read_battle(frame_bgr: np.ndarray, cal: Calibration, horde: bool = False) -> BattleReading:
    bars = read_enemy_bars(frame_bgr, cal, horde=horde)
    if not bars:
        state = BattleState.NO_BATTLE
    elif len(bars) == 1:
        state = BattleState.SINGLE
    else:
        state = BattleState.MULTI
    is_horde = _is_horde_layout(bars, frame_bgr.shape[1], horde)
    return BattleReading(state=state, bars=tuple(bars), is_horde=is_horde)
