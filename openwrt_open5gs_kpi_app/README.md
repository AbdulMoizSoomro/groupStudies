# Open5GS KPI Collection Tool

A robust Python application for collecting and monitoring KPI metrics from Open5GS 5G Core network functions. This tool discovers Prometheus metrics endpoints from Open5GS configuration and exports aggregated KPIs, plus raw network/system counters from an OpenWrt Docker container.

**Version 2.0** (Refactored with comprehensive error handling, logging, validation, and graceful shutdown)

## Features

- **Auto-discovery**: Reads Open5GS YAML config and discovers all metrics endpoints (AMF, SMF, UPF, MME, etc.)
- **KPI aggregation**: Extracts high-level KPIs (registration success rate, active UEs, session counts)
- **OpenWrt raw metrics**: Pulls interface/network/system counters directly from OpenWrt container `/proc`
- **No local host network math**: `network_kpi` is sourced from OpenWrt, not derived from local ping/throughput calculations
- **OpenWrt integration**: Uses Docker exec against the target OpenWrt container (default: `openwrt_router`)
- **Multiple output formats**: Human-readable (default) or JSON
- **Watch mode**: Continuous polling with configurable interval
- **Structured logging**: Debug/verbose logging with colored output to stderr
- **Graceful shutdown**: Handles SIGINT/SIGTERM cleanly in watch mode
- **Robust error handling**: Validates input, handles missing files/processes gracefully, detailed error reporting

## Requirements

- **Python**: 3.8+
- **Docker**: Required for OpenWrt raw metrics collection (`docker exec` into OpenWrt container)
- **Open5GS**: 5GC must be running with Prometheus metrics enabled
- **Dependencies**: PyYAML >= 6.0, requests >= 2.31.0
  - **Optional**: Flask >= 2.0.0 (for `--server` mode)
  - **Optional**: `uv` (libuv bindings); install via requirements file or separately
    - this provides a high‑performance event loop and is harmless if unused
    - some deployment environments (e.g. OpenWrt cross‑builds) may require it

## Installation

Create a Python virtual environment and install all required packages. The
`requirements.txt` file now includes `uv` along with YAML, requests and
Flask (optional HTTP server).

```bash
cd /path/to/open5gs_kpi_app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If you prefer to install packages individually you can still run e.g.

```bash
pip install PyYAML requests flask uv
```
## Quick Start

### One-shot snapshot (human-readable)

```bash
python app.py
```

Output:
```
Open5GS KPI Snapshot
============================================================
Endpoints
-  amf: http://127.0.0.2:9090/metrics
- smf1: http://127.0.0.3:9090/metrics
- upf1: http://127.0.0.7:9090/metrics

KPIs
- amf_gnbs                    : 0
- amf_registered_ues          : 3
- amf_reg_init_req            : 5
- amf_reg_init_succ           : 5
- amf_reg_success_rate_pct    :   100.00
- smf_active_ues              : 3
- smf_pfcp_peers_active       : 1
- smf_pfcp_sessions_active    : 3
- upf_active_sessions         : 3
- upf_n3_in_pkts              : 45982
- upf_n3_out_pkts             : 46042

Network/System KPIs
{...}
```

### JSON output

```bash
python app.py --json | jq '.kpi'
```

### Watch mode (poll every 5 seconds)

```bash
python app.py --watch 5
```

With debug logging:

```bash
python app.py --watch 5 --debug
```

## Usage

### Basic Options

```bash
python app.py [OPTIONS]

OPTIONS:
  --config PATH              Path to Open5GS config file (default: auto-discover)
  --timeout SECONDS          HTTP request timeout (default: 2.5)
  --json                     Output JSON instead of human-readable format
  --watch SECONDS            Poll interval in seconds (0 = one-time, default: 0)
  --verbose / --debug        Enable debug logging to stderr
  --help                     Show full help with examples
```

### OpenWrt Raw Metrics Options

```bash
  --openwrt-container NAME   OpenWrt Docker container name (default: openwrt_router)
  --ifaces IFACE1,IFACE2     Comma-separated OpenWrt interfaces to include
                             (default: eth0,eth1,br-lan,lo)
```

### OpenWrt Integration

```bash
  --openwrt-host IP          OpenWrt host IP (default: 192.168.142.200)
  --openwrt-timeout SEC      Reserved compatibility timeout flag (default: 2.0)
  --openwrt-user USER        LuCI RPC username (optional)
  --openwrt-password PASS    LuCI RPC password (DEPRECATED, see env vars)
  --no-openwrt               Disable OpenWrt probing entirely
