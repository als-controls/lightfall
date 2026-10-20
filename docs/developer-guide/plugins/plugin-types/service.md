# ServicePlugin

Service plugins declare an out-of-process service (a `ServiceHost` from
`lightfall-utils`, see its `docs/ipc.md`). Registration does not start anything; a local
service starts on the first `ServiceManager.ensure_started(name)`.

## Base Class

```python
from lightfall.plugins.service_plugin import ServicePlugin
from lightfall_utils.ipc.services.descriptor import ServiceDescriptor
```

| Attribute | Value |
|-----------|-------|
| `type_name` | `"service"` |
| `is_singleton` | `True` |

Implement `name` and `descriptor()`:

```python
class MyServicePlugin(ServicePlugin):
    @property
    def name(self) -> str:
        return "my-service"

    def descriptor(self) -> ServiceDescriptor:
        return ServiceDescriptor(
            name="my",
            scope="{prefix}.host.{hostname}.{client_instance}",
            contract="1.0",
            launch="local",
            command=("-m", "my_pkg.my_service"),
        )
```

## Launch contract

For `launch="local"` the `command` omits the interpreter. The manager runs
`[interpreter, *command, "--scope", <scope>, "--nats-url", "nats://127.0.0.1:<services_local_port>", "--token-stdin"]`
and writes the session token on stdin. The interpreter is the value of the preference named by
`interpreter_pref` when set and non-empty, else `sys.executable`. Use `run_service` from
`lightfall_utils.ipc.services.cli` in the service's `main` to get these flags.

Local services always stop with Lightfall: `rpc.shutdown`, a 3 s grace, then terminate
(`stop_with_client=False` is not honoured and logs a warning). A crashing service is restarted
with backoff 1, 2, 5, 10 s; after 5 failures within 60 s it is reported `down` with its last
stderr lines in the status bar tooltip. A child that was `up` and then stops heartbeating
while its process keeps running is killed and restarted the same way. Until a freshly
launched child answers, its health is `starting` (not `down`): start the `ServiceHost` first
and load heavy resources in the background, reporting progress through
`host.set_state(state, summary)`. The child logs to
`logs/services/<name>-<client instance[:8]>.log`.

If the host-local broker dies (this Lightfall's own, or another Lightfall's that this one was
using), the manager starts a broker on the same port and the clients and children reconnect.

## Runtime control

All of these return immediately (GUI thread):

| Call | Effect |
|------|--------|
| `ensure_started(name)` | start if not running (local: lazily, after the broker answers) |
| `stop_service(name)` | stop the client and, for a local service, the child (e.g. a settings toggle) |
| `restart(name)` | stop if running, then start again: picks up a changed interpreter preference and retries after a give-up or a failed first spawn |
| `unregister(name)` | stop it and forget the descriptor |
| `client(name)` | the current `QtServiceClient`, or `None` |
| `client_created(name)` signal | a new client exists: connect to its signals here |

Connect to the client's `state_changed`, `info_changed` (service `state`/`summary`, e.g.
loading/ready), `restarted` and `session_reset` (both mean "resync"), `event` (per-session,
after `watch_event(name)`) and `public_event` (after `watch_public_event(name)`).

For `launch="external"` the service is already running and is reached over the main broker;
`command` is unused.
