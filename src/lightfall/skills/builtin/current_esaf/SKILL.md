---
name: current-esaf
description: Retrieve the ESAF active right now at the configured beamline from alshub-api. Strictly scoped to the present moment — never queries a different date.
---

## Current ESAF Skill

Use this skill when the user asks about the ESAF, proposal, or experiment
that is **happening right now** at this beamline. Typical phrasings:

- "what ESAF is active?"
- "what's the current experiment?"
- "who's running on the beamline?"
- "tag this logbook entry with the current proposal"

### Configured beamline

Currently set to **{{beamline}}** (from Preferences → Tiled → Beamline).
If you see `<not configured>` above, the user must set the beamline pref
before this skill can be used — surface that clearly when asked.

### The one tool

`get_current_esaf(include_details: bool = False)` — calls alshub-api and
returns the ESAF whose scheduled window contains the present instant.

- `include_details=False` (default): lean payload from the public
  endpoint — `EsafFriendlyId`, `ProposalFriendlyId`, `Beamline`,
  `ScheduledStart`, `ScheduledStop`, `Title`. No auth needed.

- `include_details=True`: full Event payload, including `PI`, `ExpLead`,
  `Participants` (each with name/email/orcid/lbnl-id), `Description`,
  `Status`, `Version`. Requires an alshub API key (from the
  `tiled_alshub_api_key` preference, the `ALSHUB_API_KEY` env var, or
  `~/.bashrc`). If the key isn't available, the tool returns an error
  rather than silently degrading to the lean payload.

### Three response states (matching AccessStamper's convention)

- ESAF dict → an experiment is scheduled and running now.
- `null` → no ESAF scheduled at this instant. Not an error; surface plainly.
- Tool error → alshub-api is unreachable, returned non-404 failure, or
  required preferences are unset.

### When to REFUSE

If the user asks about an ESAF at a *different time* — "what ESAF is on
June 16?", "what's scheduled next week?", "show me last Tuesday's
experiment" — this skill does **not** answer. Tell the user it's scoped
to "now" and suggest the ALS User Portal. Do NOT extend this skill, write
inline code, or invent a date parameter for the tool. The "now-only"
constraint is structural, not stylistic.

### Beamline is set by preferences, not by argument

This skill always operates on whatever beamline is currently set in
`Preferences → Tiled → Beamline`. To use it at a different beamline,
change the preference (or restart Lightfall against a different config) —
the tool itself never accepts a beamline parameter.
