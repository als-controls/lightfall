"""CurrentEsafAgent: retrieve the ESAF active *right now* at the configured beamline.

Hard constraint (the reason this is its own skill rather than a generic ESAF
lookup): the tool exposed by this plugin can only ever return the ESAF that
contains the current instant in its scheduled window. It does NOT accept a
date or time parameter, and it never queries the alshub-api schedule endpoint
with anything other than ``now``. Future maintainers: do NOT add a date
parameter. If a different-time lookup is needed, write a sibling skill.

Configuration is read from preferences set by ``TiledSettingsPlugin``:

  ``tiled_beamline``         e.g. ``"7.0.1.1"``. Required.
  ``tiled_alshub_url``       e.g. ``"https://bcgmds01.als.lbl.gov"``. Required.
  ``tiled_alshub_api_key``   Optional. Falls back to ``ALSHUB_API_KEY`` env var,
                             then to a best-effort parse of ``~/.bashrc``.
                             Only needed when ``include_details=True``.

When ``tiled_beamline`` or ``tiled_alshub_url`` is unset, the tool returns a
clear error rather than guessing. The skill's system prompt always loads, but
the tool's response makes the unconfigured state explicit.

Endpoints used on alshub-api:

  ``GET /beamlines/{beamline}/active-esaf``    public; returns the lean payload.
  ``GET /{beamline}?start=now&stop=now``       requires ``api-key``; returns
                                               the full Event (PI, ExpLead,
                                               Participants, Description, ...).

The rich path is opt-in via ``include_details=True``. It first hits
``/active-esaf`` to identify the current ESAF, then queries the schedule
endpoint with ``start == stop == <one captured now>`` and defensively filters
the response on the friendly id from the public call so that even if the
schedule endpoint were ever to return more than one event, only the one
matching "current" leaks out.

Lightfall's production stamper (:mod:`lightfall.services.access_stamper`) consumes the
same public endpoint to build the ``access_blob`` injected into every Bluesky
run-start document. This skill surfaces the same lookup to the embedded
Claude agent for interactive queries, logbook annotation, and debugging the
``esaf_source`` decisions the stamper makes.
"""

from __future__ import annotations

import os
import pathlib
import re
from datetime import datetime, timezone
from typing import Any

from lightfall.plugins.agent_plugin import AgentPlugin
from lightfall.utils.logging import logger

#: Production alshub-api base URL, used only when no ``tiled_alshub_url``
#: preference is set. Matches the default in ``application.yaml``.
DEFAULT_ALSHUB_BASE = "https://bcgmds01.als.lbl.gov"


def _read_beamline_config() -> tuple[str | None, str | None]:
    """Return ``(beamline, alshub_url)`` from preferences.

    Reads the same prefs that ``tiled_service.install_into_run_engine`` uses
    to wire up ``AccessStamper``. Either value is ``None`` if not set;
    callers must surface this rather than substitute defaults.

    Read fresh each call — prefs may change at runtime via Settings UI, and
    we want subsequent tool calls to reflect that.
    """
    try:
        from lightfall.ui.preferences.manager import PreferencesManager
    except ImportError:
        # Bare imports during package introspection / out-of-process tests.
        return None, None

    try:
        prefs = PreferencesManager.get_instance()
    except Exception:
        return None, None

    beamline = prefs.get("tiled_beamline", None) or None
    alshub_url = prefs.get("tiled_alshub_url", None) or DEFAULT_ALSHUB_BASE
    return beamline, alshub_url


def _read_alshub_api_key() -> str | None:
    """Return the alshub API key from prefs / env / ``~/.bashrc``.

    Lookup order:

    1. ``tiled_alshub_api_key`` preference (the Lightfall-native channel).
    2. ``ALSHUB_API_KEY`` environment variable (the ops-friendly channel —
       set in shell profile, systemd unit, deployment script, etc.).
    3. Best-effort regex parse of ``~/.bashrc`` (defensive fallback for
       operators who put the key in their shell profile but launched Lightfall
       from a non-interactive shell that didn't source it). Reads only;
       never writes the key anywhere.

    Returns ``None`` if no key is configured. Callers must treat ``None``
    as "use the public endpoint only" rather than fabricating a request.
    """
    # 1. Preferences (Lightfall-native channel)
    try:
        from lightfall.ui.preferences.manager import PreferencesManager
        prefs = PreferencesManager.get_instance()
        key = prefs.get("tiled_alshub_api_key", "") or ""
        if key.strip():
            return key.strip()
    except Exception:
        pass

    # 2. Environment variable
    env_key = os.environ.get("ALSHUB_API_KEY", "").strip()
    if env_key:
        return env_key

    # 3. ~/.bashrc fallback
    try:
        text = pathlib.Path("~/.bashrc").expanduser().read_text()
    except OSError:
        return None
    m = re.search(
        r'^\s*(?:export\s+)?ALSHUB_API_KEY\s*=\s*"?([^"\n]+)"?',
        text,
        re.MULTILINE,
    )
    if not m:
        return None
    return m.group(1).strip() or None


