"""Wake-on-LAN magic packet generation and rate-limited transmission."""

import logging
import socket
import threading
import time
from typing import Sequence

log = logging.getLogger("wake_on_prompt.wol")


def build_magic_packet(mac: str) -> bytes:
    """Build a 102-byte Wake-on-LAN magic packet for a MAC address.

    Accepts MAC in common formats: 'XX:XX:XX:XX:XX:XX', 'XX-XX-XX-XX-XX-XX',
    or continuous hex string.
    """
    clean_mac = mac.replace(":", "").replace("-", "").strip()
    if len(clean_mac) != 12:
        raise ValueError(f"Invalid MAC address format: {mac!r}")
    mac_bytes = bytes.fromhex(clean_mac)
    return b"\xff" * 6 + mac_bytes * 16


class WoLSender:
    """Rate-limited Wake-on-LAN transmitter."""

    def __init__(self, cooldown: float = 5.0) -> None:
        self.cooldown = cooldown
        self._lock = threading.Lock()
        self._last_sent: dict[str, float] = {}

    def send(
        self,
        mac: str,
        broadcast_ips: Sequence[str] = ("255.255.255.255",),
        port: int = 9,
    ) -> bool:
        """Send magic packet to target MAC if outside cooldown period.

        Returns True if sent, False if suppressed by rate limit.
        """
        packet = build_magic_packet(mac)
        now = time.time()

        with self._lock:
            last = self._last_sent.get(mac, 0.0)
            if now - last < self.cooldown:
                log.debug("WoL to %s suppressed by rate limit (cooldown=%.1fs)", mac, self.cooldown)
                return False
            self._last_sent[mac] = now

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for ip in broadcast_ips:
                try:
                    s.sendto(packet, (ip, port))
                    log.info("Sent WoL magic packet for %s to %s:%d", mac, ip, port)
                except Exception as exc:
                    log.warning("Failed sending WoL to %s:%d: %s", ip, port, exc)

        return True


# Global default sender
_default_sender = WoLSender()


def send_wol(
    mac: str,
    broadcast_ips: Sequence[str] = ("255.255.255.255",),
    port: int = 9,
) -> bool:
    """Send magic packet using the global rate-limited sender."""
    return _default_sender.send(mac, broadcast_ips, port)
