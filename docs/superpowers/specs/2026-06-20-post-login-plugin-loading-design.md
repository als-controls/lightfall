# Post-login plugin loading — design

**Status:** proposed
**Date:** 2026-06-20
**Area:** `lightfall` core — plugin loader + startup sequence
**Author:** Ayaka (with Ron)

## Summary

Today the background plugin load (`PluginLoader.start_loading()`) is kicked off
at application startup, **before the login dialog**. Because the login dialog is
modal and runs a nested event loop, non-preload plugins instantiate and emit
`plugin_loaded` *while the operator is still looking at the login screen* —
before they have authenticated. Plugins that do I/O on load or first render
(e.g. the NSLS-II ring-status status-bar plugin opening a caproto connection)
therefore touch the network/hardware pre-login, and any slow/blocking work
there degrades or hangs the login screen.

This is a design flaw: **only plugins that are needed to render the login window
should load before login.** Everything else should load *after* authentication.

The fix is small in spirit — defer `start_loading()` until the `AUTHENTICATED`
transition — but it entangles with one other startup step (`setup_default_layout`
reads the panel registry, which is populated during `start_loading`). This doc
specifies the target sequence, the entanglement and its resolution, the
per-plugin-type impact, and the validation plan.

## Motivation / the flaw

- A status-bar plugin (always-on chrome, built in the main-window constructor)
  opened caproto subscriptions on its first render, which happened *during* the
  modal login dialog. Pre-login network I/O is wrong on its face, and a
  misconfigured/slow connection there is felt as a frozen login screen.
- More generally, **nothing the operator hasn't authenticated for should be
  doing work.** Loading the full plugin set before login also slows time-to-
  login-screen and spins up resources that may never be used (failed login,
  wrong user, etc.).

The `preload=True` flag already exists to mean "this plugin must exist before
the window." We make it mean, precisely, **"must exist before login"** — and
make *not* setting it mean **"load after login."**

## Current architecture (as-is)

Startup, in `main()` (`lightfall/main.py`):

```
_setup_devices()
_setup_plugins(app):
    register plugin types
    loader.load_manifest(builtin) / discover_manifests()   # register PluginInfos
    loader.load_preload_plugins()                           # SYNC: instantiate preload=True
    loader.start_loading()                                  # BACKGROUND: load everything else  ← fires here
_setup_user_plugins(app)
window = LFMainWindow()                                     # builds status bar; StatusBarManager.load_plugins()
window.setup_default_layout()                               # reads PanelRegistry to build the sidebar
_show_startup_login(window)                                 # dialog.exec()  (MODAL, nested event loop)
```

Relevant existing machinery:

- `PluginLoader` is a `QObject` with signals `loading_started`,
  `plugin_loaded(PluginInfo)`, `plugin_failed(PluginInfo)`,
  `loading_complete(ok, failed)`.
- Two phases: `load_preload_plugins()` (synchronous, pre-window) and
  `start_loading()` (background thread; emits `plugin_loaded` per plugin,
  marshaled to the main thread).
- During loading, `_register_with_type_registry(info)` registers each plugin
  with its type registry — e.g. **panels register their class with
  `PanelRegistry` here**, statusbar plugins are handled by `StatusBarManager`,
  engines with `EngineRegistry`, etc.
- `StatusBarManager.load_plugins()` already **subscribes** to `plugin_loaded`
  and only adds already-instantiated plugins (it does not force-instantiate).
- `mainwindow.setup_default_layout()` reads `PanelRegistry` (panel metadata:
  `default_area`, `sidebar_order`) to register **deferred** side panels and
  sidebar buttons. Panels are not instantiated until shown.
- `mainwindow._on_plugin_loading_complete(ok, failed)` sets `_plugins_loaded`
  and calls `_maybe_start_proactive_init()`, which starts proactive panel
  instantiation only once `_window_shown and _plugins_loaded`. **This already
  encodes "wait for plugins to load, then instantiate panels."**

The root cause: `start_loading()` is called in `_setup_plugins()` (before the
window and before login), so the background wave runs concurrently with — and
mostly before — `_show_startup_login()`.

## Proposed design (to-be)

**Defer the background plugin wave until the `AUTHENTICATED` transition.**

Two plugin phases, by intent:

1. **Preload (pre-window, pre-login)** — `preload=True`. Only plugins required
   to render the login window. In practice: `auth_provider` plugins (the login
   dialog renders one button per registered provider) and the theme. Loaded
   synchronously by `load_preload_plugins()` as today.

2. **Main wave (post-login)** — everything else (statusbar, panel, agent,
   device_backend, plan, controller, settings, …). `start_loading()` is no
   longer called at startup; it fires **once**, on the first
   `SessionManager.state_changed → AUTHENTICATED`. Existing subscribers
   (`StatusBarManager` via `plugin_loaded`, `mainwindow` via `loading_complete`)
   react exactly as they do now — just later, after login.

Sequence after the change:

```
_setup_plugins(app):
    register types; load_manifest / discover; load_preload_plugins()   # preload only
    (DO NOT start_loading here)
window = LFMainWindow()
arm one-shot: SessionManager.state_changed == AUTHENTICATED  →  loader.start_loading()
_show_startup_login(window)            # login
   └─ on AUTHENTICATED → start_loading() → plugin_loaded… → loading_complete
                                            └─ StatusBarManager adds widgets (post-login)
                                            └─ mainwindow._on_plugin_loading_complete → proactive panel init
```

