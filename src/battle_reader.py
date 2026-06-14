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


class Calibration(BaseModel):
    hp_bar: HpBarCalibration
    status: StatusCalibration
    name: NameCalibration
    battle_ui: BattleUiCalibration
    chat: ChatCalibration
    battle_text: BattleTextCalibration
    trainer: TrainerCalibration
    location: LocationCalibration


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


def read_status(hsv_full: np.ndarray, x: int, y: int, cal: StatusCalibration) -> Status:
    """Status for a bar whose fill starts at (x, y) in frame coords."""
    y0, y1 = y + cal.dy0, y + cal.dy1
    x0, x1 = x + cal.dx0, x + cal.dx1
    if y0 < 0 or x0 < 0 or y1 > hsv_full.shape[0] or x1 > hsv_full.shape[1]:
        return Status.NONE
    return classify_status_box(hsv_full[y0:y1, x0:x1], cal)


def read_enemy_bars(frame_bgr: np.ndarray, cal: Calibration) -> list[BarReading]:
    """Find and measure all enemy HP bars in the frame, top to bottom."""
    c = cal.hp_bar
    h, w = frame_bgr.shape[:2]
    y0, y1 = int(h * c.search_top), int(h * c.search_bottom)
    x0, x1 = int(w * c.search_left), int(w * c.search_right)
    roi = frame_bgr[y0:y1, x0:x1]
    hsv_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hsv_full = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)

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
        run = _fill_run(hsv_full, fy, fx, c)
        if run is None:
            continue
        rx0, rx1 = run
        fill_w = rx1 - rx0 + 1
        if fill_w > c.inner_width_px + c.width_tolerance_px:
            continue  # wider than a bar can be: scenery / UI panel
        if fill_w < c.full_fill_min_px and not _empty_part_is_crosshatch(hsv_full, fy, rx0, rx1, c):
            continue
        if not _has_bar_frame(hsv_full, y0 + by, y0 + by + bh - 1, rx0, c):
            continue
        hues = hsv_full[fy, rx0 : rx1 + 1, 0].astype(float)
        color = _classify_color(float(np.median(hues)), c.hsv)
        # Size the bar by its OWN inner width (measured from the gray frame outline),
        # not a fixed count: a full but narrower horde bar (212 px) then reads 100%,
        # not 97%. Single bars measure 218 and are unchanged.
        inner = _inner_width(hsv_full, y0 + by, y0 + by + bh - 1, rx0, c)
        hp_pct = min(100.0, 100.0 * fill_w / inner)
        status = read_status(hsv_full, rx0, fy, cal.status)
        bars.append(BarReading(hp_pct=round(hp_pct, 1), color=color, status=status, x=rx0, y=fy))

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
    return merged


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


def read_battle(frame_bgr: np.ndarray, cal: Calibration) -> BattleReading:
    bars = read_enemy_bars(frame_bgr, cal)
    if not bars:
        state = BattleState.NO_BATTLE
    elif len(bars) == 1:
        state = BattleState.SINGLE
    else:
        state = BattleState.MULTI
    return BattleReading(state=state, bars=tuple(bars))
