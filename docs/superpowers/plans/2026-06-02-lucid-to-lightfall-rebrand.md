# Lightfall → Lightfall Rebrand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the product "Lightfall" to "Lightfall" across all of its repositories — code identifiers, directories, and GitLab project paths — while keeping "NCS" as the GitLab group/initiative name and dropping the old acronym expansion.

**Architecture:** Approach A — one atomic coordinated sweep. The core distribution (`lightfall` → `lightfall`), its plugin-discovery entry-point group (`lightfall.plugins` → `lightfall.plugins`), and every dependent plugin flip together in a single pass so plugin discovery never breaks. Renames use `git mv` to preserve history; bulk identifier changes use guarded `sed` sweeps; non-mechanical points (entry-point group constant, display strings, data-dir migration, logos, pyproject metadata) get targeted edits. GitLab projects are renamed server-side via API last, after local verification.

**Tech Stack:** Python (hatch + hatch-vcs), `importlib.metadata` entry points, PySide6, pytest; reveal.js decks; LaTeX paper; GitLab REST API v4 over a SOCKS proxy (`localhost:1080`).

**Spec:** `docs/superpowers/specs/2026-06-02-lightfall-to-lightfall-rebrand-design.md`

---

## Conventions & Safety (read first)

- **Shell:** use the Bash tool (git-bash) so `git mv` / `sed` / `grep` work uniformly on Windows.
- **Branch:** in every repo, work on `rebrand/lightfall` (the core repo `ncs/ncs` is already on it).
- **Never touch:** `.venv/`, `.venv-linux/`, `build/`, `dist/`, `*.egg-info/`, `.mypy_cache/`, `__pycache__/`, `_version.py` (hatch-vcs generated), `.git/`.
- **Intentional retained literals (do NOT rename):**
  - `client_id` default `"Lightfall"` in `src/lightfall/config/schema.py`.
  - Sentry `project_name = "Lightfall"` in `pyproject.toml` `[tool.sentry]`/config.
  - `lightfall-pipelines` / `lightfall_pipelines.pipeline` (separate, uncloned framework — only in `lightfall-endstation-7011`).
- **Case handling:** a lowercase substring sweep of `lightfall` → `lightfall` correctly covers `lightfall`, `lightfall_x` (snake), `lightfall-x` (kebab), and `lightfall.plugins` (dotted). Uppercase `Lightfall` is handled by *targeted* edits only (never blanket-swept), so the retained `"Lightfall"` literals survive.
- **Reusable sweep helper** (used by multiple tasks below):

```bash
# lightfall_sweep <dir> : lowercase substring rename across text sources, excluding generated/binary
lightfall_sweep() {
  local root="$1"
  grep -rIl --exclude-dir={.git,.venv,.venv-linux,build,dist,.mypy_cache,__pycache__} \
    --include='*.py' --include='*.toml' --include='*.cfg' --include='*.ini' \
    --include='*.md' --include='*.txt' --include='*.json' --include='*.yml' --include='*.yaml' \
    --include='*.desktop' --include='*.html' --include='*.tex' --include='*.bib' --include='Makefile' \
    'lightfall' "$root" | grep -v '_version.py' | while read -r f; do
      sed -i 's/lightfall/lightfall/g' "$f"
  done
}
```

- **Shared venv** lives at `~/PycharmProjects/ncs/.venv` (Windows) — `PY=/c/Users/rp/PycharmProjects/ncs/.venv/Scripts/python.exe`. Editable installs point at the sub-project dirs, so directory renames (Phase 6) require reinstalls.

---

## Phase 1 — Core (`ncs/ncs`, distribution `lightfall` → `lightfall`)

Working dir: `/c/Users/rp/PycharmProjects/ncs/ncs` (already on branch `rebrand/lightfall`).

### Task 1: Rename the package directory (history-preserving)

**Files:** Move `src/lightfall/` → `src/lightfall/`

- [ ] **Step 1: Move the package with git**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
git mv src/lightfall src/lightfall
```

- [ ] **Step 2: Verify the move staged cleanly**

Run: `git status --short | head`
Expected: renames `R  src/lightfall/... -> src/lightfall/...` (no `D`/`A` churn for unchanged files).

- [ ] **Step 3: Commit the bare move (keeps history readable)**

```bash
git commit -q -m "refactor: git mv src/lightfall -> src/lightfall (no content change)"
```

### Task 2: Sweep import paths and identifiers in source + tests

**Files:** all `*.py` under `src/lightfall/` and `tests/`

- [ ] **Step 1: Run the lowercase sweep over source and tests**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
lightfall_sweep src/lightfall
lightfall_sweep tests
```

