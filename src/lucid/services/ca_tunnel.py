"""CA search relay for remote EPICS access through SSH tunnels.

When LUCID connects to a remote CA Gateway via SSH tunnel, CA clients
(caproto) need UDP for PV name search — but SSH only tunnels TCP. This
service relays UDP CA search requests to the gateway over TCP, then
sends the gateway's response back as UDP.

The key insight: the CA Gateway accepts search requests over TCP (on its
main server port). After a TCP search, the gateway knows about the PV
and will accept CreateChan requests on new TCP connections. We relay
the search over TCP so the gateway is primed, then tell caproto to
connect to the gateway's TCP port.

Flow:
    1. caproto sends UDP search for "PV:NAME" to localhost:5099
    2. CATunnelService receives it, opens TCP to localhost:5099 (SSH tunnel)
    3. Sends version + search over TCP to the gateway
    4. Gateway does real CA search on beamline network, responds
    5. CATunnelService sends search response back to caproto as UDP
    6. caproto opens TCP to localhost:5099 for the circuit
    7. Gateway already knows about PV → CreateChan succeeds

Usage:
    Preferences:
        - ca_tunnel_enabled: bool (default False)
        - ca_tunnel_gateway: str (default "localhost:5099")

    Must be started BEFORE any EPICS/ophyd initialization.
"""

from __future__ import annotations

import os
import socket
import struct
import threading
import time
from typing import ClassVar

from lucid.utils.logging import logger

# CA protocol constants
CA_PROTO_SEARCH = 6
CA_PROTO_VERSION = 0
CA_PROTO_NOT_FOUND = 14
CA_VERSION = 13


