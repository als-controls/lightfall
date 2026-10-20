# NATS Use Model

This page defines how every component on the ALS beamline NATS bus (bcgnats) is meant to use
NATS: which subjects exist, who may read and write each kind, what the broker enforces, and what
it does not. It is the reference for new clients and for moving existing ones onto the target
model. [IPC Architecture](ipc-architecture.md) covers how Lightfall's `IPCService` implements its
side; the [IPC Client Integration Guide](ipc-client-guide.md) shows a working external client.

```{note}
This model is enforced by the bcgnats **auth callout** (`bcgnats-auth-callout`), live on bcgnats
since 2026-10-20. Clients that connect without a capability token are rejected.
```

## The problem it solves

NATS core messages carry no sender identity, and on an open broker any connection may subscribe
to any subject, including `_INBOX.>`. Lightfall's trust handshake hands a private subject (and a
Tiled API key) back in a request reply, so on an open broker anyone could read it. Subject
allow-lists alone can't fix this: every client needs an inbox with a random name, so a rule that
lets a client read its own inbox lets it read everyone's.

The broker can only tell inboxes apart by identity. The auth callout gives every connection an
identity without issuing any credentials.

## The connection capability

Every connection carries a **capability**:

1. The client generates a random **secret**: 32 random bytes, URL-safe base64, 43 characters
   (`secrets.token_urlsafe(32)`). It sends the secret as its NATS connect `token`.
2. The callout derives the connection's **prefix**
   `p = base32(sha256("lightfall-nats-v1:" + secret))`, lowercase, padding removed, first 26
   characters (130 bits). The client computes the same `p` locally.
3. The client connects with `inbox_prefix="_INBOX.<p>"`. Its private root is `_PRIV.<p>`.
4. The callout issues the connection a user JWT whose permissions are derived from `p` (see
   "Permissions" below).

`p` is **not** secret: it appears as the reply-to of every request the client makes. What is
protected is the right to **subscribe** under `p`, and that takes the secret, which is never
published. The callout is stateless: no registry, no database, no per-client credentials.
Anonymous connections stay allowed. A connection with no token, or a token that isn't exactly 43
URL-safe characters, is rejected.

### Using the helper

Python clients use `lightfall_utils.ipc.capability` (stdlib only):

```python
import nats
from lightfall_utils.ipc.capability import new_capability

cap = new_capability()          # cap.secret, cap.prefix, cap.inbox_prefix, cap.priv_root
nc = await nats.connect(url, tls=tls_ctx, **cap.connect_kwargs())
# connect_kwargs() == {"token": cap.secret, "inbox_prefix": f"_INBOX.{cap.prefix}"}
```

The module also provides `derive_prefix(secret)`, `new_token()` (a 43-character random token,
also used for session and delivery tokens), `private_auth_request()` and `private_request()` (see
"The handshake" below), the `PrivateRootUnavailable` and `PrivateNoResponders` exceptions, and
`VENDORED_FROM` (currently `"lightfall_utils.ipc.capability v2"`).

Create one capability per connection and keep it for the connection's lifetime. nats.py reuses
its connect options on reconnect, so `p` and the private root stay stable. `IPCService` does
this for you.

### The vendored-helper rule

`lightfall_utils.ipc` imports PySide6, so headless services can't import the helper from
`lightfall-utils`. Those repositories **vendor `capability.py` byte-for-byte** as
`nats_capability.py` next to their NATS client, and carry a pinning test
(`test_nats_capability_vendored.py`) that checks known `derive_prefix` vectors and the
`VENDORED_FROM` marker; Camphor's also compares the file with the original. Never edit a
vendored copy: change `lightfall_utils/ipc/capability.py`, then re-copy it everywhere. The
`capability.py` module must stay free of Qt, Lightfall and third-party imports for this to work.

Repositories that vendor it today: lightfall-mcp-bridge, lightfall-logbook, lightfall-pipelines,
lightfall-deck, lightfall-saxs (`saxs-worker`), stxm-live, catfish (Camphor), simulator
(`prelude-core`), tsuchinoko, xpcs_live, shussebora, Xi-CAM `caproxy` and pystxmcontrol
(`pystxmcontrol/remote`, the `stxmcontrol-remote` Lightfall client). Lightfall itself and its
exporter import it from `lightfall-utils`.

