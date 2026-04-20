# LUCID Publication — Design Spec

**Date:** 2026-04-20
**Authors (arXiv version):** Ronald J. Pandolfi, Damon English, Beamline Controls Group (ALS, LBNL)
**Venue plan:** arXiv preprint first (~2 weeks out), then Journal of Synchrotron Radiation (IUCr) as the archival venue. Precedent: Xi-CAM, *J. Synchrotron Rad.* 25(4):1261–1270 (2018).
**Status:** Design approved 2026-04-20. Next step: implementation plan (writing-plans).

---

## 1. Thesis and framing

**Working title:** *LUCID: An API-first, LLM-addressable control platform for synchrotron beamlines*

**One-sentence thesis:**
> LUCID demonstrates that a single API-first design can make a beamline control system addressable by an LLM in two complementary roles — as a user of the interface and as a developer of it — yielding a sustainable alternative to both bespoke per-beamline GUIs and one-size-fits-all facility software.

**Primary contribution claim:** Dual-role LLM addressability (user + designer) enabled by a uniform API exposing panels, devices, and scan plans.

**Supporting claims** (earn their keep only insofar as they flow from the same API-first property):
- Unified UX across heterogeneous beamlines
- FAIR-compliant acquisition pipeline (Bluesky + Tiled)
- Secure remote operations (Keycloak + IPC)
- Plugin system (including runtime skills)

## 2. Venue strategy

- **arXiv first** — ~2-week draft window, ~4,500 words, 4 figures, ~25–30 references. Purpose: timestamp the ideas, enable broader co-author coordination without blocking the draft.
- **JSR conversion later** — same content partials re-used under IUCr's `iucr.cls`. JSR expansion adds the CSM/NSLS-II deployment (when live), quantitative evaluation, 2-3 additional figures, and a broader author list. Target ~6,500 words.

## 3. Abstract structure (~150 words, 5 sentences)

1. **Setup** — Synchrotron beamlines need customized control interfaces; custom development doesn't scale; generic interfaces force compromises.
2. **Approach** — LUCID is a facility-wide control platform whose API-first architecture exposes every panel, device, and scan plan through a uniform addressable interface.
3. **Result 1 (LLM-as-user)** — An embedded agent drives experiments through that same interface, bridging natural-language intent and device control.
4. **Result 2 (LLM-as-designer)** — The same addressability lets beamline staff extend the interface live via runtime skills that the agent executes; this has been tested with beamline scientists.
5. **Deployment** — In testing at the COSMIC-Scattering beamline at ALS, with planned rollout to CSM at NSLS-II. Code available at (LUCID repository URL).

## 4. Section outline (~4,500-word arXiv version)

### §1 Introduction — ~600 words
Setup the scaling problem, review prior approaches (bespoke per-beamline GUIs, one-size-fits-all facility GUIs), land the thesis, preview the paper.
Cites: Bluesky, Ophyd, Tiled, Xi-CAM, EPICS, CSS-Phoebus, EDM, BLUICE, GDA, LLM/agent literature (ReAct, tool-use).

### §2 Architecture — ~900 words
Establish the "API-first addressability" claim rigorously so §3 and §4 can appeal to it.
- 2.1 Design principles (API-first, plugin-extensible, progressive disclosure)
- 2.2 Core stack (Bluesky / Ophyd / Tiled / EPICS / Keycloak)
- 2.3 Uniform addressability — panels, devices, plans as peers on a common API
- 2.4 Plugin system (panels, settings, engines, plans, skills)
Figure 1 (architecture) lives here.

### §3 LLM-as-user (Control mode) — ~600 words
- Embedded agent architecture; tool schema auto-generated from the panel/device/plan API
- Worked example: natural-language request → tool call → panel action → device motion → acquisition
- Safety: Keycloak-gated tools, confirmation prompts on high-stakes actions, audit trail
Figure 2 (control-mode screenshot) lives here.

### §4 LLM-as-designer (Design-time extension via skills) — ~900 words — headline result
- SkillPlugin system — runtime plugins that inject prompt snippets and tools into the embedded agent
- Panel-design skills: structure, how staff invoke them, what the agent produces
- Worked example: scientist requests a new panel; agent writes panel config; change is git-committed; panel appears live
- Evaluation: what was tested with beamline scientists, what worked, what didn't
Figure 3 (before/after panel modification) lives here.

### §5 Supporting capabilities — ~500 words
Each subsection ~150 words and explicitly tied back to the API-first claim.
- 5.1 FAIR data via Tiled
- 5.2 Unified UX (theming, progressive disclosure, persistent preferences)
- 5.3 Secure remote operations
No dedicated figure.

### §6 Deployment at COSMIC-Scattering — ~600 words
- COSMIC context (technique, detector, scientist workflow)
- What we deployed, when, what was observed
- One end-to-end example threading through §3 and §4 capabilities
- CSM at NSLS-II as planned deployment (short paragraph)
Figure 4 (COSMIC operational snapshot) lives here.

### §7 Discussion and future work — ~300 words
Scope limits of current skill system; roadmap to in-app visual Design Mode; multi-facility generalization; safety/failure-mode work.

### §8 Conclusions — ~100 words
Restate thesis, point to deployment, invite collaboration.

### References — ~25–30
Anchored on Bluesky, Ophyd, Tiled, Xi-CAM, EPICS, CSS-Phoebus, BLUICE, a couple of LLM-agent references, FAIR-data references, and hardware/technique cites for COSMIC.

## 5. Figure plan

