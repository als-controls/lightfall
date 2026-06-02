# LUCID → Lightfall Rebrand — Design

**Date:** 2026-06-02
**Author:** Ron (with Ayaka)
**Status:** Draft for review

## 1. Summary

Rebrand the product currently named **LUCID** to **Lightfall** across all of its
repositories. This is a *full* rename: user-facing text, documentation, Python
package/distribution names, import paths, entry-point groups, directory names, and
GitLab project paths. The acronym expansion ("Lightsource Unified Control Interface
Dashboard") is dropped — *Lightfall* is just a name.

**NCS** ("New Control System") is **not** being renamed. It remains the name of the
GitLab **group** (`git.als.lbl.gov/ncs`) and of the broader initiative. The rebrand
*also* fixes the places where "NCS" was incorrectly used to mean the application
itself — most notably the project `ncs/ncs`, whose Python package is literally
`lucid`. That project becomes `ncs/lightfall`.

### Decisions locked in (from brainstorming)

| Decision | Choice |
|---|---|
| Rename depth | **Full** — code identifiers, directories, and git remotes |
| NCS | Stays as group/initiative name; fix NCS-as-app misuse |
| Acronym | **Drop** the expansion entirely |
| Paper (`lucid-publication`) | Still draft → **rename outright** |
| GitLab remotes | Rename **server-side via GitLab API** (SOCKS proxy, token from TOOLS.md) |
| Logo | Use the provided `~/Downloads/logo.png` (the only text/wordmark version) as-is; replace existing LUCID wordmark logos with it; no variant generation; keep non-text decorative icons (renaming only their filenames) |
| Approach | **A — atomic coordinated sweep** |

## 2. Scope

### In scope (repos under GitLab group `ncs`)

| Local path | Distribution / package | GitLab project | New project |
|---|---|---|---|
| `~/PycharmProjects/ncs/ncs` | `lucid` (pkg `lucid`) | `ncs/ncs` | `ncs/lightfall` |
| `~/PycharmProjects/ncs/lucid-deck` | `lucid-deck` / `lucid_deck` | `ncs/lucid-deck`¹ | `ncs/lightfall-deck` |
| `~/PycharmProjects/ncs/lucid-dev-plugins` | `lucid-dev-plugins` / `lucid_dev_plugins` | `ncs/lucid-dev-plugins` | `ncs/lightfall-dev-plugins` |
| `~/PycharmProjects/ncs/lucid-endstation-cms` | `lucid-endstation-cms` / `lucid_endstation_cms` | `ncs/lucid-endstation-cms`¹ | `ncs/lightfall-endstation-cms` |
| `~/PycharmProjects/ncs/lucid-endstation-7011` | `lucid-endstation-7011` / `lucid_endstation_7011` | `ncs/lucid-endstation-7011` | `ncs/lightfall-endstation-7011` |
| `~/PycharmProjects/ncs/lucid-logbook` | `lucid-logbook` | `ncs/lucid-logbook` | `ncs/lightfall-logbook` |
| `~/PycharmProjects/ncs/ncs-viz-heuristics-tests` | `lucid` (mirror of core, pkg `lucid`) | `ncs/ncs-viz-heuristics-tests` | `ncs/ncs-viz-heuristics-tests` (project name **unchanged**; package renamed) |
| `~/workspace/lucid-publication` | LaTeX paper | `ncs/lucid-publication`² | `ncs/lightfall-publication` |
| `~/workspace/lucid-pitch` | reveal.js deck | `ncs/lucid-pitch` | `ncs/lightfall-pitch` |
| `~/workspace/lucid-present` | reveal.js deck | `ncs/lucid-present` | `ncs/lightfall-present` |

¹ No local git remote configured — verify the GitLab project exists, then (re)add the remote.
² Remote URL embeds a `glpat-…` token — preserve the token, change only the path. Never echo the token.

### Out of scope

- **`epics-pyside`** — not LUCID-named; not selected. Leave as-is (note any stray display-string LUCID refs but do not change in this pass).
- **`lucid-pipelines`** — referenced as a dependency of `lucid-endstation-7011` and as the entry-point group `lucid_pipelines.pipeline`, but **not checked out locally**. It is a separate framework. Leave `lucid-pipelines` / `lucid_pipelines.pipeline` names intact; track as a follow-up.
- Creating any GitHub mirror (see §7 risk on stale `github.com/als-computing/lucid` URLs).

## 3. Naming map

Apply case-aware, context-aware replacement. Variants:

| Old | New | Where |
|---|---|---|
| `LUCID` | `Lightfall` | Prose, titles, `formal_name`, `setApplicationName`, Sentry `project_name`, README/docs |
| `lucid` | `lightfall` | Python package, distribution name, import paths, `known-first-party`, briefcase app key, version-file path, hatch `packages` |
| `lucid.plugins` | `lightfall.plugins` | **Plugin-discovery entry-point group** (core reader + every plugin's registration) |
| `lucid_deck`, `lucid_dev_plugins`, … | `lightfall_deck`, … | Snake-case package dirs, imports, entry-point *names* |
| `lucid-deck`, `lucid-dev-plugins`, … | `lightfall-deck`, … | Kebab distribution names, directory names, GitLab project paths |
| `gov.lbl.als.lucid` | `gov.lbl.als.lightfall` | Linux desktop file + app identifier |
| `lucid.icns/.ico/.png`, `lucid.desktop` | `lightfall.*` | Icon/resource filenames (image content unchanged) |
| `~/lucid/` | `~/lightfall/` | User data dir (`Path.home() / "lucid"`) — see risk §7 |

**Do not touch:** `.venv/`, `build/`, `.mypy_cache/`, generated `_version.py`, embedded git tokens, and the out-of-scope `lucid-pipelines` / `lucid_pipelines.pipeline` names.

## 4. The critical interface — plugin discovery

The core discovers plugins through the **`lucid.plugins` entry-point group**, and each
plugin both **declares `dependencies = ["lucid"]`** and **registers into that group**.
These two couplings must flip in lockstep with the core, or plugin discovery silently
breaks. Approach A guarantees this by renaming core and all plugins within one
coordinated sweep (single branch per repo, reinstalled together, verified together).

Verification gate: after the sweep,
`importlib.metadata.entry_points(group="lightfall.plugins")` must list every plugin,
and no `lucid.plugins` group may remain.

## 5. Sequencing (Approach A)

Work on a dedicated branch in each repo (`rebrand/lightfall`).

1. **Core (`ncs/ncs`)** — `git mv src/lucid src/lightfall`; update `pyproject.toml`
   (`name`, `[project.scripts]` `lucid`→`lightfall`, `lucid-exporter`→`lightfall-exporter`,
   `[project.gui-scripts]` `lucid-gui`→`lightfall-gui`, `version-file`, hatch `packages`,
   `force-include` logo path, `known-first-party`, `[tool.briefcase.app.lucid]`→`…lightfall`,
   `formal_name`, `icon`, `project_name`, `[project.urls]`); change the plugin-discovery
   code to read `lightfall.plugins`; rename resources (`lucid.icns/.ico/.png`,
   `gov.lbl.als.lucid.desktop`, `ui/resources/logo.png`); fix display strings
   (`setApplicationName`, etc.); update data path `~/lucid`→`~/lightfall`; docs + CLAUDE.md.
2. **Each plugin repo** (`lucid-deck`, `lucid-dev-plugins`, `lucid-endstation-cms`,
   `lucid-endstation-7011`, `lucid-logbook`) — `git mv` the package dir; update
   `pyproject.toml` (`name` kebab, `dependencies` `lucid`→`lightfall`, entry-point group
   `"lightfall.plugins"`, entry name `lightfall_x`, `version-file`, `packages`, urls,
   scripts); rewrite imports (`from lucid` → `from lightfall`, `import lucid_x` →
   `lightfall_x`); docs + display strings. **`lucid-endstation-7011`**: keep its
   `lucid-pipelines` dependency and `lucid_pipelines.pipeline` entry point untouched.
3. **`ncs-viz-heuristics-tests`** — mirror of core: same package rename + pyproject edits;
   project name stays.
4. **Decks (`lucid-pitch`, `lucid-present`)** — replace `logo.png` with the new Lightfall
   wordmark; update `index.html` / `docs` / `README` text; drop the acronym expansion.
5. **Paper (`lucid-publication`)** — rename LUCID→Lightfall across `.tex`, `references.bib`,
   `README`, `Makefile`, `TODO`, `figures/README`, `resources/`; drop the
   "Lightsource Unified Control Interface Dashboard" expansion. Outright (draft).
6. **Reinstall & verify** — reinstall editable packages into the shared `.venv` against the
   renamed directories; run all test suites; smoke-launch the app; build the paper/decks.
7. **Commit** each repo on its branch.
8. **Rename local directories** under `~/PycharmProjects/ncs` (`lucid-*` → `lightfall-*`,
   and `ncs/ncs` → `ncs/lightfall`); re-point the shared venv editable installs.
9. **GitLab API renames** (§6); update local remotes; push branches.
10. **Finishing touches** — update the project quick-index in `~/.claude/CLAUDE.md`
    (`lucid-*` paths → `lightfall-*`) and MemoryGraph project memories.

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
- **`lucid-publication`**: preserve the embedded `glpat-…` token in the URL; change only the path.
- **`ncs/ncs` → `ncs/lightfall`**: the local repo uses remote name `upstream` with a
  trailing-slash URL (`…/ncs.git/`) — fix the name/URL while re-pointing.
- **`lucid-deck`, `lucid-endstation-cms`**: no local remote — confirm the GitLab project
  exists (and its current path) before renaming, then add `origin`.

## 7. Risks & mitigations

1. **Plugin-discovery break** — flipping `lucid.plugins`→`lightfall.plugins` must be atomic
   across core + plugins. *Mitigation:* Approach A + the §4 verification gate.
2. **OIDC `client_id` default `"LUCID"`** (`config/schema.py`) — **RESOLVED: keep as `"LUCID"`.**
   Not exposed anywhere user-facing; leaving it avoids any IdP-coordination risk. This is an
   *intentional retained* `"LUCID"` literal (allow-listed in the residual-grep check).
3. **Sentry `project_name = "LUCID"`** — **RESOLVED: keep as `"LUCID"`.** Not exposed; leaving
   it avoids splitting Sentry event grouping. Intentional retained literal (allow-listed).
4. **User data path `~/lucid/` → `~/lightfall/`** — **RESOLVED: add a one-time first-launch
   migration.** On startup, if `~/lightfall` does not exist but `~/lucid` does, move/rename it
   (and log the migration). Then use `~/lightfall` going forward.
5. **Stale `github.com/als-computing/lucid` URLs** in pyproject `[project.urls]` — the real
   home is the GitLab `ncs` group. *Mitigation:* point URLs at the GitLab `ncs/lightfall`
   paths; do **not** create a GitHub repo. Flag the discrepancy.
6. **`lucid-pipelines` not cloned** — leave intact; `lucid-endstation-7011` keeps depending
   on it. Follow-up item.
7. **GitLab rename side-effects** — CI configs, README badges, GitLab Pages URLs may
   reference old paths. *Mitigation:* grep each repo's `.gitlab-ci.yml`/README badges and
   update; redirects cover external clones temporarily.
8. **Briefcase app identifier change** (`gov.lbl.als.lucid` → `gov.lbl.als.lightfall`) —
   installed builds treat it as a new app. Acceptable pre-release.
9. **Generated `_version.py`** — produced by hatch-vcs; update the path in pyproject, never
   hand-edit the file.

## 8. Verification checklist

- [ ] `pip install -e` succeeds for every package in the shared `.venv`.
- [ ] `python -c "import lightfall"` works; `import lucid` fails (no stray package).
- [ ] `entry_points(group="lightfall.plugins")` lists all five plugins; no `lucid.plugins` remains.
- [ ] `pytest` passes in each repo.
- [ ] App launches via `lightfall-gui`; window/app name reads "Lightfall".
- [ ] Residual-reference grep (`-i lucid`, excluding `.venv`, history, generated files) returns
      only intentional matches: `lucid-pipelines`/`lucid_pipelines.pipeline` (out of scope),
      the OIDC `client_id="LUCID"` default, and the Sentry `project_name="LUCID"`.
- [ ] First-launch data-dir migration moves `~/lucid` → `~/lightfall` when only the old exists.
- [ ] Decks render with the new logo; paper builds via `make`.
- [ ] All local remotes resolve to `ncs/lightfall-*`; pushes succeed.

## 9. Resolved decisions (was: open items)

- **OIDC `client_id`** (risk #2): **keep `"LUCID"`** — not exposed, no IdP coordination needed.
- **Sentry `project_name`** (risk #3): **keep `"LUCID"`** — not exposed.
- **App data dir** (risk #4): **add a first-launch `~/lucid` → `~/lightfall` migration.**
