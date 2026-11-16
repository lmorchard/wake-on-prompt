# Wake-on-Prompt

> **Wake-on-Demand reverse proxy for homelab LLMs and GPUs.**  
> Keep your power-hungry gaming PC asleep until you actually send a prompt.

---

## The Problem

A desktop workstation or gaming PC equipped with a modern GPU (RTX 4090, RX 7900 XTX, RX 5700 XT) is an amazing local inference engine, but it draws **100–250W at idle**. Leaving it on 24/7 wastes power and generates heat.

On the other hand, you likely run a low-power, always-on homelab server (mini-PC, Raspberry Pi, or NAS) hosting your agent harnesses, frontends, and gateways (Otari, LiteLLM, Open WebUI, DecafClaw).

**Wake-on-Prompt** sits on your always-on server. When an inference request arrives:
1. It probes the GPU machine's inference port (e.g. `llama-server` on 8080 or `ollama` on 11434).
2. If the machine is asleep, it broadcasts a Wake-on-LAN magic packet.
3. Because modern PCs wake from suspend in **4–6 seconds**, it holds the HTTP connection and proxies the request as soon as the port answers!
4. If the machine fails to wake (or is powered off), it returns `502 Bad Gateway` so gateways like Otari or LiteLLM can seamlessly fall back to cloud providers.

```
                     ┌──────────────────────────────────────────────┐
                     │ Always-On Server (Mini-PC / Pi / Synobian)   │
Client / Agent ─────►│ :8081  Wake-on-Prompt Reverse Proxy          │
(Otari / WebUI)      └───────┬──────────────────────────────┬───────┘
                             │                              │
                     Probes Port 8080               Sends WoL Packet
                     (or holds 10s)                 (if asleep)
                             │                              │
                             ▼                              ▼
                     ┌──────────────────────────────────────────────┐
                     │ Desktop Workstation (Gaming PC / Mandelbrot) │
                     │ GPU Inference (Vulkan / ROCm / llama.cpp)    │
                     └──────────────────────────────────────────────┘
```

---

## Features

- **Zero Runtime Dependencies**: Built entirely with Python's standard library (`http.server`, `urllib`, `socket`). No `pip install` required for core functionality.
- **Full Streaming Support**: Transparently handles Server-Sent Events (`text/event-stream`), chunked transfer encoding, and token-by-token streaming.
- **Dual Wake Modes**:
  - **Synchronous Wake (`wake_timeout = 10.0`)**: Holds client connection up to $N$ seconds while sending WoL. Once the GPU wakes, Turn 1 completes locally for free.
  - **Lazy Wake (`wake_timeout = 0.0`)**: Broadcasts WoL in the background and returns HTTP 502 immediately. Triggers instant cloud failover for Turn 1 with zero latency delay, while warming up the GPU for Turn 2.
- **Rate-Limited Magic Packets**: Built-in broadcast rate limiting prevents UDP packet storms if multiple concurrent requests hit a sleeping host.
- **Client Disconnect Resilient**: Safely absorbs client cancellations (`BrokenPipeError`) when users abort streaming turns.
- **Multi-Endpoint Daemon**: Proxy multiple ports simultaneously (e.g. `llama-server` on 8081, Ollama on 11435).

---

## Quickstart

### 1. Direct CLI (Zero Install)

You can run `wake-on-prompt` directly with Python:

```bash
python3 -m wake_on_prompt.cli \
  --listen 8081 \
  --target 192.168.1.100:8080 \
  --mac b4:2e:99:a0:5b:01 \
  --timeout 10.0 \
  --broadcast 192.168.1.255
```

### 2. Config File

For multiple listeners and targets, create `config.json` (or `config.yaml` with `pip install pyyaml`):

```json
{
  "broadcast_ips": ["255.255.255.255", "192.168.1.255"],
  "endpoints": [
    {
      "name": "llama-server-sync",
      "listen_port": 8081,
      "target_host": "192.168.1.100",
      "target_port": 8080,
      "mac_address": "b4:2e:99:a0:5b:01",
      "wake_timeout": 10.0
    },
    {
      "name": "llama-server-lazy",
      "listen_port": 8082,
      "target_host": "192.168.1.100",
      "target_port": 8080,
      "mac_address": "b4:2e:99:a0:5b:01",
      "wake_timeout": 0.0
    }
  ]
}
```

Run:
```bash
wake-on-prompt --config config.json
```

---

## Docker Deployment

> **Important**: Docker containers require `network_mode: host` so that UDP broadcast packets can reach your physical local subnet.

```yaml
services:
  wake-on-prompt:
    image: ghcr.io/lmorchard/wake-on-prompt:latest
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./config.json:/etc/wake-on-prompt/config.json:ro
```

---

## Gateway Integrations

### Otari (`config.yml`)

Configure Otari to route through Wake-on-Prompt with multi-tier failover:

```yaml
providers:
  desktop_gpu:
    provider_type: openai
    api_base: "http://127.0.0.1:8081/v1"
    models: [flint]

  cloud_fallback:
    provider_type: vertexai
    project: my-project
    location: global

routing:
  policies:
    smart-route:
      select:
        - default: desktop_gpu:flint
      on_failure:
        - cloud_fallback:gemini-2.5-flash
```

### LiteLLM

```yaml
model_list:
  - model_name: local-gpu
    litellm_params:
      model: openai/flint
      api_base: http://127.0.0.1:8081/v1
  - model_name: cloud-fallback
    litellm_params:
      model: gemini/gemini-2.5-flash

router_settings:
  fallbacks:
    - local-gpu: ["cloud-fallback"]
```

---

## License

MIT © [Les Orchard](https://github.com/lmorchard)
