# Changelog

## Unreleased

### Changed

- Depend on `netifaces-plus` instead of `netifaces` so caproto can enumerate every interface on multi-homed hosts on all platforms. It is a maintained drop-in fork (same `netifaces` module) with Windows and macOS arm64 wheels; upstream `netifaces` is sdist-only past Python 3.9, so it is no longer excluded on macOS.

### Added

- Assistant: choose Claude Code or Gemini (agy) in Preferences → Assistant, with sign-in, model picker and switching without a restart.
- First-run setup wizard: pick your facility, reach its server (SOCKS proxy off-site), sign in and set up the assistant; facility settings load from the server's `/.well-known/lightfall`. Re-run from Help → Setup wizard….

### Fixed

- Fix transposed (x/y-swapped) detector images. pyqtgraph's image axis order is
  now set globally to `row-major` at startup, matching the numpy / Tiled /
  area-detector `(row, col) = (y, x)` convention. The Image Stack, live camera,
  heatmap, scan-map, adaptive heatmap, and export views no longer apply
  scattered per-widget `col-major` / `.T` transposes.
- Assistant: cancelling a reply now finalises streamed text and subagent cards; Gemini no longer shows $0.0000 cost.

## 0.1.1 (2026-06-04)

First release published to PyPI (`pip install lightfall`).

- Publish sdist + wheel to PyPI on release tags via OIDC trusted publishing
- Drop the `bcs` extra (direct-URL dependency; install instructions for the
  ALS-internal `bcsophyd-zmq` backend moved to the README)
- Fix Briefcase packaging on macOS (Apple Silicon only, netifaces excluded)
- README: PyPI install instructions; fix stale repository URL

## 0.1.0 (2026-06-04)

First public release on GitHub (github.com/als-controls/lightfall).

- Unified beamline control dashboard: device management, Bluesky-based
  acquisition, Tiled data browser, visualization, plugin system
- Claude-powered assistant integration
- Per-service authentication with fine-grained access control
- Native installers built with Briefcase (Linux, macOS, Windows)
- Archived on Zenodo: https://doi.org/10.5281/zenodo.20545717

## 0.0.1 – 0.0.4

Internal development releases on ALS GitLab (as `lucid` / `ncs`).
