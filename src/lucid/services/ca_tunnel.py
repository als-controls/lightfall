"""CA search relay for remote EPICS access through SSH tunnels.

When LUCID connects to a remote CA Gateway via SSH tunnel, caproto
needs UDP for PV name search — but SSH only tunnels TCP. This service
relays UDP CA search requests to the gateway over TCP, rewrites FOUND
responses to point at localhost, and forwards NOT_FOUND as-is.

Flow:
    1. caproto sends UDP search to localhost:5099
    2. CATunnelService relays it to the gateway over TCP (SSH tunnel)
    3. Gateway does real CA search on the beamline network
    4. FOUND responses rewritten with localhost:5099 + 127.0.0.1
    5. NOT_FOUND responses forwarded as-is
    6. caproto opens TCP circuit to localhost:5099 → SSH tunnel → gateway

Preferences:
    - ca_tunnel_enabled: bool (default False)
    - ca_tunnel_gateway: str (default "localhost:5099")
"""

from __future__ import annotations

import getpass
import os
import socket
import struct
import threading
from typing import ClassVar

from lucid.utils.logging import logger

# CA protocol constants
CA_PROTO_SEARCH = 6
CA_PROTO_VERSION = 0
CA_PROTO_NOT_FOUND = 14
CA_VERSION = 13


def _parse_search_requests(data: bytes) -> list[tuple[int, int, bytes]]:
    """Parse CA search requests from a UDP packet.

    Returns list of (cid, reply_flag, pv_payload) tuples.
    """
    results = []
    offset = 0
    while offset + 16 <= len(data):
        cmd, payload_size, dtype, dcount, p1, p2 = struct.unpack_from(
            ">HHHHII", data, offset
        )
        if cmd == CA_PROTO_SEARCH and payload_size > 0:
            payload_start = offset + 16
            payload_end = payload_start + payload_size
            if payload_end <= len(data):
                results.append((p1, dtype, data[payload_start:payload_end]))
        offset += 16 + payload_size
    return results


