# Changelog

All notable changes to ShakeChecker are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [1.1.0] - 2026-06-18

### Added

- **Ball picker** — choose which Poké Balls appear in the overlay, via the
  Poké Ball button in the dex panel header.
- **Overlay sorted by catch rate** — the best ball is shown on top.
- **System-tray icon with a Quit action** — the windowless build can now be
  closed cleanly.
- New flat retro Poké Ball app/tray icon.

### Fixed

- **Trainer detection in the rain** — a rainy wild battle was misread as a
  trainer battle (which hid the overlay). The party-strip check now requires
  compact icon blobs, so diagonal weather streaks no longer trigger it. Locked
  with a rain fixture as a regression test.
- **Overlay height** — the window now shrinks to fit when balls are filtered, so
  there is no tall empty panel and rows stay evenly spaced for any ball count.
- **Dex header popups** (profiles, ball picker, legend) now close when you click
  back into the game.

### Docs

- New landing page (retro pixel) with an FAQ, demo videos, a screenshot gallery,
  and a fan-made disclaimer.

## [1.0.0] - 2026-06-17

Initial release: read-only catch-rate overlay (HP %, status, per-ball catch
probability using the Gen 3/4 formula), Location Dex "missing here" panel, and a
packaged Windows `.exe`.
