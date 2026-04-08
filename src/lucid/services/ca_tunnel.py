"""CA search relay for remote EPICS access through SSH tunnels.

When LUCID connects to a remote CA Gateway via SSH tunnel, caproto
needs UDP for PV name search — but SSH only tunnels TCP. This service
relays UDP CA search requests to the gateway over a persistent TCP
connection, rewrites FOUND responses to point at localhost, and
forwards NOT_FOUND as-is.

Flow:
    1. caproto sends UDP search to localhost:5099
    2. CATunnelService relays via persistent TCP to gateway (SSH tunnel)
    3. Gateway searches on beamline network
    4. FOUND → rewritten with localhost:5099, NOT_FOUND → forwarded as-is
    5. caproto opens TCP circuit to localhost:5099 → SSH → gateway

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
    """Relays CA UDP searches to a remote gateway via persistent TCP.

    Maintains a single TCP connection to the gateway, reusing it for
    all search relays. Reconnects automatically if the connection drops.
    """

    _instance: ClassVar[CATunnelService | None] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._running = False
        self._thread: threading.Thread | None = None
        self._udp_sock: socket.socket | None = None
        self._tcp_sock: socket.socket | None = None
        self._tcp_lock = threading.Lock()
        self._host = "127.0.0.1"
        self._port = 5099
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
        """Start the CA search relay."""
        if self._running:
            return True

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

        os.environ["EPICS_CA_AUTO_ADDR_LIST"] = "NO"
        os.environ["EPICS_CA_ADDR_LIST"] = f"{self._host}:{self._port}"

        self._identity_msgs = self._build_identity_msgs()

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

        logger.info("CA tunnel started: {}:{}", self._host, self._port)
        return True

    def stop(self) -> None:
        """Stop the relay and clean up all sockets."""
        if not self._running:
            return
        self._running = False

        # Close UDP socket to unblock recvfrom
        if self._udp_sock:
            try:
                self._udp_sock.close()
            except Exception:
                pass
            self._udp_sock = None

        # Close persistent TCP connection
        self._close_tcp()

        # Wait for thread to finish
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

        logger.info("CA tunnel stopped")

    # ── TCP connection management ─────────────────────────────────

    def _get_tcp(self) -> socket.socket | None:
        """Get or create the persistent TCP connection to the gateway."""
        with self._tcp_lock:
            if self._tcp_sock is not None:
                return self._tcp_sock

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect((self._host, self._port))

                # Version handshake
                sock.sendall(struct.pack(">HHHHII", 0, 0, 0, CA_VERSION, 0, 0))

                # Read version response (exactly 16 bytes)
                ver = b""
                while len(ver) < 16:
                    chunk = sock.recv(16 - len(ver))
                    if not chunk:
                        raise ConnectionError("Gateway closed during handshake")
                    ver += chunk

                # Send identity
                sock.sendall(self._identity_msgs)

                sock.settimeout(None)  # Non-blocking reads handled per-call
                self._tcp_sock = sock
                logger.debug("CA tunnel: TCP connection established")
                return sock

            except (OSError, ConnectionError) as e:
                # logger.debug("CA tunnel: TCP connect failed: {}", e)
                try:
                    sock.close()
                except Exception:
                    pass
                return None

    def _close_tcp(self) -> None:
        """Close the persistent TCP connection."""
        with self._tcp_lock:
            if self._tcp_sock:
                try:
                    self._tcp_sock.close()
                except Exception:
                    pass
                self._tcp_sock = None

    # ── Message builders ──────────────────────────────────────────

    @staticmethod
    def _pad(data: bytes) -> bytes:
        r = len(data) % 8
        return data + b"\0" * ((8 - r) if r else 0)

    def _build_identity_msgs(self) -> bytes:
        host = self._pad(b"localhost\0")
        try:
            user = self._pad(getpass.getuser().encode() + b"\0")
        except Exception:
            user = self._pad(b"lucid\0")
        msgs = struct.pack(">HHHHII", 20, len(host), 0, 0, 0, 0) + host
        msgs += struct.pack(">HHHHII", 21, len(user), 0, 0, 0, 0) + user
        return msgs

    def _build_found_response(self, cid: int) -> bytes:
        header = struct.pack(
            ">HHHHII",
            CA_PROTO_SEARCH, 8, self._port, 0, 0xffffffff, cid,
        )
        return header + struct.pack(">II", 0x7f000001, 0)

    # ── TCP relay ─────────────────────────────────────────────────

    def _relay(self, searches: list[tuple[int, int, bytes]]) -> bytes | None:
        """Relay search requests over the persistent TCP connection."""
        tcp = self._get_tcp()
        if tcp is None:
            return None

        try:
            # Build search messages
            DO_REPLY = 10
            msgs = b""
            for cid, _, pv_payload in searches:
                msgs += struct.pack(
                    ">HHHHII", CA_PROTO_SEARCH, len(pv_payload),
                    DO_REPLY, CA_VERSION, cid, cid,
                ) + pv_payload

            tcp.sendall(msgs)

            # Read responses with short timeout
            tcp.settimeout(3.0)
            data = b""
            try:
                while True:
                    chunk = tcp.recv(65536)
                    if not chunk:
                        raise ConnectionError("Gateway closed connection")
                    data += chunk
                    tcp.settimeout(0.3)
            except socket.timeout:
                pass

            tcp.settimeout(None)
            return data or None

        except (OSError, ConnectionError) as e:
            logger.debug("CA tunnel: relay failed, reconnecting: {}", e)
            self._close_tcp()
            return None

    # ── Main loop ─────────────────────────────────────────────────

    def _run(self) -> None:
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

            # Build UDP reply
            udp_reply = struct.pack(">HHHHII", 0, 0, 0, CA_VERSION, 0, 0)

            offset = 0
            while offset + 16 <= len(response):
                cmd, psize, dtype, dcount, p1, p2 = struct.unpack_from(
                    ">HHHHII", response, offset
                )

                if cmd == CA_PROTO_SEARCH:
                    udp_reply += self._build_found_response(p2)
                elif cmd == CA_PROTO_NOT_FOUND:
                    udp_reply += response[offset:offset + 16 + psize]
                elif cmd == CA_PROTO_VERSION:
                    pass
                else:
                    udp_reply += response[offset:offset + 16 + psize]

                offset += 16 + psize

            if len(udp_reply) > 16:
                try:
                    self._udp_sock.sendto(udp_reply, addr)
                except OSError as e:
                    logger.debug("CA tunnel send failed: {}", e)
