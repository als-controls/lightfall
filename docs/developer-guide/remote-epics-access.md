# Remote EPICS Access via CA Tunnel

*How Lightfall connects to beamline EPICS IOCs from anywhere.*

## The Problem

EPICS Channel Access (CA) uses **UDP broadcast** for PV name resolution.
When a client wants to read `BL7ANDOR1:cam1:Acquire`, it broadcasts a
UDP search packet on port 5064. Every IOC on the subnet checks if it
owns that PV and replies.

This works great on a local network. It completely breaks for remote
access because:

- **SSH tunnels** only forward TCP, not UDP
- **SOCKS proxies** only handle TCP
- **Firewalls** typically block UDP broadcast across subnets
- **VPNs** may carry UDP but add latency and complexity

After the UDP search, CA switches to TCP for the actual data channel.
So the data path is fine — it's only the **discovery** phase that's broken.

## The Solution: Two Components

### 1. CA Gateway (server-side, on the beamline network)

The [EPICS CA Gateway](https://github.com/epics-extensions/ca-gateway)
runs on a machine inside the beamline network (e.g., `suzume`). It's
both a CA client and a CA server:

- **Client side**: searches for PVs via normal UDP broadcast on the
  beamline subnet — this works because it's local
- **Server side**: listens on a TCP port (5099) for client connections

The gateway is the bridge between "CA over local network" and "CA over
TCP to a single endpoint."

```
[Beamline Network]
  IOC (.44) ──UDP──┐
  IOC (.43) ──UDP──┤
  IOC (.50) ──UDP──┼── CA Gateway (suzume:5099/tcp)
                   │
            [UDP broadcast on 192.168.10.0/24]
```

### 2. CA Tunnel (client-side, built into Lightfall)

Lightfall's `CATunnelService` runs locally on the user's machine. It solves
the "CA needs UDP but SSH only does TCP" problem with a relay:

```
caproto (ophyd)                 CATunnelService              SSH Tunnel              CA Gateway
      │                               │                         │                       │
      │──UDP search──────────────────>│                         │                       │
      │  "where is PV:NAME?"          │                         │                       │
      │                               │──TCP connect────────────────────────────────────>│
      │                               │  version + host + user  │  (SSH -L 5099)        │
      │                               │  + search request       │                       │
      │                               │                         │                       │──UDP search──>IOCs
      │                               │                         │                       │<─UDP reply────IOCs
      │                               │<─TCP search response────────────────────────────│
      │                               │  "found at port 5099"   │                       │
      │<─UDP reply────────────────────│                         │                       │
      │  "connect to localhost:5099"  │                         │                       │
      │                               │                         │                       │
      │══TCP circuit═══════════════════════════════════════════════════════════════════>│
      │  CreateChan, Read, Write,     │                         │  (SSH -L 5099)        │──CA──>IOC
      │  Subscribe (all standard CA)  │                         │                       │<─CA──IOC
      │<══════════════════════════════════════════════════════════════════════════════════│
```

#### How the relay works

1. **caproto sends a UDP search** to `localhost:5099` (configured via
   `EPICS_CA_ADDR_LIST`). Since SSH `-L` only binds the TCP port, the
   UDP port is free for our tunnel to bind.

2. **CATunnelService receives the UDP packet**, opens a **TCP connection**
   to `localhost:5099` (which SSH forwards to the gateway), and sends:
   - CA version handshake
   - Hostname + username identification
   - The search request (with `DO_REPLY` flag forced on)

3. **The CA Gateway** receives the TCP search, does a real UDP broadcast
   search on the beamline network, and responds with FOUND or NOT_FOUND.

4. **CATunnelService rewrites FOUND responses**: changes the server
   address to `127.0.0.1:5099` so caproto will connect through the
   tunnel. NOT_FOUND responses are forwarded as-is.

5. **CATunnelService sends the response back as UDP** to caproto.

6. **caproto opens a TCP circuit** to `localhost:5099` — this goes through
   the SSH tunnel to the gateway, which proxies it to the real IOC.
   All subsequent CA operations (read, write, subscribe, monitor) flow
   over this TCP circuit.

#### Why it works

The key insight: after the initial search, **all CA communication is
TCP**. The tunnel only needs to bridge the UDP search phase. Once
caproto knows where to connect (localhost:5099 → SSH → gateway), the
standard CA TCP protocol handles everything — subscriptions, monitors,
puts, gets — all through the encrypted SSH tunnel.

## Setup

### Server side (one-time)

1. **Build the CA Gateway** on a beamline network machine:

   ```bash
   # Build PCAS (required for EPICS 7)
   cd ~/epics-gateway
   git clone https://github.com/epics-modules/pcas.git
   echo "EPICS_BASE = /usr/local/epics/R7.0.8.1/base" > pcas/configure/RELEASE.local
   make -C pcas -j4

   # Build CA Gateway
   git clone https://github.com/epics-extensions/ca-gateway.git
   printf "EPICS_BASE = /usr/local/epics/R7.0.8.1/base\nPCAS = $(pwd)/pcas\n" > ca-gateway/configure/RELEASE.local
   make -C ca-gateway -j4
   ```

2. **Configure** (`config/pvlist.txt` and `config/access.txt`):

   ```
   # pvlist.txt — allow all PVs
   EVALUATION ORDER ALLOW, DENY
   .* ALLOW
   ```

   ```
   # access.txt — read/write from tunnel clients
   HAG(tunnel) { localhost }
   ASG(DEFAULT) {
       RULE(1, READ)
       RULE(1, WRITE) {
           HAG(tunnel)
       }
   }
   ```

3. **Install as a systemd service** (`/etc/systemd/system/ca-gateway.service`):

   ```ini
   [Unit]
   Description=EPICS Channel Access Gateway
   After=network.target

   [Service]
   Type=simple
   User=rp
   Environment=LD_LIBRARY_PATH=/home/rp/epics-gateway/pcas/lib/linux-x86_64:/usr/local/epics/R7.0.8.1/base/lib/linux-x86_64
   ExecStart=/home/rp/epics-gateway/ca-gateway/bin/linux-x86_64/gateway \
       -pvlist /home/rp/epics-gateway/config/pvlist.txt \
       -access /home/rp/epics-gateway/config/access.txt \
       -log /home/rp/epics-gateway/gateway.log \
       -sip localhost -sport 5099 \
       -cip "192.168.10.255 localhost" -cport 5064 \
       -debug 1
   Restart=on-failure

   [Install]
   WantedBy=multi-user.target
   ```

   Note: `-cip` must include the **broadcast address** of the beamline
   subnet so the gateway can discover IOCs on other hosts.

### Client side

1. **SSH tunnel** (add `-L 5099:localhost:5099` to your SSH command):

   ```bash
   ssh -D 1080 -L 5099:localhost:5099 rp@suzume.lbl.gov
   ```

2. **Lightfall settings** (Settings → Devices → Connection Settings):
   - Enable "CA tunnel for remote access"
   - Gateway address: `localhost:5099`

3. **Restart Lightfall**. Devices connect automatically.

## Limitations

- **Latency**: Each PV search requires a TCP round-trip through the SSH
  tunnel (~50-200ms depending on network). Batch searches help but
  initial connection takes 10-30 seconds for many devices.

- **IOC must be reachable from the gateway host**: If the gateway can't
  reach an IOC via UDP broadcast, the PV won't be found. Check firewall
  rules (both on the gateway host and the IOC host).

- **One gateway per network segment**: The gateway searches via broadcast
  on its local subnet. IOCs on other subnets need additional `-cip`
  entries or a separate gateway.

- **Not suitable for end users**: Requires SSH access and tunnel setup.
  For production remote access, see the
  [WebSocket PV Bridge proposal](https://git.als.lbl.gov/bcs/developers/damon-english/beamline-storage-system/-/issues/4)
  which will provide Keycloak-authenticated access over standard HTTPS.

## Architecture Decision: Why Not Just Use PV Access?

PV Access (pvAccess, EPICS 7) is TCP-native and would tunnel perfectly.
However:

- Most ALS IOCs run CA, not pvAccess
- ophyd uses caproto (pure Python CA) by default, not p4p
- Switching the entire control layer is a larger change than bridging CA

The CA tunnel is a pragmatic solution that works with the existing
infrastructure. The WebSocket PV Bridge is the long-term answer.

## Files

- `src/lightfall/services/ca_tunnel.py` — CATunnelService implementation
- `src/lightfall/main.py` — Tunnel startup, timeout patching, auto-retry
- `src/lightfall/ui/preferences/device_settings.py` — Settings UI