class CATunnelService:
    """Relays CA UDP searches to a remote gateway via TCP.

    Receives UDP search packets from caproto, relays them to the CA
    Gateway over TCP (through an SSH tunnel), and sends responses back
    as UDP. FOUND responses are rewritten to direct caproto to connect
    to localhost. NOT_FOUND responses are forwarded as-is so caproto
    can manage its own retry/backoff logic.
    """

    _instance: ClassVar[CATunnelService | None] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._running = False
        self._thread: threading.Thread | None = None
        self._udp_sock: socket.socket | None = None
        self._host = "127.0.0.1"
        self._port = 5099
        # Pre-build the host/user messages (they never change)
        self._identity_msgs: bytes = b""

    @classmethod
    def get_instance(cls) -> CATunnelService:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            if cls._instance is not None:
                cls._instance.stop()
            cls._instance = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self, gateway: str = "localhost:5099") -> bool:
        """Start the CA search relay.

        Args:
            gateway: The CA Gateway address as host:port.

        Returns:
            True if started successfully.
        """
        if self._running:
            return True

        # Parse gateway address
        try:
            if ":" in gateway:
                self._host, port_str = gateway.rsplit(":", 1)
                self._port = int(port_str)
            else:
                self._host = gateway
                self._port = 5099
        except ValueError:
            logger.error("Invalid CA tunnel gateway address: {}", gateway)
            return False

        # Set EPICS environment BEFORE any CA initialization
        os.environ["EPICS_CA_AUTO_ADDR_LIST"] = "NO"
        os.environ["EPICS_CA_ADDR_LIST"] = f"{self._host}:{self._port}"

        # Pre-build identity messages
        self._identity_msgs = self._build_identity_msgs()

        # Bind UDP socket (SSH -L only binds TCP, so UDP is free)
        try:
            self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._udp_sock.bind((self._host, self._port))
            self._udp_sock.settimeout(1.0)
        except OSError as e:
            logger.error("Failed to bind UDP on {}:{}: {}", self._host, self._port, e)
            self._udp_sock = None
            return False

        self._running = True
        self._thread = threading.Thread(
            target=self._run, name="ca-tunnel", daemon=True
        )
        self._thread.start()

        logger.info(
            "CA tunnel started: relaying searches on {}:{} via TCP",
            self._host, self._port,
        )
        return True

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._udp_sock:
            try:
                self._udp_sock.close()
            except Exception:
                pass
            self._udp_sock = None
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        logger.info("CA tunnel stopped")

    # ── Message builders ──────────────────────────────────────────

    @staticmethod
    def _pad(data: bytes) -> bytes:
        """Pad to 8-byte boundary."""
        r = len(data) % 8
        return data + b"\0" * ((8 - r) if r else 0)

    def _build_identity_msgs(self) -> bytes:
        """Build hostname + username messages (sent once per TCP relay)."""
        host = self._pad(b"localhost\0")
        try:
            user = self._pad(getpass.getuser().encode() + b"\0")
        except Exception:
            user = self._pad(b"lucid\0")

        msgs = struct.pack(">HHHHII", 20, len(host), 0, 0, 0, 0) + host
        msgs += struct.pack(">HHHHII", 21, len(user), 0, 0, 0, 0) + user
        return msgs

    def _build_found_response(self, cid: int) -> bytes:
        """Build a FOUND search response directing caproto to localhost."""
        header = struct.pack(
            ">HHHHII",
            CA_PROTO_SEARCH,
            8,              # payload size
            self._port,     # server TCP port
            0,              # data_count
            0xffffffff,     # SID
            cid,            # CID
        )
        payload = struct.pack(">II", 0x7f000001, 0)  # 127.0.0.1
        return header + payload

    # ── TCP relay ─────────────────────────────────────────────────

    def _relay(self, searches: list[tuple[int, int, bytes]]) -> bytes | None:
        """Relay search requests to the gateway via TCP.

        Returns the raw gateway response (minus the version header),
        or None on failure.
        """
        tcp = None
        try:
            tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            tcp.settimeout(5.0)
            tcp.connect((self._host, self._port))

            # Build: version + identity + searches
            msgs = struct.pack(">HHHHII", 0, 0, 0, CA_VERSION, 0, 0)
            msgs += self._identity_msgs

            DO_REPLY = 10
            for cid, _, pv_payload in searches:
                msgs += struct.pack(
                    ">HHHHII", CA_PROTO_SEARCH, len(pv_payload),
                    DO_REPLY, CA_VERSION, cid, cid,
                ) + pv_payload

            tcp.sendall(msgs)

            # Read all responses
            tcp.settimeout(3.0)
            data = b""
            try:
                while True:
                    chunk = tcp.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                    tcp.settimeout(0.5)
            except socket.timeout:
                pass

            # Keep connection alive briefly so gateway remembers the searches
            def _close():
                import time
                time.sleep(5.0)
                try:
                    tcp.close()
                except Exception:
                    pass

            threading.Thread(target=_close, daemon=True).start()
            return data or None

        except (OSError, socket.timeout) as e:
            logger.debug("CA tunnel relay failed: {}", e)
            if tcp:
                try:
                    tcp.close()
                except Exception:
                    pass
            return None

    # ── Main loop ─────────────────────────────────────────────────

    def _run(self) -> None:
        """Main relay loop."""
        while self._running and self._udp_sock is not None:
            try:
                data, addr = self._udp_sock.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    logger.warning("CA tunnel UDP socket error")
                break

            if len(data) < 16:
                continue

            searches = _parse_search_requests(data)
            if not searches:
                continue

            response = self._relay(searches)
            if response is None:
                continue

            # Parse gateway response and build UDP reply
            udp_reply = struct.pack(">HHHHII", 0, 0, 0, CA_VERSION, 0, 0)

            offset = 0
            while offset + 16 <= len(response):
                cmd, psize, dtype, dcount, p1, p2 = struct.unpack_from(
                    ">HHHHII", response, offset
                )

                if cmd == CA_PROTO_SEARCH:
                    # FOUND — rewrite with localhost
                    udp_reply += self._build_found_response(p2)
                elif cmd == CA_PROTO_NOT_FOUND:
                    # NOT_FOUND — forward as-is
                    udp_reply += response[offset:offset + 16 + psize]
                elif cmd == CA_PROTO_VERSION:
                    pass  # Already added our own
                else:
                    udp_reply += response[offset:offset + 16 + psize]

                offset += 16 + psize

            # Send back to caproto
            if len(udp_reply) > 16:
                try:
                    self._udp_sock.sendto(udp_reply, addr)
                except OSError as e:
                    logger.debug("CA tunnel send failed: {}", e)
