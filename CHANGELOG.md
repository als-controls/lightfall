# Changelog

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
