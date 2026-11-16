"""CLI runner for wake-on-prompt."""

import argparse
import logging
import signal
import sys
import threading
from typing import Sequence
from . import __version__
from .config import EndpointConfig, ProxyConfig
from .proxy import create_proxy_server

log = logging.getLogger("wake_on_prompt")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wake-on-prompt",
        description="Wake-on-Demand reverse proxy for homelab LLMs and GPUs.",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "-c", "--config",
        type=str,
        help="Path to JSON or YAML config file.",
    )
    parser.add_argument(
        "--listen",
        type=int,
        help="Port to listen on (e.g. 8081).",
    )
    parser.add_argument(
        "--listen-host",
        type=str,
        default="0.0.0.0",
        help="Host address to bind listener to (default: 0.0.0.0).",
    )
    parser.add_argument(
        "--target",
        type=str,
        help="Target host:port to proxy to (e.g. 192.168.1.100:8080).",
    )
    parser.add_argument(
        "--mac",
        type=str,
        help="MAC address of target host for WoL packets (e.g. AA:BB:CC:DD:EE:FF).",
    )
    parser.add_argument(
        "--broadcast",
        type=str,
        action="append",
        help="Broadcast IP for WoL packet (can be specified multiple times, default: 255.255.255.255).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for target to wake up before returning 502 (default: 10.0; set 0 for lazy wake).",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )

    return parser.parse_args(argv)


def build_config_from_args(args: argparse.Namespace) -> ProxyConfig:
    if args.config:
        return ProxyConfig.from_file(args.config)

    if not (args.listen and args.target and args.mac):
        raise ValueError(
            "Either --config must be provided, or all of --listen, --target, and --mac must be specified."
        )

    target_parts = args.target.split(":")
    if len(target_parts) != 2:
        raise ValueError(f"Invalid target format: {args.target!r}. Must be 'host:port'.")

    target_host = target_parts[0]
    target_port = int(target_parts[1])
    broadcast_ips = args.broadcast or ["255.255.255.255"]

    endpoint = EndpointConfig(
        name="cli",
        listen_host=args.listen_host,
        listen_port=args.listen,
        target_host=target_host,
        target_port=target_port,
        mac_address=args.mac,
        wake_timeout=args.timeout,
        broadcast_ips=broadcast_ips,
    )

    return ProxyConfig(broadcast_ips=broadcast_ips, endpoints=[endpoint])


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        config = build_config_from_args(args)
    except Exception as exc:
        log.error("Configuration error: %s", exc)
        return 1

    if not config.endpoints:
        log.error("No endpoints configured to proxy.")
        return 1

    servers = []
    threads = []
    stop_event = threading.Event()

    def handle_signal(sig, frame):
        log.info("Shutdown signal received (%s), terminating...", sig)
        stop_event.set()
        for s in servers:
            s.shutdown()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    for ep in config.endpoints:
        server = create_proxy_server(
            listen_host=ep.listen_host,
            listen_port=ep.listen_port,
            target_host=ep.target_host,
            target_port=ep.target_port,
            mac_address=ep.mac_address,
            broadcast_ips=ep.broadcast_ips,
            wake_timeout=ep.wake_timeout,
        )
        servers.append(server)
        t = threading.Thread(
            target=server.serve_forever,
            daemon=True,
            name=f"proxy-{ep.name}",
        )
        threads.append(t)
        t.start()
        log.info(
            "Proxy [%s] listening on %s:%d -> %s:%d (wake_timeout=%.1fs)",
            ep.name,
            ep.listen_host,
            ep.listen_port,
            ep.target_host,
            ep.target_port,
            ep.wake_timeout,
        )

    log.info("Wake-on-Prompt is running. Press Ctrl+C to stop.")

    try:
        while not stop_event.is_set():
            stop_event.wait(timeout=1.0)
    except KeyboardInterrupt:
        handle_signal(signal.SIGINT, None)

    return 0


if __name__ == "__main__":
    sys.exit(main())