```

## Environment Variables

The application respects environment variables for configuration flexibility and security:

### Configuration

```bash
# Specify Open5GS config file location
export OPEN5GS_CONFIG=/path/to/sample.yaml
python app.py
```

### Credentials (Recommended)

Instead of passing credentials via CLI arguments (visible in `ps` output):

```bash
# OpenWrt LuCI RPC password (more secure than --openwrt-password)
export OPENWRT_PASSWORD=mypassword
python app.py --openwrt-user admin
```

> **Security Note**: Environment variables are still visible to processes running as the same user. For production deployment, consider:
> - Running the tool with minimal privileges
> - Using container secret management systems (Docker secrets, Kubernetes secrets)
> - Restricting file permissions on config files

## Examples

### 1. Monitor registration success and OpenWrt interfaces live

```bash
python app.py --watch 3 --openwrt-container openwrt_router --ifaces eth0,eth1,br-lan
```

### 2. Get JSON snapshot for scripting

```bash
python app.py --json > metrics.json
cat metrics.json | jq '.kpi.amf_reg_success_rate_pct'
```

### 3. Debug endpoint discovery

```bash
python app.py --config /my/config.yaml --debug --no-openwrt
```

Look for messages like:
```
[DEBUG] Discovered endpoint: amf at http://127.0.0.2:9090/metrics
[DEBUG] Discovered endpoint: smf at http://127.0.0.3:9090/metrics
```

### 4. Monitor from non-standard config location

```bash
export OPEN5GS_CONFIG=/home/user/my-open5gs-config.yaml
python app.py --watch 10
```

### 5. Collect OpenWrt raw metrics only (selected interfaces)

```bash
python app.py --json --openwrt-container openwrt_router --ifaces eth0,eth1
```

## Exit Codes

```
0    Success
1    Unhandled exception during collection
2    Configuration error (missing/invalid config file)
3    No metrics endpoints discovered in config
```

## Logging

### Default (INFO level)

Logs only warnings and errors to stderr:

```bash
python app.py --watch 5 2>&1
```

### Verbose (DEBUG level)

Full diagnostic information to stderr:

```bash
python app.py --watch 5 --debug 2>&1
```

Example debug output:
```
2024-03-01 14:23:01,234 [DEBUG] Using config: /home/user/open5gs/build/configs/sample.yaml
2024-03-01 14:23:01,245 [DEBUG] Discovered endpoint: amf at http://127.0.0.2:9090/metrics
2024-03-01 14:23:01,250 [DEBUG] Fetching metrics from amf at http://127.0.0.2:9090/metrics
2024-03-01 14:23:01,312 [INFO] Scraped 47 metrics from amf
2024-03-01 14:23:01,320 [DEBUG] Scraped 15 metrics from upf
```

## Metrics Summary

### AMF (Access and Mobility Management Function)

These values are taken directly from the AMF Prometheus metrics exposed by
Open5GS.  The metric names correspond to the raw counters; the tool simply
sums them across all AMF instances discovered in the config.

- `amf_reg_init_req`: Counter of registration initiation requests
  (`fivegs_amffunction_rm_reginitreq`)
- `amf_reg_init_succ`: Counter of successfully completed registrations
  (`fivegs_amffunction_rm_reginitsucc`)
- `amf_reg_success_rate_pct`: Calculated by the tool as
  ``(amf_reg_init_succ / amf_reg_init_req) * 100``; returns 0 if the request
  counter is zero.
- `amf_registered_ues`: Gauge of currently registered UEs
  (`fivegs_amffunction_rm_registeredsubnbr`)
- `amf_gnbs`: Number of connected gNodeBs (metric name `gnb`)

### SMF (Session Management Function)

Also pulled from Prometheus counters/gauges under the SMF section.
Values are summed across all SMF endpoints.

- `smf_active_ues`: Gauge `ues_active` of currently active UE sessions
- `smf_pfcp_sessions_active`: Gauge `pfcp_sessions_active` (active PFCP
  sessions to UPF)
- `smf_pfcp_peers_active`: Gauge `pfcp_peers_active` (number of UPF peers)

### UPF (User Plane Function)

Metrics are derived from the UPF's Prometheus export.

- `upf_active_sessions`: Gauge `fivegs_upffunction_upf_sessionnbr` for data
  plane sessions.
- `upf_n3_in_pkts`: Counter `fivegs_ep_n3_gtp_indatapktn3upf` (UM-Downlink)
- `upf_n3_out_pkts`: Counter `fivegs_ep_n3_gtp_outdatapktn3upf` (UM-Uplink)

### OpenWrt Raw Metrics (`network_kpi`)

`network_kpi` is now collected from the OpenWrt container itself (not from local host calculations).

#### `network_kpi.network`

- `source`, `container`
- `interfaces.<iface>.rx_bytes`
- `interfaces.<iface>.rx_packets`
- `interfaces.<iface>.rx_errs`
- `interfaces.<iface>.rx_drop`
- `interfaces.<iface>.rx_fifo`
- `interfaces.<iface>.rx_frame`
- `interfaces.<iface>.rx_compressed`
- `interfaces.<iface>.rx_multicast`
- `interfaces.<iface>.tx_bytes`
- `interfaces.<iface>.tx_packets`
- `interfaces.<iface>.tx_errs`
- `interfaces.<iface>.tx_drop`
- `interfaces.<iface>.tx_fifo`
- `interfaces.<iface>.tx_colls` (collisions)
- `interfaces.<iface>.tx_carrier`
- `interfaces.<iface>.tx_compressed`

#### `network_kpi.system`

- `cpu_stat.fields`, `cpu_stat.values` (raw `/proc/stat` values)
- `meminfo.*` (all numeric fields exported by OpenWrt `/proc/meminfo`)
- `uptime.uptime_seconds`, `uptime.idle_seconds`
- `loadavg.load1`, `loadavg.load5`, `loadavg.load15`, `loadavg.running_total_threads`, `loadavg.last_pid`

#### `network_kpi.conntrack`

- `conntrack_count`
- `conntrack_max`

## Troubleshooting

### "No metrics endpoints found in config"

**Problem**: Config file is valid YAML but has no `metrics.server` sections.

**Solution**:
1. Verify `metrics` section in each NF config:
   ```yaml
   amf:
     metrics:
       server:
         - address: 127.0.0.2
           port: 9090
   ```
2. Debug with: `python app.py --debug`

### "Config file not found"

**Problem**: Default hardcoded path doesn't match your setup.

**Solution**:
```bash
export OPEN5GS_CONFIG=/path/to/your/sample.yaml
python app.py
```

Or use explicit flag:
```bash
python app.py --config /path/to/sample.yaml
```

### "Connection failed for amf: HTTPConnectionPool..."

**Problem**: Prometheus endpoint unreachable.

**Causes**:
- Open5GS NF not running
- Metrics disabled in config
- Firewall blocking localhost

**Solution**:
```bash
# Check if service is running and listening
netstat -tlnp | grep 9090

