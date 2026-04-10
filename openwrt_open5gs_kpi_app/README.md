# Open5GS KPI Collection Tool

A robust Python application for collecting and monitoring KPI metrics from Open5GS 5G Core network functions. It uses Prometheus metrics endpoints, exports aggregated KPIs, and fetches raw network/system counters from an OpenWrt Docker container.

## Features

- **Metric Collection**: Reads from explicit Prometheus metrics endpoints config (via `.env` or `--metrics-endpoints`) to collect (AMF, SMF, UPF).
- **KPI Aggregation**: Extracts high-level KPIs (registration success rate, active UEs, session counts).
- **O-RAN Integration**: Fetches dynamic raw gNB telemetry (E2SM-KPM) published to Prometheus.
- **OpenWrt Raw Metrics**: Pulls interface, network, and system counters directly via `docker exec`.
- **Multiple Modes**: Support for one-shot snapshots, continuous polling (`--watch`), and an HTTP API server (`--server`).
- **Graceful Shutdown**: Handles SIGINT/SIGTERM cleanly.

## Requirements

- **Python**: 3.8+
- **Docker**: Required for OpenWrt raw metrics collection.
- **Dependencies**: `requests >= 2.31.0`. Optional: `Flask >= 2.0.0` (for `--server`), `uv`, `python-dotenv`.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage & Quick Start

### 1. One-Shot Snapshot (Human-readable)
```bash
python app.py
```

### 2. JSON Output
```bash
python app.py --json
```

### 3. Watch Mode (Polls every 5 seconds)
```bash
python app.py --watch 5
```

### 4. HTTP Server Mode
Provides `/health` and `/kpi` endpoints. *Note: Cannot be used with `--watch`.*
```bash
python app.py --server 8080
```

### 5. Automated Traffic Steering
Executes a steering script periodically.
```bash
python app.py --watch 5 --steer-interval 5 --steer-script ./scripts/toggle_route.sh
```

## Command Line Options

| Option | Description |
|--------|-------------|
| `--metrics-endpoints` | Comma-separated host:port list (e.g., `127.0.0.4:9090`). |
| `--timeout` | HTTP request timeout in seconds (default: `2.5`). |
| `--json` | Output JSON instead of a human-readable format. |
| `--watch SECONDS` | Poll interval in seconds (default: `0` / one-time). Mutually exclusive with `--server`. |
| `--server PORT` | Start HTTP API server on the specified port. Mutually exclusive with `--watch`. |
| `--openwrt-container` | OpenWrt Docker container name (default: `openwrt_router`). |
| `--ifaces` | Comma-separated OpenWrt interfaces to monitor. Auto-discovered if omitted. |
| `--no-openwrt` | Disable OpenWrt probing entirely. |
| `--debug` | Enable debug logging to `stderr`. |

*Note: OpenWrt auth options (`--openwrt-user`, `--openwrt-password`, `OPENWRT_USER`, `OPENWRT_PASSWORD`) are currently reserved and unused in raw-only mode.*

## Environment Variables

The application can read configuration from a `.env` file in the working directory (requires `python-dotenv`) or standard OS environment variables:

- `METRICS_ENDPOINTS`: Comma-separated list of metrics endpoints.
- `RAW_METRICS`: Comma-separated metric names to include in raw_metrics output (empty=all).
- `TIMEOUT`: HTTP timeout seconds (default: 2.5).
- `WATCH_INTERVAL`: Poll interval seconds (0 = once, default: 0).
- `OPENWRT_HOST`: OpenWrt host/IP (default: 192.168.142.200).
- `OPENWRT_TIMEOUT`: OpenWrt probe timeout seconds (default: 2.0).
- `OPENWRT_CONTAINER`: OpenWrt Docker container name (default: openwrt_router).
- `OPENWRT_USER`: Reserved for future OpenWrt auth integrations (currently unused).
- `OPENWRT_PASSWORD`: Reserved for future OpenWrt auth integrations (currently unused).
- `OPENWRT_IFACES`: Comma-separated OpenWrt interfaces to include (default: eth0,eth1,br-lan,lo).
- `STEER_INTERVAL`: Trigger automated traffic steering every N seconds (`0` disables steering).
- `STEER_SCRIPT`: Path to traffic steering script.

## Metrics Summary

- **AMF**: Registration requests, success rates (`amf_reg_init_req`, `amf_reg_init_succ`, `amf_reg_success_rate_pct`), registered UEs, connected gNBs.
- **SMF**: Active UEs, PFCP sessions, PFCP peers.
- **UPF**: Active sessions, N3 in/out packets (*Note: N3 GTP counters currently report `0` in some Open5GS builds despite active traffic*).
- **O-RAN gNB**: Telemetry (e.g., `DRB_UEThpUl`, `DRB_UEThpDl`) published by O-RAN RIC xApps.
- **OpenWrt Container**: Per-interface rx/tx stats, CPU (`cpu_stat`), memory (`meminfo`), uptime, load average, and conntrack metrics.

## Testing

Run unit tests with pytest:
```bash
pytest tests/ -v
```
