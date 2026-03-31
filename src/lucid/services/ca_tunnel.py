"""Local UDP-to-TCP forwarder for Channel Access through SSH tunnels.

When LUCID is used remotely with a CA Gateway accessible via SSH tunnel,
CA clients need UDP for PV name search — but SSH only tunnels TCP. This
service runs a local UDP listener that forwards search/data packets
through the TCP tunnel, allowing pyepics/ophyd to work transparently.

Usage:
    The tunnel is configured via LUCID preferences:
        - ca_tunnel_enabled: bool (default False)
        - ca_tunnel_gateway: str (default "localhost:5099")

    When enabled, the service:
        1. Binds a local UDP socket on the gateway port
        2. Forwards incoming UDP packets to the same port via TCP
        3. Relays TCP responses back as UDP to the original sender
        4. Sets EPICS_CA_AUTO_ADDR_LIST=NO and EPICS_CA_ADDR_LIST=localhost:<port>

    This must be started BEFORE any EPICS/ophyd initialization.
"""

from __future__ import annotations

import os
import socket
import threading
from typing import ClassVar

from lucid.utils.logging import logger


class CATunnelService:
    """Singleton service that bridges UDP CA traffic to a TCP tunnel.

    Runs a background thread that listens for UDP packets on a local port
    and forwards them to the same address via TCP. Responses are relayed
    back as UDP to the original sender.
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
        """Start the UDP-to-TCP forwarder.

        Args:
            gateway: The CA Gateway address as host:port. This is both
                the TCP target (reached via SSH tunnel) and the local
                UDP listen address.

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

        # Bind UDP socket
        try:
            self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._udp_sock.bind((self._host, self._port))
            self._udp_sock.settimeout(1.0)  # Allow periodic shutdown checks
        except OSError as e:
            logger.error("Failed to bind UDP socket on {}:{}: {}", self._host, self._port, e)
            self._udp_sock = None
            return False

        self._running = True
        self._thread = threading.Thread(
            target=self._run_forwarder,
            name="ca-tunnel",
            daemon=True,
        )
        self._thread.start()

        logger.info(
            "CA tunnel started: UDP {}:{} -> TCP {}:{}",
            self._host,
            self._port,
            self._host,
            self._port,
        )
        return True

    def stop(self) -> None:
        """Stop the forwarder."""
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

    def _run_forwarder(self) -> None:
        """Main forwarder loop (runs in background thread)."""
        while self._running and self._udp_sock is not None:
            try:
                data, addr = self._udp_sock.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    logger.warning("CA tunnel UDP socket error, stopping")
                break

            # Forward via TCP
            try:
                tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                tcp_sock.settimeout(3.0)
                tcp_sock.connect((self._host, self._port))
                tcp_sock.sendall(data)

                # Read response (CA search replies are small)
                # Use a short timeout — the gateway responds quickly
                tcp_sock.settimeout(2.0)
                chunks = []
                try:
                    while True:
                        chunk = tcp_sock.recv(65536)
                        if not chunk:
                            break
                        chunks.append(chunk)
                        # CA responses are typically a single packet
                        # Don't wait for more if we got data
                        tcp_sock.settimeout(0.1)
                except socket.timeout:
                    pass
                finally:
                    tcp_sock.close()

                if chunks:
                    response = b"".join(chunks)
                    self._udp_sock.sendto(response, addr)

            except (ConnectionRefusedError, ConnectionResetError) as e:
                logger.debug("CA tunnel TCP connection failed: {}", e)
            except socket.timeout:
                logger.debug("CA tunnel TCP timeout for packet from {}", addr)
            except OSError as e:
                if self._running:
                    logger.debug("CA tunnel forward error: {}", e)
