"""Configuration loading for wake-on-prompt."""

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
from typing import Any, Sequence

log = logging.getLogger("wake_on_prompt.config")


@dataclass
class EndpointConfig:
    name: str
    listen_port: int
    target_host: str
    target_port: int
    mac_address: str
    listen_host: str = "0.0.0.0"
    wake_timeout: float = 10.0
    broadcast_ips: Sequence[str] = field(default_factory=lambda: ["255.255.255.255"])

    @classmethod
    def from_dict(cls, data: dict[str, Any], default_broadcast: Sequence[str]) -> "EndpointConfig":
        return cls(
            name=data.get("name", f"port-{data.get('listen_port')}"),
            listen_host=data.get("listen_host", "0.0.0.0"),
            listen_port=int(data["listen_port"]),
            target_host=data["target_host"],
            target_port=int(data["target_port"]),
            mac_address=data["mac_address"],
            wake_timeout=float(data.get("wake_timeout", 10.0)),
            broadcast_ips=data.get("broadcast_ips", default_broadcast),
        )


@dataclass
class ProxyConfig:
    broadcast_ips: list[str] = field(default_factory=lambda: ["255.255.255.255"])
    endpoints: list[EndpointConfig] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: str | Path) -> "ProxyConfig":
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Config file not found: {path}")

        raw_text = p.read_text(encoding="utf-8")

        # Try parsing JSON first, or fallback to PyYAML if available
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            try:
                import yaml  # type: ignore

                data = yaml.safe_load(raw_text)
            except ImportError:
                raise ValueError(
                    f"Config file {path} is not valid JSON, and PyYAML is not installed to parse YAML."
                )

        broadcast_ips = data.get("broadcast_ips", ["255.255.255.255"])
        if isinstance(broadcast_ips, str):
            broadcast_ips = [broadcast_ips]

        endpoints = [
            EndpointConfig.from_dict(ep, default_broadcast=broadcast_ips)
            for ep in data.get("endpoints", [])
        ]

        return cls(broadcast_ips=broadcast_ips, endpoints=endpoints)