- [ ] **Step 2: Re-protect the intentional `"Lightfall"` literals were untouched**

Run: `grep -rn '"Lightfall"' src/lightfall/config/schema.py`
Expected: `client_id` default still reads `"Lightfall"` (uppercase sweep was never run, so this is intact). If absent, restore it.

- [ ] **Step 3: Verify no stray lowercase `lightfall` import remains**

Run: `grep -rn 'lightfall' src/lightfall tests --include='*.py' | grep -v '"Lightfall"\|_version.py'`
Expected: no matches (empty output).

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -q -m "refactor: rewrite lightfall -> lightfall imports/identifiers in src+tests"
```

### Task 3: Update `pyproject.toml`

**Files:** Modify `pyproject.toml`

- [ ] **Step 1: Sweep the manifest, then hand-verify the high-value keys**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
sed -i 's/lightfall/lightfall/g' pyproject.toml
```

- [ ] **Step 2: Restore the Sentry project name (must stay `"Lightfall"`)**

Find the Sentry project_name line and set it back:

```bash
sed -i 's/project_name = "Lightfall"/project_name = "Lightfall"/' pyproject.toml
```

- [ ] **Step 3: Verify the manifest reads correctly**

Run: `grep -nE 'name =|\[project.scripts\]|gui-scripts|version-file|packages =|known-first-party|briefcase.app|formal_name|icon|project_name|force-include|Homepage|Repository' pyproject.toml`
Expected: `name = "lightfall"`; scripts `lightfall`, `lightfall-exporter`; `lightfall-gui`; `version-file = "src/lightfall/_version.py"`; `packages = ["src/lightfall"]`; `known-first-party = ["lightfall"]`; `[tool.briefcase.app.lightfall]`; `formal_name = "Lightfall"`; `icon = "resources/lightfall"`; force-include `"src/lightfall/ui/resources/logo.png" = "lightfall/ui/resources/logo.png"`; **`project_name = "Lightfall"`** (retained); URLs now `.../lightfall`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml && git commit -q -m "build: rename distribution lightfall -> lightfall; keep Sentry project_name"
```

### Task 4: Flip the plugin-discovery entry-point group

**Files:** Modify `src/lightfall/plugins/loader.py` (the `ENTRY_POINT_GROUP` constant + docstring), and docstring examples in `src/lightfall/plugins/manifest.py` and `src/lightfall/plugins/__init__.py`.

> Note: the Task-2 sweep already turned `"lightfall.plugins"` into `"lightfall.plugins"` everywhere. This task **verifies** the critical constant rather than editing blindly.

- [ ] **Step 1: Verify the entry-point group constant flipped**

Run: `grep -rn 'ENTRY_POINT_GROUP\|entry-points."' src/lightfall/plugins/loader.py src/lightfall/plugins/manifest.py src/lightfall/plugins/__init__.py`
Expected: `ENTRY_POINT_GROUP = "lightfall.plugins"` and all docstring examples read `[project.entry-points."lightfall.plugins"]`.

- [ ] **Step 2: Confirm no `lightfall.plugins` group string survives anywhere**

Run: `grep -rn 'lightfall.plugins' src/lightfall`
Expected: no matches.

- [ ] **Step 3: Commit (only if Step 1/2 required a fix; otherwise skip)**

```bash
git add -A && git commit -q -m "refactor: plugin entry-point group lightfall.plugins -> lightfall.plugins"
```

### Task 5: Rename icon/desktop/logo resources

**Files:** under `resources/` and `src/lightfall/ui/resources/`

- [ ] **Step 1: Rename the app-icon and desktop files**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
git mv resources/lightfall.icns resources/lightfall.icns
git mv resources/lightfall.ico  resources/lightfall.ico
git mv resources/lightfall.png  resources/lightfall.png
git mv resources/gov.lbl.als.lightfall.desktop resources/gov.lbl.als.lightfall.desktop
```

- [ ] **Step 2: Verify the desktop file's internal fields were swept (Task 3 covers `*.desktop`)**