# Verify manual curl works
curl http://127.0.0.2:9090/metrics

# Run with debug to see connection details
python app.py --debug
```

### "Timeout fetching metrics from ..."

**Problem**: NF is running but slow or unresponsive.

**Solution**: Increase timeout:
```bash
python app.py --timeout 5.0
```

### "OpenWrt container metrics are empty"

**Problem**: The specified OpenWrt container is not running or inaccessible.

**Solution**:
- Verify Docker container name (default is `openwrt_router`):
  ```bash
  docker ps --format 'table {{.Names}}\t{{.Status}}'
  ```
- Set the container explicitly:
  ```bash
  python app.py --openwrt-container openwrt_router --json
  ```
- Confirm `/proc` in the OpenWrt container:
  ```bash
  docker exec openwrt_router cat /proc/net/dev
  ```

## Testing

Run unit tests (no services required):

```bash
pip install pytest
pytest tests/test_app.py -v
```

Example output:
```
tests/test_app.py::TestParsePrometheusText::test_parse_single_metric PASSED
tests/test_app.py::TestSummarizeKpis::test_registration_success_rate_calculation PASSED
tests/test_app.py::TestDiscoverMetricsEndpoints::test_discover_valid_endpoints PASSED
...
```

## Development Notes

### Code Structure

- **Parsing functions** (`parse_prometheus_text`, `_read_openwrt_proc_net_dev`): Robust error handling, skip malformed input
- **Collection functions** (`collect_all`, `collect_network_kpis`, `collect_openwrt_raw_metrics`): Aggregate metrics and pull OpenWrt raw counters
- **Validators** (`_positive_float`, `_valid_hostname_or_ip`): Input validation at argument parse time
- **Logging**: Comprehensive logger with DEBUG/INFO levels to stderr

### Error Handling Strategy

1. **Parsing errors**: Skip malformed lines, log at DEBUG level
2. **Connection errors**: Log WARNING, record in `errors` dict, continue collection
3. **Configuration errors**: Log ERROR, fail fast (exit code 2)
4. **Subprocess errors**: Catch timeouts/not-found, return empty results gracefully

### Graceful Shutdown

- SIGINT/SIGTERM captured in watch mode
- Drains current collection, closes sockets cleanly
- Logs shutdown message
- No zombie processes

## Performance Considerations

- **OpenWrt raw metrics collection**: Multiple lightweight `docker exec` calls per collection
- **Metrics endpoint timeout**: 2.5 seconds default (may need increase for slow systems)
- **Watch interval**: Minimum 1 second recommended to avoid overwhelming logs





