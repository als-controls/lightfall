"""Local CA search responder for remote EPICS access through SSH tunnels.

When LUCID connects to a remote CA Gateway via SSH tunnel, CA clients
need UDP for PV name search — but SSH only tunnels TCP. This service
runs a local UDP responder that answers all CA search requests with
"connect to localhost:<port> via TCP", directing the CA client through
the SSH tunnel to the real CA Gateway.

The flow:
    [caproto/ophyd] --UDP search--> [CATunnelService] --"connect to localhost:5099"-->
    [caproto/ophyd] --TCP connect--> [SSH tunnel] --TCP--> [CA Gateway] --CA--> [IOCs]

Usage:
    The tunnel is configured via LUCID preferences:
        - ca_tunnel_enabled: bool (default False)
        - ca_tunnel_gateway: str (default "localhost:5099")

    When enabled, the service:
        1. Binds a local UDP socket on the gateway port
        2. Responds to all CA search requests with the gateway address
        3. Sets EPICS_CA_AUTO_ADDR_LIST=NO and EPICS_CA_ADDR_LIST=localhost:<port>

    This must be started BEFORE any EPICS/ophyd initialization.
"""

from __future__ import annotations

import os
import socket
import struct
import threading
from typing import ClassVar

from lucid.utils.logging import logger

# CA protocol constants
CA_PROTO_SEARCH = 6
CA_PROTO_VERSION = 0
CA_VERSION = 13


def _parse_search_requests(data: bytes) -> list[tuple[int, int, str]]:
    """Parse CA search request messages from a UDP packet.

    Returns list of (search_id, reply_flag, pv_name) tuples.
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
                pv_bytes = data[payload_start:payload_end]
                pv_name = pv_bytes.split(b"\0")[0].decode("ascii", errors="ignore")
                if pv_name:
                    # p1 is the search ID (CID), dtype is reply flag
                    results.append((p1, dtype, pv_name))
        offset += 16 + payload_size
    return results


def _build_search_response(search_id: int, port: int) -> bytes:
    """Build a CA search response message.

    Tells the client: "I have this PV, connect to me on TCP at port <port>."

    The CA spec (and caproto) expects payload_size=8 with an 8-byte payload
    containing the server's IP address as a big-endian uint32 (padded to 8).
    We use 127.0.0.1 (0x7f000001) so caproto connects to localhost.

    Args:
        search_id: The CID from the search request.
        port: TCP port to connect to (the tunneled gateway port).
    """
    # Header: command=6, payload_size=8, data_type=port, data_count=0,
    #         parameter1=SID (0xffffffff), parameter2=CID
    header = struct.pack(
        ">HHHHII",
        CA_PROTO_SEARCH,  # command
        8,                # payload size (8 bytes of IP address)
        port,             # server TCP port (in data_type field)
        0,                # data_count = 0
        0xffffffff,       # SID (0xffffffff = use address from payload)
        search_id,        # CID (must match request)
    )
    # Payload: 4 bytes IP address + 4 bytes padding
    # 127.0.0.1 = 0x7f000001
    payload = struct.pack(">II", 0x7f000001, 0)
    return header + payload


def _build_version_response() -> bytes:
    """Build a CA version response message."""
    return struct.pack(
        ">HHHHII",
        CA_PROTO_VERSION,  # command
        0,                 # payload size
        0,                 # unused
        CA_VERSION,        # version
        0,                 # unused
        0,                 # unused
    )


class CATunnelService:
    """Singleton service that responds to local CA searches, directing
    TCP connections through an SSH tunnel to a remote CA Gateway.

    Instead of forwarding packets, this acts as a fake CA search
    responder — it tells caproto/ophyd "yes, I have that PV, connect
    to localhost:<port> via TCP." The TCP connection then goes through
    the SSH tunnel to the real CA Gateway.
    """

    _instance: ClassVar[CATunnelService | None] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._running = False
        self._thread: threading.Thread | None = None
        self._udp_sock: socket.socket | None = None
        self._host = "127.0.0.1"
        self._port = 5099

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
        """Start the CA search responder.

        Args:
            gateway: The CA Gateway address as host:port. The SSH tunnel
                should forward this port to the remote gateway.

        Returns:
            True if started successfully, False on error.
        """
        if self._running:
            logger.debug("CA tunnel already running")
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
        logger.info(
            "Set EPICS_CA_AUTO_ADDR_LIST=NO, EPICS_CA_ADDR_LIST={}:{}",
            self._host,
            self._port,
        )

        # Bind UDP socket on the same port
        # (SSH -L only binds TCP, so UDP on this port is free)
        try:
            self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._udp_sock.bind((self._host, self._port))
            self._udp_sock.settimeout(1.0)
        except OSError as e:
            logger.error(
                "Failed to bind UDP socket on {}:{}: {}", self._host, self._port, e
            )
            self._udp_sock = None
            return False

        self._running = True
        self._thread = threading.Thread(
            target=self._run_responder,
            name="ca-tunnel",
            daemon=True,
        )
        self._thread.start()

        logger.info(
            "CA tunnel started: answering UDP searches on {}:{}, "
            "directing TCP to {}:{} (via SSH tunnel)",
            self._host,
            self._port,
            self._host,
            self._port,
        )
        return True

    def stop(self) -> None:
        """Stop the responder."""
        if not self._running:
            return

        self._running = False

        if self._udp_sock is not None:
            try:
                self._udp_sock.close()
            except Exception:
                pass
            self._udp_sock = None

        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

        logger.info("CA tunnel stopped")

    def _run_responder(self) -> None:
        """Main responder loop (runs in background thread).

        Listens for UDP CA search requests and responds with
        "connect to localhost:<port>" for every PV requested.
        """
        while self._running and self._udp_sock is not None:
            try:
                data, addr = self._udp_sock.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    logger.warning("CA tunnel UDP socket error, stopping")
                break

            if len(data) < 16:
                continue

            # Parse search requests
            searches = _parse_search_requests(data)
            if not searches:
                # Might be a version message or repeater registration — ignore
                continue

            # Build response: version + search replies
            response = _build_version_response()
            for search_id, reply_flag, pv_name in searches:
                response += _build_search_response(search_id, self._port)
                logger.debug("CA tunnel: search for '{}' -> localhost:{}", pv_name, self._port)

            # Send response back to the requesting client
            try:
                self._udp_sock.sendto(response, addr)
            except OSError as e:
                logger.debug("CA tunnel: failed to send response to {}: {}", addr, e)
