# Lightfall → Lightfall Rebrand — Design

**Date:** 2026-06-02
**Author:** Ron (with Ayaka)
**Status:** Draft for review

## 1. Summary

Rebrand the product currently named **Lightfall** to **Lightfall** across all of its
repositories. This is a *full* rename: user-facing text, documentation, Python
package/distribution names, import paths, entry-point groups, directory names, and
GitLab project paths. The acronym expansion ("Lightsource Unified Control Interface
Dashboard") is dropped — *Lightfall* is just a name.

**NCS** ("New Control System") is **not** being renamed. It remains the name of the
GitLab **group** (`git.als.lbl.gov/ncs`) and of the broader initiative. The rebrand
*also* fixes the places where "NCS" was incorrectly used to mean the application
itself — most notably the project `ncs/ncs`, whose Python package is literally
`lightfall`. That project becomes `ncs/lightfall`.

### Decisions locked in (from brainstorming)

| Decision | Choice |
|---|---|
| Rename depth | **Full** — code identifiers, directories, and git remotes |
| NCS | Stays as group/initiative name; fix NCS-as-app misuse |
| Acronym | **Drop** the expansion entirely |
| Paper (`lightfall-publication`) | Still draft → **rename outright** |
| GitLab remotes | Rename **server-side via GitLab API** (SOCKS proxy, token from TOOLS.md) |
| Logo | Use the provided `~/Downloads/logo.png` (the only text/wordmark version) as-is; replace existing Lightfall wordmark logos with it; no variant generation; keep non-text decorative icons (renaming only their filenames) |
| Approach | **A — atomic coordinated sweep** |

## 2. Scope

### In scope (repos under GitLab group `ncs`)

| Local path | Distribution / package | GitLab project | New project |
|---|---|---|---|
| `~/PycharmProjects/ncs/ncs` | `lightfall` (pkg `lightfall`) | `ncs/ncs` | `ncs/lightfall` |
| `~/PycharmProjects/ncs/lightfall-deck` | `lightfall-deck` / `lightfall_deck` | `ncs/lightfall-deck`¹ | `ncs/lightfall-deck` |
| `~/PycharmProjects/ncs/lightfall-dev-plugins` | `lightfall-dev-plugins` / `lightfall_dev_plugins` | `ncs/lightfall-dev-plugins` | `ncs/lightfall-dev-plugins` |
| `~/PycharmProjects/ncs/lightfall-endstation-cms` | `lightfall-endstation-cms` / `lightfall_endstation_cms` | `ncs/lightfall-endstation-cms`¹ | `ncs/lightfall-endstation-cms` |
| `~/PycharmProjects/ncs/lightfall-endstation-7011` | `lightfall-endstation-7011` / `lightfall_endstation_7011` | `ncs/lightfall-endstation-7011` | `ncs/lightfall-endstation-7011` |
| `~/PycharmProjects/ncs/lightfall-logbook` | `lightfall-logbook` | `ncs/lightfall-logbook` | `ncs/lightfall-logbook` |
| `~/PycharmProjects/ncs/ncs-viz-heuristics-tests` | `lightfall` (mirror of core, pkg `lightfall`) | `ncs/ncs-viz-heuristics-tests` | `ncs/ncs-viz-heuristics-tests` (project name **unchanged**; package renamed) |
| `~/workspace/lightfall-publication` | LaTeX paper | `ncs/lightfall-publication`² | `ncs/lightfall-publication` |
| `~/workspace/lightfall-pitch` | reveal.js deck | `ncs/lightfall-pitch` | `ncs/lightfall-pitch` |
| `~/workspace/lightfall-present` | reveal.js deck | `ncs/lightfall-present` | `ncs/lightfall-present` |

¹ No local git remote configured — verify the GitLab project exists, then (re)add the remote.
² Remote URL embeds a `glpat-…` token — preserve the token, change only the path. Never echo the token.

### Out of scope

- **`epics-pyside`** — not Lightfall-named; not selected. Leave as-is (note any stray display-string Lightfall refs but do not change in this pass).
- **`lightfall-pipelines`** — referenced as a dependency of `lightfall-endstation-7011` and as the entry-point group `lightfall_pipelines.pipeline`, but **not checked out locally**. It is a separate framework. Leave `lightfall-pipelines` / `lightfall_pipelines.pipeline` names intact; track as a follow-up.
- Creating any GitHub mirror (see §7 risk on stale `github.com/als-computing/lightfall` URLs).

## 3. Naming map

Apply case-aware, context-aware replacement. Variants:

| Old | New | Where |
|---|---|---|
| `Lightfall` | `Lightfall` | Prose, titles, `formal_name`, `setApplicationName`, Sentry `project_name`, README/docs |
| `lightfall` | `lightfall` | Python package, distribution name, import paths, `known-first-party`, briefcase app key, version-file path, hatch `packages` |
| `lightfall.plugins` | `lightfall.plugins` | **Plugin-discovery entry-point group** (core reader + every plugin's registration) |
| `lightfall_deck`, `lightfall_dev_plugins`, … | `lightfall_deck`, … | Snake-case package dirs, imports, entry-point *names* |
| `lightfall-deck`, `lightfall-dev-plugins`, … | `lightfall-deck`, … | Kebab distribution names, directory names, GitLab project paths |
| `gov.lbl.als.lightfall` | `gov.lbl.als.lightfall` | Linux desktop file + app identifier |
| `lightfall.icns/.ico/.png`, `lightfall.desktop` | `lightfall.*` | Icon/resource filenames (image content unchanged) |
| `~/lightfall/` | `~/lightfall/` | User data dir (`Path.home() / "lightfall"`) — see risk §7 |

**Do not touch:** `.venv/`, `build/`, `.mypy_cache/`, generated `_version.py`, embedded git tokens, and the out-of-scope `lightfall-pipelines` / `lightfall_pipelines.pipeline` names.

## 4. The critical interface — plugin discovery

The core discovers plugins through the **`lightfall.plugins` entry-point group**, and each
plugin both **declares `dependencies = ["lightfall"]`** and **registers into that group**.
These two couplings must flip in lockstep with the core, or plugin discovery silently
breaks. Approach A guarantees this by renaming core and all plugins within one
coordinated sweep (single branch per repo, reinstalled together, verified together).

Verification gate: after the sweep,
`importlib.metadata.entry_points(group="lightfall.plugins")` must list every plugin,
and no `lightfall.plugins` group may remain.

## 5. Sequencing (Approach A)

Work on a dedicated branch in each repo (`rebrand/lightfall`).

1. **Core (`ncs/ncs`)** — `git mv src/lightfall src/lightfall`; update `pyproject.toml`
   (`name`, `[project.scripts]` `lightfall`→`lightfall`, `lightfall-exporter`→`lightfall-exporter`,
   `[project.gui-scripts]` `lightfall-gui`→`lightfall-gui`, `version-file`, hatch `packages`,
   `force-include` logo path, `known-first-party`, `[tool.briefcase.app.lightfall]`→`…lightfall`,
   `formal_name`, `icon`, `project_name`, `[project.urls]`); change the plugin-discovery
   code to read `lightfall.plugins`; rename resources (`lightfall.icns/.ico/.png`,
   `gov.lbl.als.lightfall.desktop`, `ui/resources/logo.png`); fix display strings
   (`setApplicationName`, etc.); update data path `~/lightfall`→`~/lightfall`; docs + CLAUDE.md.
2. **Each plugin repo** (`lightfall-deck`, `lightfall-dev-plugins`, `lightfall-endstation-cms`,
   `lightfall-endstation-7011`, `lightfall-logbook`) — `git mv` the package dir; update
   `pyproject.toml` (`name` kebab, `dependencies` `lightfall`→`lightfall`, entry-point group
   `"lightfall.plugins"`, entry name `lightfall_x`, `version-file`, `packages`, urls,
   scripts); rewrite imports (`from lightfall` → `from lightfall`, `import lightfall_x` →
   `lightfall_x`); docs + display strings. **`lightfall-endstation-7011`**: keep its
   `lightfall-pipelines` dependency and `lightfall_pipelines.pipeline` entry point untouched.
3. **`ncs-viz-heuristics-tests`** — mirror of core: same package rename + pyproject edits;
   project name stays.
4. **Decks (`lightfall-pitch`, `lightfall-present`)** — replace `logo.png` with the new Lightfall
   wordmark; update `index.html` / `docs` / `README` text; drop the acronym expansion.
5. **Paper (`lightfall-publication`)** — rename Lightfall→Lightfall across `.tex`, `references.bib`,
   `README`, `Makefile`, `TODO`, `figures/README`, `resources/`; drop the
   "Lightsource Unified Control Interface Dashboard" expansion. Outright (draft).
6. **Reinstall & verify** — reinstall editable packages into the shared `.venv` against the
   renamed directories; run all test suites; smoke-launch the app; build the paper/decks.
7. **Commit** each repo on its branch.
8. **Rename local directories** under `~/PycharmProjects/ncs` (`lightfall-*` → `lightfall-*`,
   and `ncs/ncs` → `ncs/lightfall`); re-point the shared venv editable installs.
9. **GitLab API renames** (§6); update local remotes; push branches.
10. **Finishing touches** — update the project quick-index in `~/.claude/CLAUDE.md`
    (`lightfall-*` paths → `lightfall-*`) and MemoryGraph project memories.

## 6. GitLab project-rename procedure

For each in-scope project, over the SOCKS proxy (`localhost:1080`, required for `*.lbl.gov`),
using the ALS GitLab token from `TOOLS.md`:

```
PATCH https://git.als.lbl.gov/api/v4/projects/<id>
  body: { "name": "lightfall-…", "path": "lightfall-…" }
```

GitLab auto-creates a redirect from the old path. Then locally:
`git remote set-url origin https://git.als.lbl.gov/ncs/lightfall-<x>.git`.

Special cases:
- **`lightfall-publication`**: preserve the embedded `glpat-…` token in the URL; change only the path.
- **`ncs/ncs` → `ncs/lightfall`**: the local repo uses remote name `upstream` with a
  trailing-slash URL (`…/ncs.git/`) — fix the name/URL while re-pointing.
- **`lightfall-deck`, `lightfall-endstation-cms`**: no local remote — confirm the GitLab project
  exists (and its current path) before renaming, then add `origin`.

## 7. Risks & mitigations

1. **Plugin-discovery break** — flipping `lightfall.plugins`→`lightfall.plugins` must be atomic
   across core + plugins. *Mitigation:* Approach A + the §4 verification gate.
2. **OIDC `client_id` default `"Lightfall"`** (`config/schema.py`) — **RESOLVED: keep as `"Lightfall"`.**
   Not exposed anywhere user-facing; leaving it avoids any IdP-coordination risk. This is an
   *intentional retained* `"Lightfall"` literal (allow-listed in the residual-grep check).
3. **Sentry `project_name = "Lightfall"`** — **RESOLVED: keep as `"Lightfall"`.** Not exposed; leaving
   it avoids splitting Sentry event grouping. Intentional retained literal (allow-listed).
4. **User data path `~/lightfall/` → `~/lightfall/`** — **RESOLVED: add a one-time first-launch
   migration.** On startup, if `~/lightfall` does not exist but `~/lightfall` does, move/rename it
   (and log the migration). Then use `~/lightfall` going forward.
5. **Stale `github.com/als-computing/lightfall` URLs** in pyproject `[project.urls]` — the real
   home is the GitLab `ncs` group. *Mitigation:* point URLs at the GitLab `ncs/lightfall`
   paths; do **not** create a GitHub repo. Flag the discrepancy.
6. **`lightfall-pipelines` not cloned** — leave intact; `lightfall-endstation-7011` keeps depending
   on it. Follow-up item.
7. **GitLab rename side-effects** — CI configs, README badges, GitLab Pages URLs may
   reference old paths. *Mitigation:* grep each repo's `.gitlab-ci.yml`/README badges and
   update; redirects cover external clones temporarily.
8. **Briefcase app identifier change** (`gov.lbl.als.lightfall` → `gov.lbl.als.lightfall`) —
   installed builds treat it as a new app. Acceptable pre-release.
9. **Generated `_version.py`** — produced by hatch-vcs; update the path in pyproject, never
   hand-edit the file.

## 8. Verification checklist

- [ ] `pip install -e` succeeds for every package in the shared `.venv`.
- [ ] `python -c "import lightfall"` works; `import lightfall` fails (no stray package).
- [ ] `entry_points(group="lightfall.plugins")` lists all five plugins; no `lightfall.plugins` remains.
- [ ] `pytest` passes in each repo.
- [ ] App launches via `lightfall-gui`; window/app name reads "Lightfall".
- [ ] Residual-reference grep (`-i lightfall`, excluding `.venv`, history, generated files) returns
      only intentional matches: `lightfall-pipelines`/`lightfall_pipelines.pipeline` (out of scope),
      the OIDC `client_id="Lightfall"` default, and the Sentry `project_name="Lightfall"`.
- [ ] First-launch data-dir migration moves `~/lightfall` → `~/lightfall` when only the old exists.
- [ ] Decks render with the new logo; paper builds via `make`.
- [ ] All local remotes resolve to `ncs/lightfall-*`; pushes succeed.

## 9. Resolved decisions (was: open items)

- **OIDC `client_id`** (risk #2): **keep `"Lightfall"`** — not exposed, no IdP coordination needed.
- **Sentry `project_name`** (risk #3): **keep `"Lightfall"`** — not exposed.
- **App data dir** (risk #4): **add a first-launch `~/lightfall` → `~/lightfall` migration.**
