"""Thread-safe UDP transport for simulator drivers.

Ported from Meridian's ``UnifiedSim.transports.udp`` with the same
responsibilities: one reusable datagram socket per transport, a single
``send(ip, port, data)`` entry point, and a ``close()`` that makes
subsequent sends raise. Drivers own one transport and share it across
every :class:`OutputChannel` they create.

The class is intentionally tiny — the goal is to keep the hot path off the
main thread without adding failure modes we will have to reason about
under load.
"""

from __future__ import annotations

import socket
import threading
from typing import Optional


class UdpTransport:
    """Thread-safe wrapper around a single AF_INET/SOCK_DGRAM socket."""

    def __init__(self, enable_broadcast: bool = False) -> None:
        self._lock = threading.Lock()
        self._sock: Optional[socket.socket] = None
        self._closed = False
        self._enable_broadcast = enable_broadcast
        self._ensure_socket()

    def send(self, ip: str, port: int, data: bytes) -> None:
        """Send ``data`` to ``(ip, port)``. Serialised across threads.

        Raises ``RuntimeError`` if the transport has been closed; raises
        ``OSError`` if the underlying socket call fails.
        """
        if self._closed:
            raise RuntimeError("UDP transport is closed")
        if self._sock is None:
            self._ensure_socket()
            if self._sock is None:
                raise RuntimeError("UDP socket not available")
        # sendto itself is generally thread-safe on modern platforms, but we
        # lock anyway so the internal state (_sock / _closed) stays coherent
        # with any concurrent close() call.
        with self._lock:
            self._sock.sendto(data, (ip, port))

    def close(self) -> None:
        """Close the socket. Idempotent."""
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                finally:
                    self._sock = None
                    self._closed = True
            else:
                self._closed = True

    def _ensure_socket(self) -> None:
        if self._sock is not None:
            return
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # SO_REUSEADDR is a cheap insurance against quick restart TIME_WAIT
        # on platforms that honour it; harmless if unsupported.
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except OSError:
            pass
        if self._enable_broadcast:
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except OSError:
                pass
        self._sock = s
