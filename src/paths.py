"""Resolve resource and user-data paths for both source runs and PyInstaller builds.

From source, resources live under the repo (this file is in src/) and per-account
user data sits in repo/userdata/. In a frozen build, PyInstaller unpacks the bundled
data under sys._MEIPASS, and user data must go somewhere writable (Program Files is
not) -- so it goes to %APPDATA%/ShakeChecker. All path resolution funnels through
here so the rest of the code doesn't special-case being frozen.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "ShakeChecker"
APP_VERSION = "1.1.1"  # keep in sync with pyproject [project].version


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Base dir holding bundled resources (calibration.toml, src/data/...)."""
    if _frozen():
        # _MEIPASS in onefile / the exe dir in onedir; both hold the --add-data tree.
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent  # repo root (src/ -> ..)


# Bundled, read-only resources.
DATA_DIR = resource_root() / "src" / "data"
CALIBRATION_PATH = resource_root() / "calibration.toml"


def userdata_dir() -> Path:
    """Writable per-account data dir. %APPDATA%/ShakeChecker when frozen, else
    repo/userdata/. The account store creates subdirs on save, so this only
    computes the path."""
    if _frozen():
        base = Path(os.environ.get("APPDATA") or Path.home())
        return base / APP_NAME
    return resource_root() / "userdata"