def _resolve_alshub_proxy(alshub_url: str) -> str | None:
    """Resolve the configured proxy (if any) for ``alshub_url``.

    Honors Lightfall's shared Network Proxy settings — the same lookup
    ``tiled_service`` uses when constructing the production ``AlshubClient``.
    Returns ``None`` when no proxy applies, ``str`` URL otherwise.
    """
    try:
        from lightfall.ui.preferences.proxy_settings import ProxySettingsProvider
        return ProxySettingsProvider.should_use_proxy_for_url(alshub_url)
    except Exception:
        return None


async def _fetch_active_esaf_lean(
    beamline: str, alshub_url: str
) -> dict | None:
    """Hit the public ``/active-esaf`` endpoint via :class:`AlshubClient`.

    Reuses the production client (same one ``AccessStamper`` uses), so
    proxy / timeout behavior matches the stamping path. Returns ``None``
    on 404 ("no ESAF scheduled now"), raises on network/HTTP errors.
    """
    from lightfall.services._alshub_client import AlshubClient

    proxy = _resolve_alshub_proxy(alshub_url)
    client = AlshubClient(base_url=alshub_url, proxy=proxy)
    return await client.get_active_esaf(beamline)


async def _fetch_active_esaf_full(
    beamline: str, alshub_url: str, api_key: str
) -> dict | None:
    """Two-step lookup for the rich ``Event`` payload.

    Step 1: call ``/active-esaf`` to learn *which* ESAF is current (ground
    truth for "current"). Step 2: query the schedule endpoint with
    ``start == stop == <captured now>`` and filter on the friendly id from
    step 1. The captured timestamp is computed once and used twice so the
    two query params cannot diverge.

    Returns the full ``Event`` dict, or the lean payload with
    ``_partial: True`` added if the schedule endpoint returns nothing for
    our target id (unexpected; worth surfacing). Returns ``None`` if
    there's no active ESAF at all.
    """
    import httpx

    lean = await _fetch_active_esaf_lean(beamline, alshub_url)
    if lean is None:
        return None
    target = lean.get("EsafFriendlyId")

    # SAFETY: start and stop derive from a SINGLE captured value. Do not
    # split this into two ``datetime.now()`` calls — they could drift, and
    # more importantly, future readers might interpret a non-trivial gap
    # as a "window" worth widening.
    now_iso = datetime.now(timezone.utc).isoformat()

    # Header form for the API key. Keeps the secret out of URLs, access
    # logs, and process listings; the OpenAPI spec accepts query / header /
    # cookie equivalently.
    proxy = _resolve_alshub_proxy(alshub_url)
    client_kwargs: dict[str, Any] = {"timeout": 10.0}
    if proxy:
        client_kwargs["proxy"] = proxy

    async with httpx.AsyncClient(**client_kwargs) as c:
        resp = await c.get(
            f"{alshub_url.rstrip('/')}/{beamline}",
            params={
                "start": now_iso,
                "stop": now_iso,
                "include_unscheduled": "false",
            },
            headers={"api-key": api_key},
        )
    resp.raise_for_status()
    events = resp.json()
    matches = [e for e in events if e.get("EsafFriendlyId") == target]
    if not matches:
        # The public endpoint said something, the schedule endpoint said
        # nothing matching it. Return the lean answer rather than guessing.
        return {"_partial": True, **lean}
    # If somehow >1 match, prefer the latest Version (i.e. the most recent
    # revision of the same ESAF). The friendly-id filter above already
    # guarantees they're all the same logical ESAF.
    matches.sort(key=lambda e: e.get("Version", 0), reverse=True)
    return matches[0]