## Subject classes

| Class | Examples | Who subscribes | Who publishes | Protects against |
|---|---|---|---|---|
| Public discovery and metadata | `_lightfall.discover`, `{prefix}.meta.actions`, `{prefix}.meta.events`, `_saxs.discover`, `{scope}.svc.discover`, `{root}.meta.info` | anyone | anyone | nothing; payloads must hold nothing secret |
| Public broadcast | `{prefix}.runs.new`, `{prefix}.runs.complete`, `{prefix}.state.engine`, `lightfall.settings.changed`, `xpcs.g2.updated`, `stxm.spectrum.updated`, `caproxy.<beamline>.lease` | anyone | anyone | nothing; readable and forgeable by anyone |
| Public request (legacy) | `saxs.op.<name>`, `saxs.pipeline.run`, xpcs, tsuchinoko, stxm, pipelines, exporter, Shussebora, Prelude and deck motor actions | anyone | anyone | nothing; requests readable, replies raceable |
| `_INBOX.<p>` | request replies | the owner only | nobody directly; a responder may answer once via `allow_responses` | reading another connection's replies. Not injection: see below |
| `_PRIV.<p>` | `_PRIV.<p>.auth.request`, `_PRIV.<p>.session.<token>.…`, `_PRIV.<p>.d.<delivery token>.…` | the owner only | anyone | reading private requests; answering them (only the owner receives them) |

### Public discovery and metadata

Discovery answers tell a client where a peer lives. In the target model these are the **only**
public request subjects, and their answers include the owner's **private root**. Lightfall's
`_lightfall.discover`, `{prefix}.meta.actions` and `{prefix}.meta.events` replies carry
`priv_root` (`_PRIV.<owner p>`).

### Public broadcast

Heartbeats, run lifecycle events, logbook change notices and other status broadcasts stay public:
many listeners need them and they carry no secrets. They are **not authenticated**: until CSM
registration exists, anyone can publish a fake heartbeat, event or discovery answer. Don't act on
a broadcast in a way that a forged one could abuse; fetch authoritative state through a private
request where it matters.

### Public request (legacy)