def _parse_search_requests(data: bytes) -> list[tuple[int, int, bytes]]:
    """Parse CA search request messages from a UDP packet.

    Returns list of (cid, reply_flag, pv_payload) tuples.
    pv_payload includes the PV name with padding, ready to re-send.
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
                pv_payload = data[payload_start:payload_end]
                pv_name = pv_payload.split(b"\0")[0].decode("ascii", errors="ignore")
                results.append((p1, dtype, pv_payload))
                logger.debug("CA tunnel: search for '{}' -> relay via TCP", pv_name)
        offset += 16 + payload_size
    return results


class CATunnelService:
    """Singleton service that relays CA UDP searches to the gateway via TCP.

    When caproto sends a UDP search, this service:
    1. Opens a TCP connection to the gateway (through SSH tunnel)
    2. Sends version + host + user + search request
    3. Reads the search response
    4. Sends it back to caproto as UDP

    This primes the gateway with knowledge of the PV, so when caproto
    subsequently opens a TCP circuit to CreateChan, the gateway accepts it.
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
        """Start the CA search relay.

        Args:
            gateway: The CA Gateway address as host:port.

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
            "CA tunnel started: relaying UDP searches on {}:{} via TCP",
            self._host,
            self._port,
        )
        return True

    def stop(self) -> None:
        """Stop the relay."""
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

    def _relay_search_via_tcp(self, search_requests: list[tuple[int, int, bytes]]) -> bytes | None:
        """Relay search requests to the gateway via TCP.

        Opens a TCP connection, sends version + hostname + username + search
        requests, reads back the responses, and returns them.

        Args:
            search_requests: List of (cid, reply_flag, pv_payload) tuples.

        Returns:
            Raw bytes of gateway's response, or None on failure.
        """
        tcp_sock = None
        try:
            tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            tcp_sock.settimeout(5.0)
            tcp_sock.connect((self._host, self._port))

            # Build complete message sequence
            msgs = b""

            # 1. Version
            msgs += struct.pack(">HHHHII", 0, 0, 0, CA_VERSION, 0, 0)

            # 2. Hostname
            host = b"localhost\0"
            pad = (8 - len(host) % 8) % 8
            host_padded = host + b"\0" * pad
            msgs += struct.pack(">HHHHII", 20, len(host_padded), 0, 0, 0, 0) + host_padded

            # 3. Username
            import getpass
            try:
                user = getpass.getuser().encode() + b"\0"
            except Exception:
                user = b"lucid\0"
            pad = (8 - len(user) % 8) % 8
            user_padded = user + b"\0" * pad
            msgs += struct.pack(">HHHHII", 21, len(user_padded), 0, 0, 0, 0) + user_padded

            # 4. Search requests
            for cid, reply_flag, pv_payload in search_requests:
                msgs += struct.pack(
                    ">HHHHII",
                    CA_PROTO_SEARCH,
                    len(pv_payload),
                    reply_flag,
                    CA_VERSION,
                    cid,
                    cid,
                ) + pv_payload

            # Send everything, then read everything
            tcp_sock.sendall(msgs)

            # Read all responses — version + search replies arrive together
            # First read: wait up to 5s for first data
            tcp_sock.settimeout(5.0)
            all_data = b""
            try:
                chunk = tcp_sock.recv(65536)
                if chunk:
                    all_data += chunk
                # Quick follow-up reads for any remaining data
                tcp_sock.settimeout(1.0)
                while True:
                    chunk = tcp_sock.recv(65536)
                    if not chunk:
                        break
                    all_data += chunk
                    tcp_sock.settimeout(0.5)
            except socket.timeout:
                pass

            logger.debug(
                "CA tunnel: gateway returned {} bytes: {}",
                len(all_data),
                all_data[:64].hex() if all_data else "(empty)",
            )

            # Keep the TCP connection open briefly so the gateway maintains
            # its knowledge of the search results for subsequent circuits
            def _close_later():
                time.sleep(10.0)
                try:
                    tcp_sock.close()
                except Exception:
                    pass

            closer = threading.Thread(target=_close_later, daemon=True)
            closer.start()

            return all_data if all_data else None

        except (ConnectionRefusedError, ConnectionResetError, socket.timeout) as e:
            logger.debug("CA tunnel TCP relay failed: {}", e)
            if tcp_sock:
                try:
                    tcp_sock.close()
                except Exception:
                    pass
            return None
        except OSError as e:
            logger.debug("CA tunnel TCP relay error: {}", e)
            if tcp_sock:
                try:
                    tcp_sock.close()
                except Exception:
                    pass
            return None

    def _build_search_response_for_udp(self, cid: int) -> bytes:
        """Build a CA search response directing caproto to connect via TCP.

        The gateway's TCP search response has port=0 meaning "use this
        connection." But caproto will open a NEW TCP connection for the
        circuit. We need to tell it the gateway's port explicitly.

        Args:
            cid: The client's search ID.

        Returns:
            Raw bytes for a UDP search response.
        """
        # Header: cmd=6, payload=8, data_type=port, data_count=0,
        #         p1=SID(0xffffffff), p2=CID
        header = struct.pack(
            ">HHHHII",
            CA_PROTO_SEARCH,
            8,              # payload size
            self._port,     # server TCP port
            0,              # data_count
            0xffffffff,     # SID = use address from payload
            cid,            # CID
        )
        # Payload: 127.0.0.1 + padding
        payload = struct.pack(">II", 0x7f000001, 0)
        return header + payload

    def _run_responder(self) -> None:
        """Main relay loop (runs in background thread).

        Listens for UDP CA search requests, relays them to the gateway
        via TCP, then sends the gateway's response back as UDP.
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
                continue

            # Relay searches to gateway via TCP
            gateway_response = self._relay_search_via_tcp(searches)

            if gateway_response is None:
                # Gateway unreachable or no response
                continue

            # Parse gateway TCP response and build UDP responses
            # The gateway may respond with search replies (cmd=6) or
            # not-found (cmd=14). We need to rewrite port=0 responses
            # to include the actual gateway port.
            udp_response = b""

            # Add version response first
            udp_response += struct.pack(">HHHHII", 0, 0, 0, CA_VERSION, 0, 0)

            offset = 0
            while offset + 16 <= len(gateway_response):
                cmd, psize, dtype, dcount, p1, p2 = struct.unpack_from(
                    ">HHHHII", gateway_response, offset
                )

                if cmd == CA_PROTO_SEARCH:
                    # Gateway found the PV — rewrite with our port and IP
                    cid = p2
                    udp_response += self._build_search_response_for_udp(cid)
                    logger.debug("CA tunnel: PV found (CID={}), directing to localhost:{}", cid, self._port)
                elif cmd == CA_PROTO_NOT_FOUND:
                    # PV not found — forward as-is
                    udp_response += gateway_response[offset:offset + 16 + psize]
                elif cmd == CA_PROTO_VERSION:
                    # Skip version responses (we already added one)
                    pass
                else:
                    # Forward other messages as-is
                    udp_response += gateway_response[offset:offset + 16 + psize]

                offset += 16 + psize

            # Send response back to caproto
            if len(udp_response) > 16:  # More than just the version header
                logger.debug(
                    "CA tunnel: sending {} bytes UDP response to {}",
                    len(udp_response),
                    addr,
                )
                try:
                    self._udp_sock.sendto(udp_response, addr)
                except OSError as e:
                    logger.debug("CA tunnel: failed to send UDP response to {}: {}", addr, e)
            else:
                logger.debug("CA tunnel: no search results to relay (response was version-only)")