class CurrentEsafAgent(AgentPlugin):
    """Skill telling the embedded Claude agent how to retrieve the ESAF that
    is active *right now* at the beamline configured in Lightfall's Tiled
    settings.

    Contributes one MCP tool (``get_current_esaf``) plus a system-prompt
    snippet explaining when to use it and — critically — when to *refuse*
    to use it (any request that names a different date).
    """

    @property
    def name(self) -> str:
        return "current_esaf"

    @property
    def display_name(self) -> str:
        return "Current ESAF"

    @property
    def description(self) -> str:
        return (
            "Retrieve the ESAF active right now at the configured beamline "
            "from alshub-api. Strictly scoped to the present moment — never "
            "queries a different date."
        )

    @property
    def category(self) -> str:
        return "operations"

    @property
    def priority(self) -> int:
        return 40

    def get_system_prompt(self) -> str:
        # Read beamline at session start so the prompt mentions the actual
        # configured value rather than a placeholder. The agent re-reads
        # prefs on each tool call regardless, so prompt staleness is
        # cosmetic.
        beamline, _ = _read_beamline_config()
        beamline_str = beamline or "<not configured>"
        return f"""\
## Current ESAF Skill

Use this skill when the user asks about the ESAF, proposal, or experiment
that is **happening right now** at this beamline. Typical phrasings:

- "what ESAF is active?"
- "what's the current experiment?"
- "who's running on the beamline?"
- "tag this logbook entry with the current proposal"

### Configured beamline

Currently set to **{beamline_str}** (from Preferences → Tiled → Beamline).
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
"""

    def create_tools(self) -> list[Any]:
        try:
            from claude_agent_sdk import tool
        except ImportError:
            logger.warning(
                "claude_agent_sdk not available; current_esaf skill tools disabled"
            )
            return []

        @tool(
            name="get_current_esaf",
            description=(
                "Return the ESAF active RIGHT NOW at the beamline configured "
                "in Lightfall's Tiled preferences. Calls alshub-api and returns "
                "the lean ActiveEsaf payload by default, or the full Event "
                "with PI/Participants/Description if include_details=True. "
                "Returns null when no ESAF is scheduled at this instant. "
                "Takes NO date or time parameter — this tool is intentionally "
                "scoped to the present moment only. Takes NO beamline "
                "parameter — the beamline comes from the Tiled settings."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "include_details": {
                        "type": "boolean",
                        "description": (
                            "If true, fetch the full Event payload (PI, "
                            "ExpLead, Participants, Description, Status, "
                            "Version) via the gated endpoint. Requires "
                            "an alshub API key. Default false."
                        ),
                        "default": False,
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        )
        async def get_current_esaf(args: dict) -> dict[str, Any]:
            import httpx

            from lightfall.plugins.agents._mcp_helpers import mcp_error, mcp_result

            # Read beamline/alshub URL ONCE per call. Holding them as locals
            # for both the lean and rich paths ensures consistency even if
            # the user mutates prefs mid-call.
            beamline, alshub_url = _read_beamline_config()
            if not beamline:
                return mcp_error(
                    "tiled_beamline preference is not set. Configure it in "
                    "Preferences → Tiled → Beamline before using this skill."
                )
            if not alshub_url:
                return mcp_error(
                    "tiled_alshub_url preference is not set. Configure it in "
                    "Preferences → Tiled → Alshub URL before using this skill."
                )

            include_details = bool(args.get("include_details", False))

            try:
                if not include_details:
                    payload = await _fetch_active_esaf_lean(beamline, alshub_url)
                    return mcp_result(
                        {
                            "beamline": beamline,
                            "esaf": payload,
                            "source": f"alshub:/beamlines/{beamline}/active-esaf",
                            "detail_level": "lean",
                        }
                    )

                api_key = _read_alshub_api_key()
                if not api_key:
                    return mcp_error(
                        "include_details=True requires an alshub API key. "
                        "Set the tiled_alshub_api_key preference, the "
                        "ALSHUB_API_KEY env var, or add it to ~/.bashrc. "
                        "Re-run with include_details=False for the public "
                        "lean payload."
                    )
                payload = await _fetch_active_esaf_full(
                    beamline, alshub_url, api_key,
                )
                return mcp_result(
                    {
                        "beamline": beamline,
                        "esaf": payload,
                        "source": f"alshub:/{beamline}?start=now&stop=now",
                        "detail_level": "full",
                    }
                )
            except httpx.HTTPStatusError as e:
                logger.warning(
                    "alshub HTTP error for current ESAF: {} {}",
                    e.response.status_code,
                    e.response.text[:200],
                )
                return mcp_error(
                    f"alshub-api returned HTTP {e.response.status_code}. "
                    "Try again or check the alshub service status."
                )
            except (httpx.NetworkError, httpx.TimeoutException) as e:
                logger.warning(
                    "alshub network error for current ESAF: {}: {}",
                    type(e).__name__,
                    e,
                )
                return mcp_error(
                    f"Could not reach alshub-api at {alshub_url}: "
                    f"{type(e).__name__}. Likely a network issue "
                    "(check VPN/proxy if off-site)."
                )

        return [get_current_esaf]