Run: `grep -niE 'name|exec|icon|lightfall|lightfall' resources/gov.lbl.als.lightfall.desktop`
Expected: `Exec`/`Icon`/`Name` reference `lightfall`; no `lightfall` remains.

- [ ] **Step 3: Replace the in-app wordmark logo with the new Lightfall logo**

```bash
cp /c/Users/rp/Downloads/logo.png src/lightfall/ui/resources/logo.png
```

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -q -m "assets: rename app icons/desktop to lightfall; swap in Lightfall wordmark logo"
```

### Task 6: Display-name string (targeted uppercase)

**Files:** Modify `src/lightfall/core/application.py`

- [ ] **Step 1: Change the Qt application name only**

```bash
sed -i 's/setApplicationName("Lightfall")/setApplicationName("Lightfall")/' src/lightfall/core/application.py
```

- [ ] **Step 2: Verify the two retained `"Lightfall"` literals are still present and untouched**

Run: `grep -rn '"Lightfall"' src/lightfall`
Expected: exactly the `client_id` default in `config/schema.py` (and any Sentry usage that reads the kept project name). The `setApplicationName` line now reads `"Lightfall"`.

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -q -m "feat: display application name as Lightfall (keep Lightfall OIDC client_id)"
```

### Task 7: First-launch data-dir migration `~/lightfall` → `~/lightfall` (TDD)

**Files:**
- Create: `src/lightfall/utils/data_migration.py`
- Modify: `src/lightfall/main.py` (call the migration during bootstrap, before any data-dir access)
- Test: `tests/utils/test_data_migration.py`

> The Task-2 sweep already changed the runtime paths to `~/lightfall` / `~/.lightfall` (in `acquire/plans/user_plans.py`, `plugins/types.py`, `plugins/user_plugins.py`, `ui/dialogs/create_plan_dialog.py`, `utils/git_tracker.py`). This task adds a one-time move so existing user data isn't orphaned.

- [ ] **Step 1: Write the failing test**

```python
# tests/utils/test_data_migration.py
from pathlib import Path
from lightfall.utils.data_migration import migrate_legacy_data_dir


def test_migrates_when_only_legacy_exists(tmp_path):
    home = tmp_path
    legacy = home / "lightfall"
    legacy.mkdir()
    (legacy / "plans").mkdir()
    (legacy / "plans" / "scan.py").write_text("# plan")

    moved = migrate_legacy_data_dir(home)

    assert moved is True
    assert (home / "lightfall" / "plans" / "scan.py").read_text() == "# plan"
    assert not legacy.exists()


def test_no_op_when_new_exists(tmp_path):
    home = tmp_path
    (home / "lightfall").mkdir()
    (home / "lightfall").mkdir()

    moved = migrate_legacy_data_dir(home)

    assert moved is False
    assert (home / "lightfall").exists()  # left untouched; new dir wins


def test_no_op_when_nothing_exists(tmp_path):
    assert migrate_legacy_data_dir(tmp_path) is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `$PY -m pytest tests/utils/test_data_migration.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lightfall.utils.data_migration'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# src/lightfall/utils/data_migration.py
"""One-time migration of the user data directory from the legacy Lightfall name."""
from __future__ import annotations

from pathlib import Path

from loguru import logger


def migrate_legacy_data_dir(home: Path | None = None) -> bool:
    """Move ``~/lightfall`` to ``~/lightfall`` once, if only the legacy dir exists.

    Returns True if a migration was performed, False otherwise. No-ops (and
    leaves both in place) if the new directory already exists.
    """
    home = home or Path.home()
    legacy = home / "lightfall"
    current = home / "lightfall"
    if current.exists() or not legacy.exists():
        return False
    legacy.rename(current)
    logger.info("Migrated legacy data directory {} -> {}", legacy, current)
    return True
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `$PY -m pytest tests/utils/test_data_migration.py -v`
Expected: 3 passed.

- [ ] **Step 5: Call the migration during bootstrap**

In `src/lightfall/main.py`, add the import near the other `lightfall.*` imports and invoke it early in the startup function (before plugins/plans load — i.e. just before the user-plugin load near the `~/lightfall/plugins/` logic):

```python
from lightfall.utils.data_migration import migrate_legacy_data_dir
...
    # One-time rebrand migration of the user data directory.
    migrate_legacy_data_dir()
```

