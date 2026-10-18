"""Network step: reach the facility server (through a SOCKS proxy off-site), then load
its bootstrap, apply it and reconfigure services."""
from __future__ import annotations

import socket
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from lightfall.setup.bootstrap import (
    FROM_SETTINGS,
    Bootstrap,
    NetworkError,
    apply_bootstrap,
    fetch_bootstrap,
    has_bootstrap,
    probe_health,
    resolve_proxy,
)
from lightfall.setup.services import reconfigure_services
from lightfall.setup.step import StepError, StepState, StepStatus

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from lightfall.setup.context import SetupContext


def host_of(url: str) -> str:
    return urlsplit(url).hostname or url


def route_label(server_url: str) -> str:
    return "Via SOCKS proxy" if resolve_proxy(server_url) else "Direct"


def connect(server_url: str, proxy: object = FROM_SETTINGS) -> Bootstrap:
    """Probe ``/health``, then fetch the bootstrap. Blocking: call off the GUI thread."""
    probe_health(server_url, proxy=proxy)  # type: ignore[arg-type]
    return fetch_bootstrap(server_url, proxy=proxy)  # type: ignore[arg-type]


def probe_socks(host: str, port: int, timeout: float = 2.0) -> bool:
    """Is something listening on host:port (a SOCKS tunnel)? Blocking."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class NetworkStep:
    id = "network"
    title = "Network"

    def detect(self, ctx: SetupContext) -> StepState:
        server = ctx.server_url()
        if not server:
            return StepState(StepStatus.NEEDS_INPUT, "Choose a facility first")
        try:
            probe_health(server)
        except NetworkError:
            return StepState(StepStatus.FAILED, f"{host_of(server)} not reachable")
        if not has_bootstrap(ctx.config) or ctx.config.get("setup.facility.server_url") != server:
            return StepState(StepStatus.NEEDS_INPUT, "Facility settings not loaded")
        return StepState(StepStatus.SATISFIED, route_label(server))

    def widget(self, ctx: SetupContext) -> QWidget:
        from lightfall.setup.pages.network import NetworkPage

        return NetworkPage(ctx)

    def validate(self, ctx: SetupContext) -> str | None:
        if ctx.bootstrap is None and not has_bootstrap(ctx.config):
            return "Facility settings haven't loaded yet. Check the connection, or skip this step."
        return None

    def apply(self, ctx: SetupContext) -> None:
        bootstrap = ctx.bootstrap
        if bootstrap is not None:
            if (not bootstrap.proxy_domains and ctx.facility is not None
                    and ctx.facility.proxy_domains):
                bootstrap = bootstrap.with_proxy_domains(ctx.facility.proxy_domains)
            try:
                apply_bootstrap(bootstrap, ctx.config)
            except OSError as exc:
                raise StepError(f"Couldn't save facility settings: {exc}") from exc
        errors = reconfigure_services(ctx.config)
        if errors:
            raise StepError("Facility settings loaded, but some services couldn't be "
                            "reconfigured: " + "; ".join(errors))
