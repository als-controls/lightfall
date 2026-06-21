# Post-login plugin loading — implementation plan

**Status:** implemented (core); box validation pending
**Date:** 2026-06-20
**Spec:** [2026-06-20-post-login-plugin-loading-design.md](../specs/2026-06-20-post-login-plugin-loading-design.md)
**Branch:** `feat/post-login-plugin-loading`

## Goal

Defer the background plugin wave (`PluginLoader.start_loading`) from application
startup to *after* the login screen, so only login-window plugins (auth
providers, theme) load before login and nothing does network/hardware I/O while
the modal login dialog is up.

## Findings during implementation (deviations from / refinements to the spec)

Three facts surfaced while reading the code that the spec did not capture; they
shaped the implementation:

1. **The main window is hidden during the login dialog.** `window.show()` runs
   in `app.run()`, *after* `_show_startup_login()` returns — so `showEvent`
   fires post-login, not "behind the login dialog." The latch is therefore
   gated on `showEvent` (window-shown) AND `loading_complete` (plugins-loaded),
   whichever happens last.

2. **Guest / cancelled startup never reaches `AUTHENTICATED`.**
   `LoginDialog._on_guest_clicked` just `accept()`s; cancel likewise. Arming
   *only* on `AUTHENTICATED` would leave a guest (or a user who cancelled — the
   startup dialog allows guest, and the app then runs anonymously) with **zero
   post-login plugins**: empty status bar, no panels. So `_arm_post_login_plugin_load`
   returns a `fire()` callable that `main()` invokes **after the startup dialog
   closes, for any outcome**. It is idempotent with the `AUTHENTICATED` trigger
   and the already-authenticated guard — the wave starts exactly once.

3. **`loading_complete` must be connected before the wave can start.** An
   already-authenticated start (or a zero-plugin wave) emits `loading_complete`
   synchronously. So `main()` connects `loader.loading_complete` to the window
   *before* arming.

## Task checklist

- [x] `_setup_plugins()`: remove `loader.start_loading()`.
- [x] `main()`: add `_arm_post_login_plugin_load(loader, session_manager)` →
      one-shot `fire()`; connect on `state_changed == AUTHENTICATED`; fire now
      if already authenticated; return `fire`.
- [x] `main()`: connect `loader.loading_complete → window._on_plugin_loading_complete`
      before arming; remove the pre-login `window.setup_default_layout()` call;
      call `fire()` after `_show_startup_login()` (guest/cancel safety net).
- [x] `mainwindow`: `_on_plugin_loading_complete` → `_ensure_default_layout()`
      (build layout once) + `_finalize_layout_if_ready()`.
- [x] `mainwindow`: `_finalize_layout_if_ready()` gated on window-shown AND
      plugins-loaded; restores saved docking state (panels-first invariant) then
      starts proactive init; runs once.
- [x] `mainwindow`: `showEvent` records window-shown + calls the latch; remove
      `_watch_plugin_loading`.
- [x] Unit tests: loader/arming seam + layout-latch sequencing
      (`tests/test_post_login_plugin_loading.py`); update `test_proactive_latch.py`.
- [x] Adversarial review (3 lenses + per-finding verification); 2 findings
      confirmed and fixed:
  - [x] **No-loader regression**: the defensive `loader is None` branch now calls
        `window._on_plugin_loading_complete(0, 0)` so the window still builds a
        (empty) layout instead of staying blank. (Near-unreachable, but correct.)
  - [x] **Geometry flash**: because the window shows before `loading_complete`,
        deferring the *whole* restore caused a default-size → saved-size jump.
        `showEvent` now restores window **geometry** up-front
        (`_restore_window_geometry`); only the **dock-panel** state
        (`_restore_dock_state`) stays in the post-login `_finalize_layout_if_ready`
        (it needs panels registered first). No size jump; panels still populate
        post-login (inherent).
- [x] Full suite green (1469 passed, 3 skipped — integration deps / gpcam; one
      unrelated pre-existing flaky test, `test_user_portable_set_emits_through_topic`,
      passes in isolation / on re-run).
- [ ] **Box validation on ws5** (below).

## Box validation plan (ws5, restart-by-restart)

The startup/login/layout reorder cannot be meaningfully unit-tested. Validate on
the deployment box, watching the startup log each restart:

1. **Login screen is interactive immediately**, with **no pre-login plugin or
   network activity** in the log (no `plugin_loaded`, no caproto/backend I/O
   before `Auth state: ... -> AUTHENTICATED`).
2. After login: **status bar and panels populate** (look for "Starting
   post-login plugin wave" → `plugin_loaded` → `Plugin loading complete`).
3. **Saved layout restores correctly** on a second launch (existing
   `mainwindow/geometry` / docking state). Watch that the window appears at its
   saved **size/position/maximize immediately** (geometry is restored up-front
   in `showEvent`); panels then populate post-login (the dock layout restores
   once the wave registers them). There should be no default-size → saved-size
   resize jump.
4. **First-run default layout** builds correctly on a cleared profile (no saved
   state).
5. **Guest mode**: choosing "Continue as Guest" still loads the wave (status bar
   + permission-filtered panels appear).

### Behavior change to confirm on the box

`setup_default_layout()` now runs **post-login**, so panels are filtered by the
**authenticated** user's permissions rather than anonymous (the old pre-login
build used the anonymous user). A privileged operator may now see more default
panels than before; guest is unchanged. This is within the spirit of the change
("post-login") and arguably a fix, but confirm it reads as intended.
