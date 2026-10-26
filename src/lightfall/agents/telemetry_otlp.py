"""Optional OTLP export of agent telemetry to the facility LGTM stack.

Off unless all of these hold:

- the ``opentelemetry-sdk`` and ``opentelemetry-exporter-otlp-proto-http``
  packages are installed (``pip install lightfall[telemetry]``);
- an endpoint is configured: preference ``agent_telemetry_otlp_endpoint`` or
  env ``LIGHTFALL_OTLP_ENDPOINT`` (e.g. ``https://bcgotel.als.lbl.gov:8443``,
  the facility's OTLP/HTTP ingress);
- a producer credential is available when the endpoint needs one:
  ``LIGHTFALL_OTLP_USERNAME`` / ``LIGHTFALL_OTLP_PASSWORD`` (env only, never a
  preference, so it is not synced through settings scopes).

Metric names follow CSM's convention (``<service>_<thing>_total``) and the
resource carries ``service.name=lightfall`` and ``service.instance.id=<host>``,
the two labels the facility's Loki/Mimir index on.
"""

from __future__ import annotations

import base64
import os
import socket
from typing import Any

from lightfall.utils.logging import logger

SERVICE_NAME = "lightfall"
EXPORT_INTERVAL_MS = 60_000

_TOKEN_KINDS = {
    "input_tokens": "input",
    "output_tokens": "output",
    "cache_read_input_tokens": "cache_read",
    "cache_creation_input_tokens": "cache_creation",
}


def _pref(key: str, default: str = "") -> str:
    try:
        from lightfall.ui.preferences.manager import PreferencesManager

        return str(PreferencesManager.get_instance().get(key, default) or default)
    except Exception:  # noqa: BLE001
        return default


def configured_endpoint() -> str:
    return (os.environ.get("LIGHTFALL_OTLP_ENDPOINT") or _pref("agent_telemetry_otlp_endpoint")).rstrip("/")


def _auth_headers() -> dict[str, str]:
    user = os.environ.get("LIGHTFALL_OTLP_USERNAME", "")
    password = os.environ.get("LIGHTFALL_OTLP_PASSWORD", "")
    if not (user and password):
        # Half a credential would earn a 401 that reads as "wrong password";
        # send none so the failure says "no credential" instead.
        return {}
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


class OtlpAgentExporter:
    """Turns AgentTelemetry events into OTLP counters/histograms."""

    def __init__(self, meter_provider: Any) -> None:
        self._provider = meter_provider
        meter = meter_provider.get_meter("lightfall.agents")
        self._turns = meter.create_counter("lightfall_agent_turns_total", description="Agent turns")
        self._cost = meter.create_counter(
            "lightfall_agent_cost_usd_total", unit="USD", description="Agent cost reported by the backend")
        self._tokens = meter.create_counter("lightfall_agent_tokens_total", description="Agent tokens by kind")
        self._duration = meter.create_histogram(
            "lightfall_agent_turn_duration_seconds", unit="s", description="Wall time per agent turn")
        self._tools = meter.create_counter("lightfall_agent_tool_calls_total", description="Agent tool calls")
        self._permissions = meter.create_counter(
            "lightfall_agent_permission_decisions_total", description="Permission prompts and answers")

    def record(self, kind: str, *, beamline: str = "", **f: Any) -> None:
        base = {"agent": f.get("agent", ""), "beamline": beamline}
        if kind == "turn":
            attrs = {**base, "backend": f.get("backend", ""), "model": f.get("model", "")}
            values = f.get("values") or {}
            self._turns.add(1, {**attrs, "is_error": str(bool(f.get("is_error"))).lower()})
            if values.get("total_cost_usd"):
                self._cost.add(values["total_cost_usd"], attrs)
            for key, label in _TOKEN_KINDS.items():
                if values.get(key):
                    self._tokens.add(values[key], {**attrs, "kind": label})
            if values.get("duration_ms"):
                self._duration.record(values["duration_ms"] / 1000.0, attrs)
        elif kind == "tool":
            self._tools.add(1, {**base, "tool": f.get("tool", ""),
                                "is_error": str(bool(f.get("is_error"))).lower()})
        elif kind == "permission":
            self._permissions.add(1, {**base, "tool": f.get("tool", ""),
                                      "outcome": "allowed" if f.get("allowed") else "denied"})

    def shutdown(self) -> None:
        self._provider.shutdown()


def make_exporter() -> OtlpAgentExporter | None:
    """Build the exporter, or None when OTLP export is not configured/installed."""
    endpoint = configured_endpoint()
    if not endpoint:
        return None
    try:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
    except ImportError:
        logger.info(
            "Agent telemetry endpoint is set but opentelemetry is not installed; "
            "install lightfall[telemetry] to export. Local log only."
        )
        return None

    host = socket.gethostname()
    resource = Resource.create({
        "service.name": SERVICE_NAME,
        "service.instance.id": host,
        "host.name": host,
    })
    exporter = OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", headers=_auth_headers())
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=EXPORT_INTERVAL_MS)
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    logger.info("Agent telemetry: exporting OTLP metrics to {}", endpoint)
    return OtlpAgentExporter(provider)
