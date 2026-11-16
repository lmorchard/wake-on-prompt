"""Wake-on-Prompt: Wake-on-Demand reverse proxy for homelab LLMs and GPUs."""

__version__ = "0.1.0"

from .wol import build_magic_packet, send_wol
from .proxy import is_port_alive, wait_for_target

__all__ = [
    "__version__",
    "build_magic_packet",
    "send_wol",
    "is_port_alive",
    "wait_for_target",
]
