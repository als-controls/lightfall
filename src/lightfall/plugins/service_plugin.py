"""ServicePlugin: contributes an out-of-process service to the ServiceManager."""
from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar

from lightfall.plugins.types import PluginType
from lightfall_utils.ipc.services.descriptor import ServiceDescriptor


class ServicePlugin(PluginType):
    """Plugin type that declares an out-of-process service.

    The loader instantiates the plugin (singleton) and registers
    ``descriptor()`` with :class:`lightfall.services.manager.ServiceManager`.
    Registration does not start anything; a local service starts on the first
    ``ServiceManager.ensure_started(name)``.

    Local launch convention (``launch="local"``): ``descriptor.command`` is the
    invocation *without* the interpreter, e.g. ``("-m", "my_pkg.my_service")``. The
    manager runs::

        [interpreter, *command, "--scope", <resolved scope>,
         "--nats-url", "nats://127.0.0.1:<services_local_port>", "--token-stdin"]

    where ``interpreter`` is the value of the preference named by
    ``descriptor.interpreter_pref`` when that is set and non-empty, else
    ``sys.executable``. The service gets its session token as one line on stdin and
    should exit when stdin closes (see ``lightfall_utils.ipc.services.cli``).

    ``descriptor.stop_with_client``: when True (default) Lightfall's shutdown sends
    ``rpc.shutdown`` and, after a 3 s grace, terminates the child. When False the
    manager only closes its client connection and leaves the child running (it still
    sees stdin EOF when the Lightfall process itself exits).

    External services (``launch="external"``) are reached over Lightfall's main IPC
    broker; ``command`` is unused.
    """

    type_name: ClassVar[str] = "service"
    is_singleton: ClassVar[bool] = True

    @property
    def description(self) -> str:
        return "Out-of-process service plugin"

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin name."""
        ...

    @abstractmethod
    def descriptor(self) -> ServiceDescriptor:
        """Return the service's descriptor."""
        ...
