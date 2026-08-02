# Final Fix Report — agent-definitions-phase1

## Finding fixed

Important: a bad `{{var}}` in any user agent file broke the main Claude
panel. `resolve_template` raised `AgentSpecError` at session-assembly time
(`assemble_spec_options` for the current spec, `subagent_definitions` for
every OTHER enabled spec), `QtClaudeAgent.__init__` did not catch it, and
`widget.py`'s `except ValueError` (which `AgentSpecError` subclasses)
mistook it for the API-key error and showed the wrong UI. Per the design
spec, agent-file problems must never break startup — skip, log, surface.

## Changes

1. `src/lightfall/agents/spec.py`
   - Added `KNOWN_TEMPLATE_VARIABLES = frozenset({"beamline", "user", "endstation"})`.
   - `parse_agent_file` now scans the prompt body for `{{var}}` placeholders
     and raises `AgentSpecError` at load time if any are not in
     `KNOWN_TEMPLATE_VARIABLES`. This routes unknown-variable agent files
     through the registry's existing skip+`errors()` path instead of
     surfacing only later, at session assembly.
   - `resolve_template` unchanged (still raises for direct callers).

2. `src/lightfall/agents/assembly.py`
   - `subagent_definitions` now accepts an optional `variables: dict | None`
     param (defaults to `template_variables()` when omitted) and wraps each
     spec's `AgentDefinition` construction in `try/except AgentSpecError` →
     `logger.warning` + skip that spec. This is defense in depth for specs
     that bypass load-time validation (e.g. constructed directly, or if the
     known-variable set is ever loosened).
   - `assemble_spec_options` now passes its already-computed `variables`
     through to `subagent_definitions`, eliminating the redundant
     `template_variables()` re-read (ledger cleanup, adjacent to the fix).

3. `src/lightfall/claude/agent.py`
   - `QtClaudeAgent.__init__` now wraps the `assemble_spec_options(...)` call
     for the CURRENT spec in `try/except AgentSpecError`. On failure it logs
     a warning and falls back to the legacy `QT_SYSTEM_PROMPT` path (setting
     `self._spec = None`, which reuses the same branch already used when no
     spec is present), instead of letting the exception propagate up to
     `widget.py`'s `except ValueError` and triggering the API-key error UI.

## Tests added

- `tests/agents/test_spec.py`
  - `test_parse_agent_file_rejects_unknown_template_var` — `{{nope}}` in the
    body → `parse_agent_file` raises `AgentSpecError`.
  - `test_parse_agent_file_accepts_known_template_var` — `{{beamline}}`
    parses fine.
- `tests/agents/test_registry.py`
  - `test_bad_template_var_is_skipped_and_reported` — a user-scope file with
    `{{typo}}` lands in `errors()`; the other (good) agent still loads.
- `tests/agents/test_assembly.py`
  - `test_subagent_definitions_skips_spec_with_unknown_template_var` —
    `subagent_definitions` skips a spec whose prompt has an unknown var
    (constructed directly via `AgentSpec(...)` to bypass load validation)
    and still returns the good ones.

## Test commands + outcomes

```
cd .worktrees/agent-definitions-phase1
PYTHONPATH=src ../../.venv/Scripts/python -m pytest tests/agents tests/claude -q
```
Result: all passed (96 tests: 75 + 21 collected across the two dirs), exit 0.

```
PYTHONPATH=src ../../.venv/Scripts/python -m pytest -q
```
Result: full suite passed, exit 0, zero FAILED/ERROR lines (3 pre-existing
SKIPPED for optional integration/gpcam extras, unrelated to this change).

## Concerns

- None outstanding for this finding. The three layers are independent and
  each is individually sufficient; layered together per the design spec's
  defense-in-depth intent.
