"""End-to-end dex check on screenshot(s): location OCR -> region -> missing-here,
plus the enemy's OT-caught ball icon. Processes images in order through ONE
session, so the tracked region pins from a region-unique location and then
resolves ambiguous ones -- exactly the live behaviour, but offline.

    python scripts/dex_image_check.py fixtures/full_health_no_status.png
    python scripts/dex_image_check.py fixtures/red_health_no_status_cave.png --region Sinnoh
    python scripts/dex_image_check.py A.png B.png --period NIGHT --season 1
"""

from __future__ import annotations

import argparse
import io
import sys
import tempfile
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from account_store import CaughtStore  # noqa: E402
from battle_reader import load_calibration, read_battle, read_caught_icon  # noqa: E402
from dex_session import DexSession  # noqa: E402
from dex_tracker import EncounterData  # noqa: E402
from game_time import Period  # noqa: E402
from location_reader import read_location  # noqa: E402
from name_reader import NameReader  # noqa: E402

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")

DATA = ROOT / "src" / "data"
SHOWN_MAX = 5


def main() -> None:
    p = argparse.ArgumentParser(description="Dex check on screenshot(s)")
    p.add_argument("images", nargs="+", help="screenshot path(s), processed in order")
    p.add_argument("--region", help="seed the starting region (Kanto/Johto/...)")
    p.add_argument("--period", choices=[x.value for x in Period])
    p.add_argument("--season", type=int, choices=[0, 1, 2, 3])
    p.add_argument("--all", action="store_true", help="list every missing entry")
    args = p.parse_args()

    cal = load_calibration(ROOT / "calibration.toml")
    data = EncounterData.load(DATA / "encounters.json", DATA / "legendaries.json")
    name_reader = NameReader(cal.name, DATA / "species_core.json")

    # throwaway caught store so the check never writes real account data
    with tempfile.TemporaryDirectory() as tmp:
        caught = CaughtStore.for_account(tmp, "_check")
        kw = {}
        if args.period:
            kw["period_fn"] = lambda: Period(args.period)
        if args.season is not None:
            kw["season_fn"] = lambda: args.season
        session = DexSession(data, caught, **kw)
        if args.region:
            session.seed_region(args.region)

        for path in args.images:
            img = cv2.imread(path)
            if img is None:
                print(f"{path}: cannot read\n")
                continue
            loc_raw = read_location(img, cal.location)

            # enemy + OT ball (single-battle only)
            reading = read_battle(img, cal)
            enemy_line = ""
            if reading.bars:
                bar = reading.bars[0]
                enemy = name_reader.read(img, bar)
                ot = read_caught_icon(img, bar, cal.caught_icon)
                who = enemy["name"] if enemy else "?"
                enemy_line = f"  enemy: {who}  OT-caught: {'YES' if ot else 'no'}"

            view = session.on_location(loc_raw)
            print(f"[{path}]  HUD={loc_raw!r}")
            if view is None:
                print("  location not matched (unknown, or ambiguous before a region is known)")
                if enemy_line:
                    print(enemy_line)
                print()
                continue
            print(f"  {view.route}  ({view.region})  [{view.period.value}, season {view.season}]")
            missing = [e for e in view.entries if not e.caught]
            print(f"  {len(missing)} still needed")
            shown = missing if args.all else missing[:SHOWN_MAX]
            for m in shown:
                ways = f" ({'/'.join(m.ways)})" if m.ways else ""
                print(f"    #{m.id:<4} {m.name:<13} [{m.rarity}]{ways}")
            extra = len(missing) - len(shown)
            if extra > 0:
                print(f"    +{extra}")
            if enemy_line:
                print(enemy_line)
            print()


if __name__ == "__main__":
    main()
