"""HTTP streaming reverse proxy with Wake-on-Demand integration."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import socket
import time
import urllib.error
import urllib.request
from typing import Sequence
from .wol import send_wol

log = logging.getLogger("wake_on_prompt.proxy")

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def is_port_alive(host: str, port: int, timeout: float = 0.3) -> bool:
    """Check if a remote TCP port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def wait_for_target(
    host: str,
    port: int,
    mac: str,
    broadcast_ips: Sequence[str] = ("255.255.255.255",),
    timeout: float = 10.0,
    poll_interval: float = 0.5,
) -> bool:
    """Check if target port is alive; if not, send WoL and poll up to timeout."""
    if is_port_alive(host, port):
        return True

    log.info("Target %s:%d is unreachable/sleeping. Sending WoL...", host, port)
    send_wol(mac, broadcast_ips)

    if timeout <= 0:
        return False

    log.info("Waiting up to %.1fs for %s:%d to wake...", timeout, host, port)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll_interval)
        if is_port_alive(host, port):
            log.info("Target %s:%d is now awake and accepting connections!", host, port)
            return True

    log.warning("Timed out waiting %.1fs for %s:%d to wake", timeout, host, port)
    return False


class ProxyHandler(BaseHTTPRequestHandler):
    """HTTP handler that proxies requests to an upstream target with wake-on-demand."""

    target_host: str = "127.0.0.1"
    target_port: int = 8080
    mac_address: str = ""
    broadcast_ips: Sequence[str] = ("255.255.255.255",)
    wake_timeout: float = 10.0
    buffer_size: int = 2048

    def do_GET(self) -> None:
        self.handle_proxy()

    def do_POST(self) -> None:
        self.handle_proxy()

    def do_HEAD(self) -> None:
        self.handle_proxy()

    def do_OPTIONS(self) -> None:
        self.handle_proxy()

    def do_DELETE(self) -> None:
        self.handle_proxy()

    def do_PUT(self) -> None:
        self.handle_proxy()

    def handle_proxy(self) -> None:
        awake = wait_for_target(
            self.target_host,
            self.target_port,
            self.mac_address,
            self.broadcast_ips,
            self.wake_timeout,
        )

        if not awake:
            self._send_sleep_error()
            return

        target_url = f"http://{self.target_host}:{self.target_port}{self.path}"

        body = None
        content_len = self.headers.get("Content-Length")
        if content_len:
            try:
                body = self.rfile.read(int(content_len))
            except Exception as exc:
                log.error("Failed reading request body from client: %s", exc)
                self.send_error(400, "Bad Request")
                return

        req_headers = {}
        for k, v in self.headers.items():
            if k.lower() not in HOP_BY_HOP_HEADERS and k.lower() != "host":
                req_headers[k] = v
        req_headers["Host"] = f"{self.target_host}:{self.target_port}"

        req = urllib.request.Request(
            target_url,
            data=body,
            headers=req_headers,
            method=self.command,
        )

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                self.send_response(resp.status)
                for h, v in resp.getheaders():
                    if h.lower() not in HOP_BY_HOP_HEADERS and h.lower() != "content-length":
                        self.send_header(h, v)

                cl = resp.headers.get("Content-Length")
                if cl:
                    self.send_header("Content-Length", cl)
                self.end_headers()

                while True:
                    chunk = resp.read(self.buffer_size)
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        log.debug("Client closed connection during streaming response")
                        break

        except urllib.error.HTTPError as exc:
            try:
                self.send_response(exc.code)
                for h, v in exc.headers.items():
                    if h.lower() not in HOP_BY_HOP_HEADERS and h.lower() != "content-length":
                        self.send_header(h, v)
                err_body = exc.read()
                self.send_header("Content-Length", str(len(err_body)))
                self.end_headers()
                self.wfile.write(err_body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        except Exception as exc:
            log.error("Proxy error connecting to %s: %s", target_url, exc)
            try:
                self.send_error(502, f"Bad Gateway: {exc}")
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _send_sleep_error(self) -> None:
        try:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            err_data = {
                "error": {
                    "message": f"Target {self.target_host}:{self.target_port} is sleeping or unreachable. WoL packet sent.",
                    "type": "target_offline",
                    "code": 502,
                }
            }
            msg = json.dumps(err_data).encode("utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format: str, *args) -> None:
        if args and len(args) > 1 and str(args[1]).startswith(("4", "5")):
            log.warning("%s - %s", self.address_string(), format % args)
        else:
            log.debug("%s - %s", self.address_string(), format % args)


def create_proxy_server(
    listen_host: str,
    listen_port: int,
    target_host: str,
    target_port: int,
    mac_address: str,
    broadcast_ips: Sequence[str] = ("255.255.255.255",),
    wake_timeout: float = 10.0,
) -> ThreadingHTTPServer:
    """Create a configured ThreadingHTTPServer instance for a single proxy route."""

    class ConfiguredHandler(ProxyHandler):
        pass

    ConfiguredHandler.target_host = target_host
    ConfiguredHandler.target_port = target_port
    ConfiguredHandler.mac_address = mac_address
    ConfiguredHandler.broadcast_ips = broadcast_ips
    ConfiguredHandler.wake_timeout = wake_timeout

    return ThreadingHTTPServer((listen_host, listen_port), ConfiguredHandler)
