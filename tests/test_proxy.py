"""Unit tests for wake-on-prompt."""

import http.server
import json
import threading
import time
import urllib.request
import urllib.error
import pytest

from wake_on_prompt.wol import build_magic_packet, WoLSender
from wake_on_prompt.config import ProxyConfig
from wake_on_prompt.proxy import create_proxy_server, is_port_alive


def test_magic_packet_generation():
    mac = "b4:2e:99:a0:5b:01"
    packet = build_magic_packet(mac)
    assert len(packet) == 102
    assert packet[:6] == b"\xff" * 6
    mac_bytes = bytes.fromhex("b42e99a05b01")
    assert packet[6:12] == mac_bytes
    assert packet[12:18] == mac_bytes
    assert packet[96:102] == mac_bytes

    # Dash notation
    assert build_magic_packet("B4-2E-99-A0-5B-01") == packet

    # Invalid MAC formats
    with pytest.raises(ValueError):
        build_magic_packet("invalid-mac")
    with pytest.raises(ValueError):
        build_magic_packet("b4:2e:99")


def test_wol_rate_limiter():
    sender = WoLSender(cooldown=1.0)
    mac = "b4:2e:99:a0:5b:01"

    # First send should succeed
    sent1 = sender.send(mac, broadcast_ips=["127.0.0.1"])
    assert sent1 is True

    # Second send within cooldown should be suppressed
    sent2 = sender.send(mac, broadcast_ips=["127.0.0.1"])
    assert sent2 is False


def test_config_parsing(tmp_path):
    config_file = tmp_path / "config.json"
    data = {
        "broadcast_ips": ["192.168.1.255"],
        "endpoints": [
            {
                "name": "test-sync",
                "listen_port": 9081,
                "target_host": "127.0.0.1",
                "target_port": 9080,
                "mac_address": "b4:2e:99:a0:5b:01",
                "wake_timeout": 5.0,
            }
        ],
    }
    config_file.write_text(json.dumps(data))

    cfg = ProxyConfig.from_file(config_file)
    assert cfg.broadcast_ips == ["192.168.1.255"]
    assert len(cfg.endpoints) == 1
    ep = cfg.endpoints[0]
    assert ep.name == "test-sync"
    assert ep.listen_port == 9081
    assert ep.target_port == 9080
    assert ep.wake_timeout == 5.0


class MockUpstreamHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        msg = b'{"status": "ok", "method": "GET"}'
        self.send_header("Content-Length", str(len(msg)))
        self.end_headers()
        self.wfile.write(msg)

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        resp = {"status": "ok", "echo": body.decode("utf-8")}
        msg = json.dumps(resp).encode("utf-8")
        self.send_header("Content-Length", str(len(msg)))
        self.end_headers()
        self.wfile.write(msg)

    def log_message(self, *args):
        pass


def test_proxy_forwarding_alive():
    # Start mock upstream server on ephemeral port
    upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockUpstreamHandler)
    up_host, up_port = upstream.server_address
    up_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    up_thread.start()

    # Start proxy on ephemeral port
    proxy = create_proxy_server(
        listen_host="127.0.0.1",
        listen_port=0,
        target_host=up_host,
        target_port=up_port,
        mac_address="b4:2e:99:a0:5b:01",
        wake_timeout=2.0,
    )
    p_host, p_port = proxy.server_address
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()

    try:
        # Test GET through proxy
        with urllib.request.urlopen(f"http://{p_host}:{p_port}/test") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode())
            assert data["status"] == "ok"
            assert data["method"] == "GET"

        # Test POST through proxy
        req = urllib.request.Request(
            f"http://{p_host}:{p_port}/echo",
            data=b"hello proxy",
            headers={"Content-Type": "text/plain"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode())
            assert data["echo"] == "hello proxy"
    finally:
        proxy.shutdown()
        upstream.shutdown()


def test_proxy_lazy_wake_sleeping_target():
    # Target port is an unused port that nothing is listening on
    unused_port = 59123
    assert not is_port_alive("127.0.0.1", unused_port)

    # Start proxy with wake_timeout=0.0 (lazy wake)
    proxy = create_proxy_server(
        listen_host="127.0.0.1",
        listen_port=0,
        target_host="127.0.0.1",
        target_port=unused_port,
        mac_address="b4:2e:99:a0:5b:01",
        wake_timeout=0.0,
    )
    p_host, p_port = proxy.server_address
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()

    try:
        start = time.time()
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"http://{p_host}:{p_port}/v1/chat")
        duration = time.time() - start

        assert exc_info.value.code == 502
        # Lazy wake should return immediately (<0.5s) without waiting
        assert duration < 0.5

        err_body = json.loads(exc_info.value.read().decode())
        assert err_body["error"]["code"] == 502
        assert "WoL packet sent" in err_body["error"]["message"]
    finally:
        proxy.shutdown()


def test_proxy_synchronous_wake_waiting():
    unused_port = 59124

    proxy = create_proxy_server(
        listen_host="127.0.0.1",
        listen_port=0,
        target_host="127.0.0.1",
        target_port=unused_port,
        mac_address="b4:2e:99:a0:5b:01",
        wake_timeout=3.0,
    )
    p_host, p_port = proxy.server_address
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()

    upstream_holder = []

    def start_late():
        time.sleep(0.6)
        up = http.server.ThreadingHTTPServer(("127.0.0.1", unused_port), MockUpstreamHandler)
        upstream_holder.append(up)
        up.serve_forever()

    late_thread = threading.Thread(target=start_late, daemon=True)
    late_thread.start()

    try:
        with urllib.request.urlopen(f"http://{p_host}:{p_port}/test") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode())
            assert data["status"] == "ok"
    finally:
        proxy.shutdown()
        if upstream_holder:
            upstream_holder[0].shutdown()