| # | Subject | Source | Effort |
|---|---------|--------|--------|
| 1 | Architecture diagram (layered: embedded agent → API layer → panels/devices/plans as peers → core stack). Annotations show LLM-as-user and LLM-as-designer paths. | PlantUML draft (`figures/arch.puml`), to be visually polished in another tool by Pandolfi. Committed as `figures/arch.pdf`. | High |
| 2 | LLM-as-user: annotated screenshot of embedded agent driving a COSMIC operation. | Placeholder in arXiv scaffold; Pandolfi will capture. | Medium |
| 3 | LLM-as-designer: before/after of a scientist-driven panel modification, with git-diff callout. | Placeholder; Pandolfi will capture from beamline-scientist-tested interactions. | Medium |
| 4 | COSMIC deployment snapshot. | Placeholder; Pandolfi will capture. | Low-medium |

Dropped for arXiv, reconsider for JSR: a SkillPlugin code-listing figure.

## 6. Repository and build mechanics

**New repo:** `git.als.lbl.gov/ncs/lucid-paper`

### Layout

```
lucid-paper/
├── README.md
├── Makefile
├── main-arxiv.tex
├── main-jsr.tex            # stub; matures when JSR conversion begins
├── content/
│   ├── 00-abstract.tex
│   ├── 01-introduction.tex
│   ├── 02-architecture.tex
│   ├── 03-llm-as-user.tex
│   ├── 04-llm-as-designer.tex
│   ├── 05-supporting.tex
│   ├── 06-deployment.tex
│   ├── 07-discussion.tex
│   └── 08-conclusions.tex
├── figures/
│   ├── arch.puml
│   ├── arch.pdf            # generated, committed so Overleaf works
│   ├── fig2-control-mode.png       # placeholder
│   ├── fig3-design-mode.png        # placeholder
│   ├── fig4-cosmic.png             # placeholder
│   └── README.md
├── references.bib
├── .gitlab-ci.yml
└── .gitignore
```

### LaTeX template

- **arXiv:** `article`, 11pt, standard margins.
- **JSR (later):** IUCr's `iucr.cls`. Same `content/` partials, different top-level file.
- **Bibliography:** `biblatex` + `biber`. Numeric style for arXiv; switch to IUCr's house style during JSR conversion. Single `references.bib`.
- **PlantUML:** draft `arch.puml`; `make figures` runs `plantuml -tpdf`. Both `.puml` and rendered `.pdf` committed.
- **Font:** no override. Let each class pick.

### Build

- `make pdf` → `latexmk -pdf main-arxiv.tex` (default).
- `make figures` → regenerates `arch.pdf` from `arch.puml`.
- `make arxiv-tarball` → flattened tarball for arXiv submission.

### CI

GitLab CI on day one: `latex` image, runs `make pdf`, uploads artifact. Validates that the paper compiles cleanly and lets co-authors get a current PDF without a local TeX install.

### Overleaf bridge

Deferred. Once a first draft is in, enable Overleaf's "Import from GitLab" with a personal access token for two-way sync.

## 7. JSR expansion deltas (post-arXiv)

- §6 expanded with CSM/NSLS-II deployment narrative (when live).
- Quantitative evaluation added (task-completion time, staff customization throughput, or similar — to be scoped when the data are in hand).
- 2–3 additional figures.
- Broader co-author list, including NSLS-II collaborators and any CAMERA/ASCR contributors to the LLM integration.
- Target length: ~6,500 words.
- Bibliography style switched to IUCr house style; top-level swapped to `main-jsr.tex` on `iucr.cls`.

## 8. Success criteria

Two milestones. The implementation plan owns M1; Pandolfi owns M2.

### M1 — Scaffold complete, initial drafts in (Claude-owned)

- New repo `ncs/lucid-paper` created, pushed, and building cleanly in GitLab CI.
- Repo layout matches §6: `content/` partials for all eight sections, `figures/` with placeholders, `references.bib` seeded, `Makefile` targets working, `.gitlab-ci.yml` green.
- `figures/arch.puml` drafted and rendered to `figures/arch.pdf`.
- Figures 2–4 present as placeholder images with `figures/README.md` explaining how to replace them.
- Initial prose draft for each section partial, at roughly the word budget (±30% at this stage is fine). Abstract and thesis sentence present in final form.
- Bibliography seeded with the anchor references (Bluesky, Ophyd, Tiled, Xi-CAM, EPICS, a couple of LLM-agent refs) even if the final count is not yet 25.
- `make arxiv-tarball` produces a valid tarball.

### M2 — arXiv submission ready (Pandolfi-owned, post-handoff)

- Figure 1 polished in Pandolfi's tool of choice; final `figures/arch.pdf` committed.
- Figures 2–4 have final captures replacing placeholders.
- All section prose reviewed and revised by Pandolfi/English.
- Reference list at ~25–30, all resolvable.
- arXiv metadata (categories, co-authors, license) agreed.
- arXiv submission uploaded.

## 9. Open items to resolve during implementation

- **§4 evaluation specifics.** Which beamline-scientist tests do we cite? What qualitative observations survive into the paper? (Resolution: Pandolfi selects the cleanest scientist-tested interaction for Fig. 3 and provides a 1-2 paragraph account of the testing session for §4.)
- **Panel-design-skill source.** Where do these skills live in the LUCID tree? The spec asserts they exist and work at runtime via the embedded agent; the implementation plan must locate them concretely so §4 can cite file paths or a skill name.
- **CSM collaborators' level of involvement.** Do any NSLS-II collaborators want to contribute an early paragraph to §6 for the arXiv version, or is that purely a JSR-expansion item? (Default assumption: JSR only.)
- **Reference list.** Final list to be built incrementally during drafting.

## 10. Out of scope for this spec

- Drafting the paper's prose. That's the implementation plan's job.
- The JSR submission itself (cover letter, revisions, rebuttal). Separate effort after arXiv.
- Overleaf setup. Deferred.
- In-app Design Mode (runtime visual editor). Discussed in §7 as future work; no implementation work here.
