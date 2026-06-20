# Changelog

All notable changes to ShakeChecker are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [1.1.1] - 2026-06-21

### Fixed

- **Nidoran gender** — a male Nidoran was recognized and shown as the female
  one. OCR cannot read the gender glyph, so the name tied between the two
  entries and always resolved to the female. The gender is now read from the
  banner's colored gender icon (pink female / blue male). Catch rates were
  correct before; the name, sprite, and dex record were not.
- **Dex panel at region transitions** — the "missing here" panel no longer
  disappears on a single garbled location read; it stays up through transient
  OCR misses and only hides after several consecutive misses.

### Added

- **Single-instance lock** — starting ShakeChecker a second time now shows a
  notice and exits instead of drawing a duplicate overlay.

### Docs

- Landing page and README note that ShakeChecker is built for the default
  client theme.
- README links the VirusTotal scan and a permanent latest-download link.

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
