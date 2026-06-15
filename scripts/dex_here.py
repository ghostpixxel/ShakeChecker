"""Terminal check for the dex tracker: print what spawns at a location now that
you still need. Verifies the data + time/season + matching chain without the game.

    python scripts/dex_here.py "Viridian Forest" --region Kanto
    python scripts/dex_here.py "Route 5" --region Johto --period NIGHT --season 1
    python scripts/dex_here.py "Ilex Forest" --caught 16,19 --all
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

# Species names contain non-ASCII (Nidoran-female sign); force UTF-8 on Windows.
if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dex_tracker import EncounterData, select_display  # noqa: E402
from game_time import Period, current_period, current_season  # noqa: E402

DATA = ROOT / "src" / "data"
SHOWN_MAX = 5  # overlay shows this many; the rest collapse into "+X"


def main() -> None:
    p = argparse.ArgumentParser(description="What do I still need here?")
    p.add_argument("location", help="HUD location name, e.g. 'Viridian Forest'")
    p.add_argument("--region", help="region hint (Kanto/Johto/Hoenn/Sinnoh/Unova)")
    p.add_argument("--period", choices=[x.value for x in Period], help="override current period")
    p.add_argument("--season", type=int, choices=[0, 1, 2, 3], help="override current season")
    p.add_argument("--caught", default="", help="comma-separated dex ids already caught")
    p.add_argument("--all", action="store_true", help="list every missing entry, not just 5 + X")
    args = p.parse_args()

    data = EncounterData.load(DATA / "encounters.json", DATA / "legendaries.json")
    key = data.match_location(args.location, args.region)
    if key is None:
        print(f"no match for {args.location!r}" + (f" in {args.region}" if args.region else ""))
        if not args.region:
            print("(if it's a generic 'Route N', add --region)")
        return

    period = Period(args.period) if args.period else current_period()
    season = args.season if args.season is not None else current_season()
    caught = {int(x) for x in args.caught.split(",") if x.strip()}

    entries = data.entries_here(key, period.value, season, caught)
    needed = sum(1 for e in entries if not e.caught)
    print(f"{data.location_name(key)}  [{period.value}, season {season}] — {needed} needed")

    def fmt(e):
        ways = f" ({'/'.join(e.ways)})" if e.ways else ""
        check = " ✓" if e.caught else ""
        return f"  #{e.id:<4} {e.name:<13} [{e.rarity}]{ways}{check}"

    if args.all:  # every available species, caught flagged
        for e in entries:
            print(fmt(e))
        return
    rows, hidden = select_display(entries, SHOWN_MAX)  # hybrid: uncaught first, pad rares
    for e in rows:
        print(fmt(e))
    if hidden > 0:
        print(f"  +{hidden}")


if __name__ == "__main__":
    main()