- [ ] **Step 6: Verify the suite still passes and the dotfile path was also renamed**

Run: `$PY -m pytest tests/utils/test_data_migration.py -v && grep -rn '"\.lightfall"\|/ "lightfall"' src/lightfall/plugins/types.py`
Expected: tests pass; `types.py` references `lightfall` / `.lightfall` (not `lightfall`).

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -q -m "feat: migrate ~/lightfall -> ~/lightfall data dir on first launch"
```

### Task 8: Docs, CLAUDE.md, and NCS-as-app fixes (by judgment)

**Files:** `CLAUDE.md`, `docs/**`, `README*`, `features.md`, `plan.md`, `todo.md`

- [ ] **Step 1: Sweep documentation prose**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
lightfall_sweep docs
for f in CLAUDE.md README.md features.md plan.md todo.md; do [ -f "$f" ] && sed -i 's/lightfall/lightfall/g' "$f"; done
```

- [ ] **Step 2: Fix uppercase "Lightfall" in prose (decks/docs use the proper-noun form)**

```bash
grep -rIl --exclude-dir={.git,.venv} 'Lightfall' docs CLAUDE.md README.md features.md plan.md todo.md 2>/dev/null | while read -r f; do sed -i 's/Lightfall/Lightfall/g' "$f"; done
```

- [ ] **Step 3: Fix NCS-as-app misuse by judgment (do NOT blanket-replace NCS)**

Review and edit only where "NCS" names the *application* (it should say Lightfall), while keeping "NCS"/"New Control System" where it means the initiative or GitLab group. Specifically in `CLAUDE.md`:
- The sub-project line "`ncs`: The main UI application" → "`lightfall`: The main UI application".
- Keep the title "# NCS", "New Control System (NCS)", and the group/vision references.

Run after editing: `grep -ni 'ncs' CLAUDE.md`
Expected: remaining `NCS` references are the initiative/group ones; the app is referred to as Lightfall.

- [ ] **Step 4: Drop any acronym expansion**

Run: `grep -rni 'Unified Control Interface Dashboard\|Lightsource Unified' .`
Expected: no matches (remove any that appear).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -q -m "docs: rebrand prose to Lightfall; disambiguate NCS-as-app; drop acronym"
```

### Task 9: Verify the core in place (no dir rename yet)

- [ ] **Step 1: Reinstall the core editable and import it**

```bash
cd /c/Users/rp/PycharmProjects/ncs/ncs
$PY -m pip install -e . -q
$PY -c "import lightfall; print('ok', lightfall.__name__)"
```
Expected: `ok lightfall`.

- [ ] **Step 2: Confirm the entry-point group resolves**

```bash
$PY -c "from importlib.metadata import entry_points as e; print(sorted(ep.name for ep in e(group='lightfall.plugins')))"
```
Expected: a list (may be just core/built-ins until plugins are migrated); **no error**.

- [ ] **Step 3: Run the core test suite**

Run: `$PY -m pytest -q`
Expected: passing (same pass/fail baseline as before the rename; investigate any new failures referencing `lightfall`).

- [ ] **Step 4: Confirm no unexpected residual references**

Run: `grep -rIn 'lightfall' src/lightfall tests pyproject.toml | grep -v '"Lightfall"\|_version.py'`
Expected: empty.

---

## Phase 2 — `ncs-viz-heuristics-tests` — REMOVED FROM SCOPE (2026-06-02)

This directory turned out to be an **orphaned git worktree** of the core `ncs` repo: its
`.git` pointed at a non-existent Linux path and the Windows core repo no longer registered it.
Its committed work is preserved in the core repo branch **`feature/viz-heuristics-tests`**
(tip `56ef05e`). Per Ron's "clean it up" decision, the orphaned Windows directory was moved to
`/c/Users/rp/PycharmProjects/_orphaned-worktrees-backup-20260602/` (reversible). It is **not**
rebranded here. Proper `git worktree prune` should be run on the Linux side where the worktree
metadata lives; the branch itself can be rebranded in that later pass.

---

## Phase 3 — Plugin repos (template applied per repo)

Five repos, identical mechanics. For each, substitute from this table:

| `$DIR` (local + GitLab project) | `$PKG` (snake package) | New dir / project | New `$PKG` |
|---|---|---|---|
| `lightfall-deck` | `lightfall_deck` | `lightfall-deck` | `lightfall_deck` |
| `lightfall-dev-plugins` | `lightfall_dev_plugins` | `lightfall-dev-plugins` | `lightfall_dev_plugins` |
| `lightfall-endstation-cms` | `lightfall_endstation_cms` | `lightfall-endstation-cms` | `lightfall_endstation_cms` |
| `lightfall-endstation-7011` | `lightfall_endstation_7011` | `lightfall-endstation-7011` | `lightfall_endstation_7011` |
| `lightfall-logbook` | (no `src/lightfall_*` pkg; text only) | `lightfall-logbook` | — |

### Task 11–15: Rename each plugin repo

Run these tasks once **per repo** (set `R=lightfall-deck`, etc.). `lightfall-logbook` has no Python package dir — skip the `git mv src/...` step for it.

- [ ] **Step 1: Branch and move the package directory**

```bash
R=lightfall-deck            # <-- change per repo
PKG=lightfall_deck          # <-- change per repo
cd /c/Users/rp/PycharmProjects/ncs/$R
git checkout -b rebrand/lightfall
NEWPKG=$(echo "$PKG" | sed 's/lightfall/lightfall/')
[ -d "src/$PKG" ] && git mv "src/$PKG" "src/$NEWPKG" && git commit -q -m "refactor: git mv src/$PKG -> src/$NEWPKG"
```

- [ ] **Step 2: Sweep source, tests, docs, and pyproject**

```bash
lightfall_sweep src 2>/dev/null; lightfall_sweep tests 2>/dev/null; lightfall_sweep docs 2>/dev/null
sed -i 's/lightfall/lightfall/g' pyproject.toml
[ -f README.md ] && sed -i 's/lightfall/lightfall/g; s/Lightfall/Lightfall/g' README.md
```

- [ ] **Step 3: (ONLY for `lightfall-endstation-7011`) restore the out-of-scope `lightfall-pipelines` names**

```bash
sed -i 's/lightfall-pipelines/lightfall-pipelines/g; s/lightfall_pipelines/lightfall_pipelines/g' pyproject.toml
grep -rln 'lightfall_pipelines\|lightfall-pipelines' src 2>/dev/null | while read -r f; do sed -i 's/lightfall_pipelines/lightfall_pipelines/g; s/lightfall-pipelines/lightfall-pipelines/g' "$f"; done
```

Verify: `grep -n 'pipelines' pyproject.toml` → dependency reads `lightfall-pipelines`, entry-point group reads `lightfall_pipelines.pipeline`; the plugin's own group reads `lightfall.plugins`.

- [ ] **Step 4: Verify the manifest**

Run: `grep -nE 'name =|dependencies|entry-points|version-file|packages =' pyproject.toml`
Expected: `name = "lightfall-<x>"`; `dependencies` include `"lightfall"` (NOT `"lightfall"`); entry-point group `[project.entry-points."lightfall.plugins"]` with name `lightfall_<x>`; `version-file`/`packages` reference `src/lightfall_<x>`.

- [ ] **Step 5: Verify no residual (except allowed pipelines in 7011)**

Run: `grep -rIn 'lightfall' . --include='*.py' --include='*.toml' --include='*.md' | grep -v '\.venv\|_version.py\|lightfall-pipelines\|lightfall_pipelines'`
Expected: empty.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -q -m "refactor: rebrand $R -> ${R/lightfall/lightfall} (lightfall -> lightfall)"
```

---

## Phase 4 — Decks (`lightfall-pitch`, `lightfall-present`)

Working dirs: `/c/Users/rp/workspace/lightfall-pitch`, `/c/Users/rp/workspace/lightfall-present`.

### Task 16: Swap deck logos and rebrand text

Run once per deck (`D=lightfall-pitch`, then `D=lightfall-present`):

- [ ] **Step 1: Branch and replace the logo with the Lightfall wordmark**

```bash
D=lightfall-pitch          # <-- change per deck
cd /c/Users/rp/workspace/$D
git checkout -b rebrand/lightfall
cp /c/Users/rp/Downloads/logo.png logo.png
```

- [ ] **Step 2: Rebrand text and drop the acronym expansion**

```bash
for f in index.html README.md rebuttal.md; do [ -f "$f" ] && sed -i 's/Lightfall/Lightfall/g; s/lightfall/lightfall/g' "$f"; done
[ -d docs ] && lightfall_sweep docs
grep -rni 'Unified Control Interface Dashboard\|Lightsource Unified' . && echo "REMOVE these expansions" || echo "no acronym expansion"
```

- [ ] **Step 3: Verify and commit**

Run: `grep -rni 'lightfall' . | grep -v '\.git'`
Expected: empty (or only intentional). Then:

```bash
git add -A && git commit -q -m "rebrand: Lightfall logo + text for $D; drop acronym"
```

---

## Phase 5 — Paper (`lightfall-publication`)

Working dir: `/c/Users/rp/workspace/lightfall-publication`. Draft → rename outright.

### Task 17: Rebrand the paper

- [ ] **Step 1: Branch and sweep all LaTeX/bib/docs**

```bash
cd /c/Users/rp/workspace/lightfall-publication
git checkout -b rebrand/lightfall
grep -rIl --exclude-dir=.git 'Lightfall\|lightfall' . | grep -vE '\.png$|\.pdf$|\.jpg$' | while read -r f; do sed -i 's/Lightfall/Lightfall/g; s/lightfall/lightfall/g' "$f"; done
```

- [ ] **Step 2: Drop the acronym expansion in prose**

Run: `grep -rni 'Unified Control Interface Dashboard\|Lightsource Unified\|\\\\ac' content *.tex`
Expected: remove the expansion wording / any `\acro{Lightfall}{...}` definition; the name stands alone as "Lightfall".

- [ ] **Step 3: Build the paper to confirm it still compiles**

Run: `make 2>&1 | tail -20` (or the documented build target)
Expected: PDF builds with no new errors; "Lightfall" appears in title/abstract.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -q -m "rebrand: Lightfall -> Lightfall throughout the paper; drop acronym"
```

---

## Phase 6 — Local directory renames + venv reinstall + full verification

### Task 18: Rename local directories and rewire the shared venv

- [ ] **Step 1: Rename code directories under `~/PycharmProjects/ncs`**

```bash
cd /c/Users/rp/PycharmProjects/ncs
for d in ncs lightfall-deck lightfall-dev-plugins lightfall-endstation-cms lightfall-endstation-7011 lightfall-logbook; do
  case "$d" in ncs) new=lightfall;; *) new=${d/lightfall/lightfall};; esac
  [ -d "$d" ] && mv "$d" "$new" && echo "renamed $d -> $new"
done
```
(`ncs-viz-heuristics-tests` and `epics-pyside` keep their directory names.)

- [ ] **Step 2: Rename the workspace repos**

```bash
cd /c/Users/rp/workspace
for d in lightfall-publication lightfall-pitch lightfall-present; do mv "$d" "${d/lightfall/lightfall}"; done
```

- [ ] **Step 3: Reinstall all editable packages into the shared venv**

```bash
PY=/c/Users/rp/PycharmProjects/ncs/.venv/Scripts/python.exe
cd /c/Users/rp/PycharmProjects/ncs
for p in lightfall lightfall-deck lightfall-dev-plugins lightfall-endstation-cms lightfall-endstation-7011 lightfall-logbook; do
  [ -f "$p/pyproject.toml" ] && $PY -m pip install -e "$p" -q && echo "installed $p"
done
```

- [ ] **Step 4: Full cross-repo verification**

```bash
$PY -c "import lightfall; print('core ok')"
$PY -c "from importlib.metadata import entry_points as e; print(sorted(ep.name for ep in e(group='lightfall.plugins')))"
```
Expected: `core ok`; the entry-point list now includes every migrated plugin (`lightfall_deck`, `lightfall_dev_plugins`, `lightfall_endstation_cms`, `lightfall_endstation_7011`). **This is the plugin-discovery gate from the spec — it must list all plugins.**

- [ ] **Step 5: Run every repo's test suite**

```bash
for p in lightfall lightfall-deck lightfall-dev-plugins lightfall-endstation-cms lightfall-endstation-7011 lightfall-logbook; do
  [ -d "$p" ] && (cd "$p" && echo "== $p ==" && $PY -m pytest -q 2>&1 | tail -3)
done
```
Expected: each suite at its pre-rename baseline; no new failures referencing `lightfall`.

- [ ] **Step 6: Smoke-launch the app**

Run: `$PY -m lightfall.main --help` (or the `lightfall-gui` entry point)
Expected: launches / prints help; window+app name read "Lightfall".

### Task 19: Update the global project quick-index

**Files:** Modify `C:\Users\rp\.claude\CLAUDE.md`

- [ ] **Step 1: Update the path index entries**

Change the quick-index lines so `lightfall-publication`, `lightfall-pitch`, `lightfall-present` (workspace) and the `ncs` PycharmProjects entry reflect the new names (`lightfall-*`; main app dir `ncs` → `lightfall`). Keep "NCS" where it names the umbrella/group. (This file is outside the repos; edit directly, no commit.)

- [ ] **Step 2: Verify**

Run: `grep -ni 'lightfall' /c/Users/rp/.claude/CLAUDE.md`
Expected: no stale `lightfall-*` paths (acronyms/initiative refs to NCS remain).

---

## Phase 7 — GitLab server-side renames + remote rewiring

> External, hard-to-reverse. Do this **only after** Phases 1–6 verify green and branches are pushed-ready. Uses the ALS GitLab token from `TOOLS.md` over the SOCKS proxy (`localhost:1080`). Never echo the token into logs.

### Task 20: Rename GitLab projects via API and update local remotes

- [ ] **Step 1: Read the token and confirm proxy reachability**

Read `C:\Users\rp\workspace\TOOLS.md` for the `glpat-*` GitLab token (just-in-time; do not print it). Then, with `curl --socks5-hostname localhost:1080` and header `PRIVATE-TOKEN: <token>`, list the group's projects to capture each project `id` and current `path`:

```bash
curl -s --socks5-hostname localhost:1080 -H "PRIVATE-TOKEN: $GLPAT" \
  "https://git.als.lbl.gov/api/v4/groups/ncs/projects?per_page=100" \
  | $PY -c "import sys,json; [print(p['id'], p['path']) for p in json.load(sys.stdin)]"
```
Expected: a list mapping ids → paths for `ncs`, `lightfall-deck`, `lightfall-dev-plugins`, `lightfall-endstation-cms`, `lightfall-endstation-7011`, `lightfall-logbook`, `lightfall-publication`, `lightfall-pitch`, `lightfall-present`.

- [ ] **Step 2: Rename each project (path + name)**

For each id, PATCH the new path/name (group `ncs` unchanged):

```bash
# example for one project; repeat per id with the right NEWPATH
curl -s --socks5-hostname localhost:1080 -X PUT -H "PRIVATE-TOKEN: $GLPAT" \
  "https://git.als.lbl.gov/api/v4/projects/<id>" \
  --data-urlencode "name=<NEWPATH>" --data-urlencode "path=<NEWPATH>" >/dev/null && echo "renamed <id> -> <NEWPATH>"
```
Mapping: `ncs`→`lightfall`, `lightfall-deck`→`lightfall-deck`, `lightfall-dev-plugins`→`lightfall-dev-plugins`, `lightfall-endstation-cms`→`lightfall-endstation-cms`, `lightfall-endstation-7011`→`lightfall-endstation-7011`, `lightfall-logbook`→`lightfall-logbook`, `lightfall-publication`→`lightfall-publication`, `lightfall-pitch`→`lightfall-pitch`, `lightfall-present`→`lightfall-present`. (`ncs-viz-heuristics-tests` is **not** renamed.)

- [ ] **Step 3: Update local remotes**

```bash
# code repos (origin)
declare -A M=( [lightfall]=lightfall [lightfall-deck]=lightfall-deck [lightfall-dev-plugins]=lightfall-dev-plugins \
  [lightfall-endstation-cms]=lightfall-endstation-cms [lightfall-endstation-7011]=lightfall-endstation-7011 [lightfall-logbook]=lightfall-logbook )
cd /c/Users/rp/PycharmProjects/ncs
for d in "${!M[@]}"; do
  [ -d "$d/.git" ] && git -C "$d" remote set-url origin "https://git.als.lbl.gov/ncs/${M[$d]}.git" 2>/dev/null \
    || git -C "$d" remote add origin "https://git.als.lbl.gov/ncs/${M[$d]}.git" 2>/dev/null
done
# core app used remote name 'upstream' with an odd trailing-slash URL — normalize to origin:
git -C lightfall remote remove upstream 2>/dev/null; git -C lightfall remote add origin "https://git.als.lbl.gov/ncs/lightfall.git" 2>/dev/null
git -C lightfall remote set-url origin "https://git.als.lbl.gov/ncs/lightfall.git"
```

For `lightfall-deck`/`lightfall-deck` and `lightfall-endstation-cms`/`lightfall-endstation-cms` (which had **no** remote): the `git remote add origin` above covers them — confirm the GitLab project actually exists from Step 1 first.

- [ ] **Step 4: Update the workspace repo remotes (preserve the embedded token on publication)**

```bash
cd /c/Users/rp/workspace
git -C lightfall-pitch   remote set-url origin "https://git.als.lbl.gov/ncs/lightfall-pitch.git"
git -C lightfall-present remote set-url origin "https://git.als.lbl.gov/ncs/lightfall-present.git"
# publication: keep oauth2:<token>@ userinfo, change only the path
OLD=$(git -C lightfall-publication remote get-url origin)
NEW=$(echo "$OLD" | sed 's#/ncs/lightfall-publication\.git#/ncs/lightfall-publication.git#')
git -C lightfall-publication remote set-url origin "$NEW"
```

- [ ] **Step 5: Push all branches**

```bash
for d in /c/Users/rp/PycharmProjects/ncs/lightfall* /c/Users/rp/PycharmProjects/ncs/ncs-viz-heuristics-tests /c/Users/rp/workspace/lightfall-*; do
  [ -d "$d/.git" ] && git -C "$d" push -u origin rebrand/lightfall 2>&1 | tail -1
done
```
Expected: each push succeeds against the renamed remote.

- [ ] **Step 6: Verify remotes resolve and CI/badge paths are clean**

Run: `for d in /c/Users/rp/PycharmProjects/ncs/lightfall* /c/Users/rp/workspace/lightfall-*; do git -C "$d" remote get-url origin; done`
Expected: all URLs under `ncs/lightfall*`. Then grep each repo's `.gitlab-ci.yml` / README badges for stale `lightfall` paths and fix in a follow-up commit if present.

---

## Phase 8 — Finishing touches

### Task 21: Update MemoryGraph and notify

- [ ] **Step 1: Update MemoryGraph project memories**

Update the `type=project` memories for the Lightfall/NCS projects to reflect the Lightfall name and new repo paths (search `tags=["project"]`, then `update_memory`). Add a `general` memory noting the rebrand date (2026-06-02) and that NCS remains the group name. Keep token references in `TOOLS.md` only.

- [ ] **Step 2: Final residual audit across everything**

```bash
grep -rIn 'Lightfall\|lightfall' /c/Users/rp/PycharmProjects/ncs/lightfall* /c/Users/rp/workspace/lightfall-* \
  --include='*.py' --include='*.toml' --include='*.md' --include='*.tex' \
  | grep -v '\.venv\|_version.py\|lightfall-pipelines\|lightfall_pipelines\|"Lightfall"' | head -40
```
Expected: empty, or only deliberately retained items (the two `"Lightfall"` literals, the `lightfall-pipelines` framework).

---

## Self-Review Notes (coverage vs spec)

- §2 scope (all repos incl. the two no-remote ones, viz-tests project name kept, epics-pyside excluded) → Phases 1–7 + remote add in Task 20.3.
- §3 naming map (snake/kebab/dotted/uppercase, app id, icons, data dir) → sweep helper + Tasks 3, 5, 6, 7.
- §4 plugin-discovery interface → Task 4 + the Task 18.4 gate.
- §5 sequencing → Phases 1→7 order; dir rename + reinstall in Phase 6 precedes remote rename in Phase 7.
- §6 GitLab procedure (token, SOCKS, publication token preservation, no-remote repos, `ncs` upstream URL) → Task 20.
- §7 risks: OIDC/Sentry retained literals (Conventions + Tasks 2.2/3.2/6.2), data-dir migration (Task 7), github-URL strings (Task 3 sweeps `[project.urls]` to lightfall paths), lightfall-pipelines exclusion (Task 11–15 Step 3), CI/badges (Task 20.6), briefcase app id (Task 3/5), generated `_version.py` excluded everywhere.
- §8 verification checklist → Tasks 9, 18.4–18.6, 20.6, 21.2.