Several existing services still serve commands on public subjects (saxs-worker, stxm-live,
tsuchinoko, xpcs-live, the pipelines executor, the Lightfall exporter, Shussebora, Prelude's
controls and the deck's motor actions). Their subjects stay on the subscribe allow-list, so any
connection can read their requests and race their replies. This is an **accepted gap** in the
same class as spoofable discovery (see "What is and isn't protected" below).
In the cutover their only change is the connect options (token and inbox prefix). Don't add new
endpoints of this class; see "Migrating a legacy endpoint" below.

### `_INBOX.<p>`: replies

nats.py builds request reply subjects from the connection's `inbox_prefix`, so every reply to a
connection lands under `_INBOX.<p>`. Only the owner may subscribe there. Nobody may publish into
`_INBOX.>` directly; a responder answers a request it received through NATS `allow_responses`,
which permits **one** reply per received request, within **10 minutes** of receiving it. That
covers Lightfall's trust dialog (up to 60 s) and slow device waits. Longer-running work should
reply immediately with an accepted id and report completion via events.

Inbox isolation protects **reads only**. It does not stop another connection from *writing* into
your inbox. nats-server never checks a publisher's reply-to subject, and it grants
`allow_responses` to whoever receives a message with a reply subject. So a spy can publish to a
subject it subscribes to itself, with `reply=_INBOX.<your p>.<anything>`, and then "answer" it,
which delivers a forged message into your inbox. All it needs is the exact subject. The reply
subjects nats.py's `request()` generates are predictable to anyone who has seen one of your
public requests (they are a fixed per-connection prefix, a counter and 16 random bits).

So **every private or session request must use `private_request()`**, never `nc.request()`.
`private_request(nc, subject, payload, timeout=5.0, inbox_prefix=None)` subscribes a fresh reply subject
`<inbox_prefix>.<new_token()>` (256 random bits; `inbox_prefix` defaults to the connection's own) for one message, publishes the request with that
reply-to, and returns the decoded JSON reply. The reply subject only ever travels inside your
`_PRIV` request to the owner, which only the owner reads, so nobody else can guess it.
`private_auth_request()` uses it for the `auth.request` leg. Replies to *public* requests
(discovery, `meta.actions`, legacy public actions) can still be forged; they are in the same
class as spoofable discovery.

### `_PRIV.<p>`: private endpoints and channels

The owner's private requests and channels live under its private root. Only the owner may
subscribe, so only the owner reads what is published there and only the owner can answer it.
Anyone may publish into another connection's `_PRIV`, which is how requests reach it. Anything a
client must be able to trust as coming from a specific peer either travels as a reply to a
request the client sent into that peer's `_PRIV`, or carries a secret suffix.

- **Handshake:** `_PRIV.<owner p>.auth.request`.
- **Sessions:** `_PRIV.<owner p>.session.<session token>.<action>`.
- **Per-session events to a client** (services pattern): the client generates a delivery token
  with `new_token()` and sends it **inside** its `auth.request`, which only the owner reads. The
  service publishes that client's events to `_PRIV.<client p>.d.<delivery token>.…`. Knowing the
  client's `p` isn't enough to forge them.

## The handshake

1. **Discover** the peer and its private root: request `{prefix}.meta.actions` (or request
   `_lightfall.discover`) and read `priv_root`.
2. **Authenticate:** request `{priv_root}.auth.request` with
   `{"app_name": ..., "app_version": ...}` through `private_request()`. The reply goes to an
   unguessable subject in the client's own `_INBOX.<p>`.
3. On approval the reply carries `session_token` and **`session_subject`**
   (`_PRIV.<owner p>.session.<token>`), plus application fields (Lightfall adds `tiled_token`,
   `tiled_url`, `session_id`).
4. Send commands to `{session_subject}.<action>` with `private_request()`, for example
   `await private_request(nc, f"{session_subject}.commands.plan.run", params)`.

`private_auth_request(nc, topic_prefix, payload, timeout=5.0)` performs steps 1 and 2 and raises
`PrivateRootUnavailable` if the peer doesn't advertise a `priv_root` of the form
`_PRIV.<26 base32 characters>` (an old peer, or a malformed or hostile answer). On the service
side, `IPCService.register_action("auth.request", handler, private=True)` subscribes the handler
at `{priv_root}.auth.request` (its `meta.actions` catalog entry carries `"private": true`), and
`build_auth_response()` adds `session_subject`.

Requests to the old bare trusted subjects (`{prefix}.commands.…`) still answer with a structured
`denied` error. The pre-callout `{prefix}.session.<token>.…` and `{prefix}.auth.request` subjects
are no longer served.

## Permissions

The callout issues the same permission template to every connection:

- **Subscribe allow:** `_INBOX.<p>.>`, `_PRIV.<p>.>`, and `<root>.>` for each public root:
  `_lightfall`, `_shussebora`, `_saxs`, `_stxm`, `_tsuchinoko`, `_xpcs`, `als`, `lightfall`,
  `saxs`, `stxm`, `tsuchinoko`, `xpcs`, `shussebora`, `prelude`, `caproxy`.
- **No subscribe deny.** NATS can't express "deny `_INBOX.>` except your own", and a deny always
  beats an allow, so a deny would block the owner's own inbox. Isolation comes from the
  allow-list alone.
- **No subscribe pattern may begin with a wildcard.** A pattern like `*.*.svc.*.heartbeat` would
  also match `_PRIV.<p>.svc.x.heartbeat`. The callout refuses to load a template containing a
  leading-wildcard pattern, or one under `_INBOX` or `_PRIV`.
- **Publish:** allow `>`, deny `_INBOX.>`. The deny blocks only *direct* publishes into an
  inbox; replies via `allow_responses` (including reflected ones) still get through, which is
  why private requests use unguessable reply subjects.
- **Responses:** `allow_responses`: one reply per received request, within 10 minutes.
- **Lifetime:** the connection's. A reconnect goes through the callout again.

Public subscriptions are allow-listed rather than "everything except private namespaces", so a
new public subject must be added deliberately and a mistake fails closed: a subscribe outside the
allow-list gets a permissions violation.

### Adding a public subject root

A component that publishes under a new top-level subject token needs that root added before
anyone can subscribe to it:

1. Add `"<root>.>"` to `subscribe_allow` in `config/template.yaml` of `bcgnats-auth-callout`, with
   a comment naming the component and subject.
2. Add the root to `ROOTS` in `tests/spy/test_isolation.py` there, so `test_public_roots_allowed`
   covers it.
3. Run the spy suite and the smoke matrix against the replica, then redeploy the template on
   bcgnats (restart `nats-auth-callout.service`).

The root must start with a literal token. Prefer putting new subjects under an existing root
(`als.<beamline>.…`, `lightfall.…`) over adding a root.

## What is and isn't protected

**Protected.** Once a client holds a private root from the genuine service, nobody else can:

- read the handshake (including the Tiled API key) or session traffic, or anything else in
  another connection's `_INBOX.<p>` or `_PRIV.<p>` (inbox isolation protects reads);
- answer the handshake or any session request: only the owner receives them;
- forge a reply to a handshake or session request, **provided the client sends it with
  `private_request()`**: the reply subject is unguessable and only the owner sees it. This comes
  from the unguessable reply subject, not from the `_INBOX.>` publish deny, which reflection
  through `allow_responses` gets around;
- forge per-session events back to the client without its delivery token.

**Not protected (accepted gaps).**

- **Spoofable discovery / service impersonation.** Discovery answers are public and
  unauthenticated. A spy can answer `_lightfall.discover`, `meta.actions` or `svc.discover` with
  its own `_PRIV` root and impersonate a service to a client that believes it. This is the
  same gap as having no service identity. The intended fix is CSM registration, for example by
  letting clients check a discovered root against a registry. Accepted on 2026-10-19.
  (GitLab work item [ncs/lightfall#1](https://git.als.lbl.gov/ncs/lightfall/-/work_items/1), internal)
- **Replies to public requests can be forged.** Any connection can inject a message into
  another connection's inbox by reflection through `allow_responses` if it can predict the reply
  subject, and nats.py's `request()` reply subjects are predictable. That covers every public
  request (discovery, `meta.actions`, legacy actions), in the same class as spoofable discovery.
  Private and session requests are safe only because they use `private_request()`.
- **Legacy public request endpoints.** Requests and replies on the legacy public request
  subjects can be read, raced and forged (tracked in the same work item).
- **Public broadcasts** are readable and forgeable by anyone.

The bus doesn't claim stronger service identity than the control system underneath it: EPICS
Channel Access to the IOCs is unauthenticated. Service identity is deferred to CSM registration.

## Migrating a legacy endpoint

A service that serves commands on public subjects moves onto the target model like this, ideally
by adopting the IPC services pattern (`ServiceHost` in `lightfall-utils`, in design):

1. Keep only discovery and metadata public, and add the service's `priv_root` to its discovery
   and `meta.*` answers.
2. Move its command handlers under `{priv_root}` (with `IPCService`, `register_action(...,
   private=True)`, or `trusted=True` behind a session), behind an `auth.request` handshake at
   `{priv_root}.auth.request`.
3. Deliver per-client results to `_PRIV.<client p>.d.<delivery token>.…` instead of public
   result subjects; keep only non-sensitive status broadcasts public.
4. Update its clients to discover `priv_root` and use `private_auth_request()`.
5. Once no client uses the old public request subjects, stop serving them. Drop the root from the
   callout template if nothing public remains under it.

## Local development

The bundled `LocalNatsServer` listens on 127.0.0.1 only and runs **without** the callout or any
permissions: anything on that host is trusted. Clients still send their token and inbox prefix,
which a plain `nats-server` ignores, so the same client code runs against both. Don't expose a
local broker on a network interface.

To test against the real permission model, run the bcgnats replica from `bcgnats-auth-callout`
(`go run ./cmd/replica --port 4299`), which embeds `nats-server` with the callout on 127.0.0.1.
Its spy suite (`tests/spy`) and smoke matrix (`tools/smoke_matrix.py`) exercise the negative
cases and every known client.