If the session is already `AUTHENTICATED` when we arm (e.g. cached token / auto
login / `NCS_AUTH` dev override), fire `start_loading()` immediately so we never
wait for a transition that won't come.

## The `setup_default_layout` entanglement and its resolution

`setup_default_layout()` runs pre-login today and reads `PanelRegistry`, which is
populated **during** `start_loading()`. If `start_loading()` moves past login,
the pre-login layout sees an empty registry and registers no side panels.

Resolution: **move the default-layout build into the post-load flow.** The panel
sidebar is built from registered panel classes, and the system already waits for
`_plugins_loaded` before proactive init — so:

- `setup_default_layout()` is invoked after the main wave registers panels
  (driven from `_on_plugin_loading_complete`, alongside / just before
  `_maybe_start_proactive_init`), **not** in `main()` pre-login.
- Saved-layout restoration (window geometry + docking state via `showEvent`)
  must be re-checked against this reorder: the saved state is restored on
  `showEvent`, and the default layout only applies on first run (no saved
  state). The ordering invariant to preserve is *"panels are registered before
  the layout that references them is applied,"* which the post-load flow
  satisfies. **This is the highest-risk part of the change and the focus of
  validation (below).**

Pre-login, the main window therefore shows with no docked panels behind the
login dialog. That is acceptable and arguably correct (nothing is usable until
login).

## Per-plugin-type impact

| Type            | Phase after change | Notes |
|-----------------|--------------------|-------|
| `auth_provider` | **preload**        | Required to render login buttons. Unchanged. |
| `theme`         | preload            | Applied before window so first paint is themed. |
| `statusbar`     | post-login         | `StatusBarManager` already subscribes; widgets (and any I/O on render) now happen post-login. |
| `panel`         | post-login         | Registered with `PanelRegistry` in the main wave; layout + proactive init already gate on `_plugins_loaded`. |
| `device_backend`| post-login         | Device catalog populates after login (device UI is post-login anyway). |
| `agent` / `plan` / `controller` / `settings` | post-login | No pre-login consumer. |

## Consumer impact (CMS endstation)

- **Auth (`nsls2_tiled`)** stays `preload=True` — the only CMS preload. Unchanged.
- **Device backend** becomes an ordinary post-login plugin: it sets the packaged
  happi-JSON path and lets the canonical `HappiBackend` load devices in the
  post-login wave. No preload, no special pre-login arming.
- **SAM kernel hosting / device injection** (the post-login bootstrap that ran
  the profile and injected ophyd objects into the console kernel) relied on a
  trigger armed *before* `AUTHENTICATED`. Under this model that arming would be
  too late if it rides on a post-login plugin. Since CMS device loading now uses
  the canonical happi-JSON path and the kernel injection is no longer strictly
  necessary, the bootstrap should be re-expressed as a **post-login action**
  (or dropped) rather than a pre-login-armed trigger. Tracked as a separate CMS
  follow-up; out of scope for this core change.

## Backward compatibility

- Deployments with no login (or auto-auth) get an immediate `start_loading()`
  via the "already authenticated → fire now" guard, so behavior is unchanged for
  them.
- The `preload` flag keeps its meaning; existing preload plugins are unaffected.
- Plugins that previously (accidentally) relied on loading before login must now
  set `preload=True` if they genuinely need to. This is a deliberate, documented
  semantic.

## Risks & validation

- **Highest risk:** the `setup_default_layout` reorder vs saved-state
  restoration (`showEvent`). A startup/login/layout reorder **cannot be
  meaningfully validated by unit tests** — it needs a real GUI + login run.
- Validation plan:
  1. Unit-test the loader/sequence seam in isolation: `start_loading()` is not
     called at startup; arming fires it once on `AUTHENTICATED`; the
     already-authenticated path fires immediately; double-`AUTHENTICATED` loads
     once.
  2. Smoke-test on the deployment box (ws5), restart-by-restart, watching the
     bootstrap/startup log: (a) login screen is interactive immediately, with no
     pre-login plugin/network activity; (b) after login the status bar and
     panels populate; (c) **saved layout restores correctly** on a second launch
     and a first-run default layout builds correctly on a cleared profile.

## Open questions / decisions

- **Where to apply `setup_default_layout`** in the post-load flow — before vs
  after `_maybe_start_proactive_init`, and how it interacts with `showEvent`
  restoration. To be settled during implementation with the box smoke-test.
- **Should `theme` stay preload?** (Yes, for a themed first paint — but confirm
  no theme plugin does I/O at load.)

## Implementation sketch (files)

- `lightfall/main.py` — remove `start_loading()` from `_setup_plugins()`; arm a
  one-shot on `SessionManager.state_changed` (with the already-authenticated
  guard) after the window exists; stop calling `setup_default_layout()` pre-login.
- `lightfall/ui/mainwindow.py` — drive `setup_default_layout()` from the
  post-`loading_complete` path; preserve the panels-registered-before-layout
  invariant and saved-state restoration.
- Tests — loader/sequence seam (see validation plan).
- Docs — this spec; a short plan doc with the task checklist.
