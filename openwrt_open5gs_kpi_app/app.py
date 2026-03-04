#!/usr/bin/env python3
"""
Open5GS KPI Collection Tool

Fetches KPI snapshots from Open5GS Prometheus metrics endpoints, including:
- 5GC function metrics (AMF registration, active UEs, PFCP sessions)
- Network/system statistics (throughput, latency, CPU, memory)
- OpenWrt host reachability and system info (optional)

Exit codes:
  0: Success
  2: Configuration error (missing/invalid config file)
  3: No metrics endpoints discovered
  1: Unhandled exception during collection
"""

import argparse
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
import yaml

try:
    from flask import Flask, jsonify
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

# optional libuv bindings (UV): not required but may improve performance in some
# async frameworks or external tools.  We don't use it directly yet, but it's
# included in requirements for environments that expect it.
try:
    import uv  # type: ignore
    HAS_UV = True
except ImportError:
    HAS_UV = False

# Setup logging
logger = logging.getLogger(__name__)
_log_handler = logging.StreamHandler(sys.stderr)
_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_log_handler)
logger.setLevel(logging.INFO)


def _resolve_config_path(config_arg: Optional[str] = None) -> str:
    """
    Resolve the Open5GS config file path with fallback logic.
    
    Tries in order:
    1. --config argument (if provided)
    2. OPEN5GS_CONFIG environment variable
    3. Default hardcoded path
    
    Returns the resolved path as string.
    Raises FileNotFoundError if no valid config found.
    """
    candidates = [
        config_arg,
        os.environ.get("OPEN5GS_CONFIG"),
        "/home/abdul-moiz-soomro/prj/group_studies/open5gs/build/configs/sample.yaml",
    ]
    
    for candidate in candidates:
        if not candidate:
            continue
        p = Path(candidate)
        if p.exists() and p.is_file():
            logger.debug(f"Using config: {p}")
            return str(p)
    
    # None found - provide helpful error
    env_advice = f"\nSet OPEN5GS_CONFIG=/path/to/config.yaml or use --config flag"
    raise FileNotFoundError(f"No valid Open5GS config found.{env_advice}")


# Will be set by parse_args, used as default for _resolve_config_path in discover_metrics_endpoints
DEFAULT_CONFIG: Optional[str] = None

KPI_KEYS = {
    "amf_reg_init_req": "fivegs_amffunction_rm_reginitreq",
    "amf_reg_init_succ": "fivegs_amffunction_rm_reginitsucc",
    "amf_registered_ues": "fivegs_amffunction_rm_registeredsubnbr",
    "amf_gnbs": "gnb",
    "smf_active_ues": "ues_active",
    "smf_pfcp_sessions_active": "pfcp_sessions_active",
    "smf_pfcp_peers_active": "pfcp_peers_active",
    "upf_active_sessions": "fivegs_upffunction_upf_sessionnbr",
    "upf_n3_in_pkts": "fivegs_ep_n3_gtp_indatapktn3upf",
    "upf_n3_out_pkts": "fivegs_ep_n3_gtp_outdatapktn3upf",
}

PROM_LINE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^}]*\})?\s+(?P<value>[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)$"
)


# Argparse custom type validators
def _positive_float(value: str) -> float:
    """Validate that argument is a positive float."""
    try:
        f = float(value)
        if f <= 0:
            raise ValueError(f"Must be positive, got {f}")
        return f
    except (ValueError, TypeError) as e:
        raise argparse.ArgumentTypeError(f"Invalid positive float: {e}")


def _positive_int(value: str) -> int:
    """Validate that argument is a positive integer."""
    try:
        i = int(value)
        if i <= 0:
            raise ValueError(f"Must be positive, got {i}")
        return i
    except (ValueError, TypeError) as e:
        raise argparse.ArgumentTypeError(f"Invalid positive integer: {e}")


def _valid_hostname_or_ip(value: str) -> str:
    """Validate hostname or IP address for ping/probe target."""
    if not value or len(value) > 255:
        raise argparse.ArgumentTypeError(f"Invalid hostname: {value}")
    # Allow alphanumeric, dots, hyphens, underscores (basic validation)
    if not re.match(r"^[a-zA-Z0-9._\-:]+$", value):
        raise argparse.ArgumentTypeError(f"Invalid characters in hostname: {value}")
    return value


@dataclass(frozen=True)
class Endpoint:
    """Prometheus metrics endpoint for a 5GC network function."""
    nf: str
    address: str
    port: int

    @property
    def url(self) -> str:
        return f"http://{self.address}:{self.port}/metrics"


@dataclass(frozen=True)
class OpenWrtTarget:
    """OpenWrt host configuration for ICMP/HTTP/LuCI probing."""
    host: str
    timeout: float
    container: str = "openwrt_router"
    username: str = ""
    password: str = ""


@dataclass(frozen=True)
class NetworkKpiConfig:
    """Configuration for network/system KPI collection."""
    interfaces: List[str]
    openwrt_container: str


def _run_openwrt_cmd(container: str, cmd: List[str], timeout: float = 5.0) -> str:
    """Run a command inside OpenWrt container and return combined stdout/stderr."""
    try:
        result = subprocess.run(
            ["docker", "exec", container, *cmd],
            capture_output=True,
            text=True,
            check=False,
            timeout=max(1.0, timeout),
        )
        return ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        logger.warning(f"OpenWrt command timeout for container {container}: {' '.join(cmd)}")
        return ""
    except Exception as e:
        logger.warning(f"OpenWrt command failed for container {container}: {e}")
        return ""


def _read_openwrt_proc_net_dev(container: str) -> Dict[str, Dict[str, int]]:
    """Read full per-interface counters from OpenWrt /proc/net/dev."""
    raw = _run_openwrt_cmd(container, ["cat", "/proc/net/dev"], timeout=5.0)
    out: Dict[str, Dict[str, int]] = {}
    lines = raw.splitlines()
    start_idx = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("Inter-|"):
            start_idx = i + 2
            break

    for line in lines[start_idx:]:
        if ":" not in line:
            continue
        iface, rest = line.split(":", 1)
        iface = iface.strip()
        cols = rest.split()
        if len(cols) < 16:
            continue
        try:
            out[iface] = {
                "rx_bytes": int(cols[0]),
                "rx_packets": int(cols[1]),
                "rx_errs": int(cols[2]),
                "rx_drop": int(cols[3]),
                "rx_fifo": int(cols[4]),
                "rx_frame": int(cols[5]),
                "rx_compressed": int(cols[6]),
                "rx_multicast": int(cols[7]),
                "tx_bytes": int(cols[8]),
                "tx_packets": int(cols[9]),
                "tx_errs": int(cols[10]),
                "tx_drop": int(cols[11]),
                "tx_fifo": int(cols[12]),
                "tx_colls": int(cols[13]),
                "tx_carrier": int(cols[14]),
                "tx_compressed": int(cols[15]),
            }
        except ValueError:
            continue
    return out


def _read_openwrt_meminfo(container: str) -> Dict[str, int]:
    """Read all numeric meminfo fields from OpenWrt /proc/meminfo."""
    raw = _run_openwrt_cmd(container, ["cat", "/proc/meminfo"], timeout=5.0)
    out: Dict[str, int] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parts = value.strip().split()
        if not parts:
            continue
        try:
            out[key] = int(parts[0])
        except ValueError:
            continue
    return out


def _read_openwrt_cpu_stat(container: str) -> Dict[str, Any]:
    """Read raw CPU stat fields from OpenWrt /proc/stat without calculations."""
    raw = _run_openwrt_cmd(container, ["cat", "/proc/stat"], timeout=5.0)
    for line in raw.splitlines():
        if line.startswith("cpu "):
            parts = line.split()
            vals: List[int] = []
            for item in parts[1:]:
                try:
                    vals.append(int(item))
                except ValueError:
                    vals.append(0)
            return {
                "fields": [
                    "user",
                    "nice",
                    "system",
                    "idle",
                    "iowait",
                    "irq",
                    "softirq",
                    "steal",
                    "guest",
                    "guest_nice",
                ],
                "values": vals,
            }
    return {}


def _read_openwrt_uptime(container: str) -> Dict[str, float]:
    """Read raw uptime values from OpenWrt /proc/uptime."""
    raw = _run_openwrt_cmd(container, ["cat", "/proc/uptime"], timeout=5.0)
    parts = raw.split()
    if len(parts) < 2:
        return {}
    try:
        return {
            "uptime_seconds": float(parts[0]),
            "idle_seconds": float(parts[1]),
        }
    except ValueError:
        return {}


def _read_openwrt_loadavg(container: str) -> Dict[str, Any]:
    """Read raw loadavg values from OpenWrt /proc/loadavg."""
    raw = _run_openwrt_cmd(container, ["cat", "/proc/loadavg"], timeout=5.0)
    parts = raw.split()
    if len(parts) < 5:
        return {}
    return {
        "load1": parts[0],
        "load5": parts[1],
        "load15": parts[2],
        "running_total_threads": parts[3],
        "last_pid": parts[4],
    }


def _read_openwrt_conntrack(container: str) -> Dict[str, Any]:
    """Read conntrack counters from OpenWrt /proc/sys/net/netfilter."""
    out: Dict[str, Any] = {}
    count_raw = _run_openwrt_cmd(container, ["cat", "/proc/sys/net/netfilter/nf_conntrack_count"], timeout=5.0)
    max_raw = _run_openwrt_cmd(container, ["cat", "/proc/sys/net/netfilter/nf_conntrack_max"], timeout=5.0)
    try:
        if count_raw:
            out["conntrack_count"] = int(count_raw.splitlines()[-1].strip())
    except ValueError:
        pass
    try:
        if max_raw:
            out["conntrack_max"] = int(max_raw.splitlines()[-1].strip())
    except ValueError:
        pass
    return out


def collect_openwrt_raw_metrics(container: str, interfaces: List[str]) -> Dict[str, Any]:
    """Collect raw OpenWrt metrics without local calculations or ping probes."""
    all_ifaces = _read_openwrt_proc_net_dev(container)
    if interfaces:
        iface_map = {iface: all_ifaces.get(iface, {}) for iface in interfaces if iface in all_ifaces}
    else:
        iface_map = all_ifaces

    return {
        "source": "openwrt_container",
        "container": container,
        "interfaces": iface_map,
        "system": {
            "cpu_stat": _read_openwrt_cpu_stat(container),
            "meminfo": _read_openwrt_meminfo(container),
            "uptime": _read_openwrt_uptime(container),
            "loadavg": _read_openwrt_loadavg(container),
        },
        "conntrack": _read_openwrt_conntrack(container),
    }


def discover_metrics_endpoints(config_path: str) -> List[Endpoint]:
    """
    Discover Prometheus metrics endpoints from Open5GS YAML configuration.
    
    Parses the Open5GS config file and extracts all network function metrics
    server endpoints (address, port pairs).
    
    Args:
        config_path: Path to Open5GS sample.yaml config file
        
    Returns:
        List of Endpoint objects discovered in config
        
    Raises:
        FileNotFoundError: If config file does not exist
        yaml.YAMLError: If config is malformed YAML
        
    Note:
        Silently skips network functions without metrics configuration.
    """
    logger.debug(f"Parsing config: {config_path}")
    try:
        with open(config_path, "r", encoding="utf-8") as stream:
            cfg = yaml.safe_load(stream) or {}
    except FileNotFoundError:
        logger.error(f"Config file not found: {config_path}")
        raise
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse YAML config: {e}")
        raise

    endpoints: List[Endpoint] = []
    for nf_name, nf_cfg in cfg.items():
        # skip entries for MME; it often isn't running in lightweight setups
        if nf_name.lower() == "mme":
            logger.debug("Skipping MME entry from config (not running)")
            continue
        if not isinstance(nf_cfg, dict):
            continue
        metrics = nf_cfg.get("metrics")
        if not isinstance(metrics, dict):
            continue
        server = metrics.get("server")
        if not isinstance(server, list):
            continue
        for srv in server:
            if not isinstance(srv, dict):
                continue
            address = srv.get("address")
            port = srv.get("port", 9090)
            if isinstance(address, str) and isinstance(port, int):
                ep = Endpoint(nf=nf_name, address=address, port=port)
                endpoints.append(ep)
                logger.debug(f"Discovered endpoint: {nf_name} at {ep.url}")
    
    logger.info(f"Discovered {len(endpoints)} metrics endpoints")
    return endpoints


def parse_prometheus_text(body: str) -> Dict[str, float]:
    """
    Parse Prometheus text format metrics into a dict.
    
    Extracts metric name and value from each non-comment line.
    Aggregates metrics with the same name by summing their values.
    
    Args:
        body: Prometheus text format response body
        
    Returns:
        Dict mapping metric names to float values
        
    Note:
        Lines starting with '#' (comments) and invalid lines are skipped.
        If a metric appears multiple times, values are summed.
    """
    metrics: Dict[str, float] = {}
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = PROM_LINE.match(line)
        if not match:
            logger.debug(f"Skipping unparseable prometheus line: {line[:50]}")
            continue
        name = match.group("name")
        try:
            value = float(match.group("value"))
            metrics[name] = metrics.get(name, 0.0) + value
        except (ValueError, AttributeError) as e:
            logger.debug(f"Failed to parse metric value: {e}")
    return metrics


def fetch_endpoint_metrics(endpoint: Endpoint, timeout: float) -> Dict[str, float]:
    """
    Fetch and parse Prometheus metrics from an endpoint.
    
    Makes HTTP GET request to the endpoint's /metrics path with SSL verification.
    
    Args:
        endpoint: Prometheus Endpoint to scrape
        timeout: HTTP request timeout in seconds
        
    Returns:
        Dict of metrics parsed from response
        
    Raises:
        requests.RequestException: If HTTP request fails
        ValueError: If response is not valid Prometheus text format
    """
    logger.debug(f"Fetching metrics from {endpoint.nf} at {endpoint.url}")
    try:
        response = requests.get(endpoint.url, timeout=timeout, verify=True)
        response.raise_for_status()
        return parse_prometheus_text(response.text)
    except requests.Timeout:
        logger.warning(f"Timeout fetching metrics from {endpoint.nf}")
        raise
    except requests.ConnectionError as e:
        logger.warning(f"Connection failed for {endpoint.nf}: {e}")
        raise
    except requests.RequestException as e:
        logger.warning(f"HTTP error fetching {endpoint.nf}: {e}")
        raise


def _ping_host(host: str, timeout: float) -> Tuple[bool, Optional[float], str]:
    """
    Ping a host and measure reachability and RTT.
    
    Args:
        host: Hostname or IP to ping
        timeout: Timeout for ping command in seconds
        
    Returns:
        Tuple of (success: bool, rtt_ms: float or None, output: str)
    """
    wait_seconds = str(max(1, int(timeout)))
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", wait_seconds, host],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout + 5,  # Give subprocess extra time
        )
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        match = re.search(r"time[=<]([0-9]+(?:\.[0-9]+)?)\s*ms", output)
        rtt_ms = float(match.group(1)) if match else None
        return result.returncode == 0, rtt_ms, output.strip()
    except subprocess.TimeoutExpired:
        logger.warning(f"Ping subprocess timeout for {host}")
        return False, None, "Subprocess timeout"
    except Exception as e:
        logger.warning(f"Ping failed for {host}: {e}")
        return False, None, str(e)


def _probe_http_openwrt(target: OpenWrtTarget) -> Dict[str, Any]:
    """
    Probe OpenWrt HTTP endpoints for reachability.
    
    Tries multiple URLs to detect OpenWrt web interface.
    
    Args:
        target: OpenWrtTarget configuration
        
    Returns:
        Dict with 'reachable' bool and optional 'url', 'http_status', 'server' fields
    """
    for url in (f"http://{target.host}/cgi-bin/luci/", f"http://{target.host}/"):
        try:
            resp = requests.get(url, timeout=target.timeout, allow_redirects=True, verify=True)
            logger.debug(f"OpenWrt HTTP probe succeeded: {url} -> {resp.status_code}")
            return {
                "reachable": True,
                "url": url,
                "http_status": resp.status_code,
                "server": resp.headers.get("Server", ""),
            }
        except requests.Timeout:
            logger.debug(f"OpenWrt HTTP probe timeout: {url}")
        except requests.ConnectionError as e:
            logger.debug(f"OpenWrt connection error: {url}: {e}")
        except requests.RequestException as e:
            logger.debug(f"OpenWrt HTTP error: {url}: {e}")
    
    logger.debug(f"OpenWrt HTTP probe unreachable: {target.host}")
    return {"reachable": False}


def _fetch_luci_sysinfo(target: OpenWrtTarget) -> Dict[str, Any]:
    """
    Fetch system info from OpenWrt LuCI RPC.
    
    Args:
        target: OpenWrtTarget with username/password credentials
        
    Returns:
        Dict of system info from LuCI RPC, or empty dict if auth fails
    """
    if not target.username or not target.password:
        return {}

    try:
        auth_url = f"http://{target.host}/cgi-bin/luci/rpc/auth"
        login_payload = {"id": 1, "method": "login", "params": [target.username, target.password]}
        auth_resp = requests.post(auth_url, json=login_payload, timeout=target.timeout, verify=True)
        auth_resp.raise_for_status()
        token = (auth_resp.json() or {}).get("result")
        if not isinstance(token, str) or not token:
            logger.warning(f"OpenWrt LuCI login failed: no token in response")
            return {}

        sys_url = f"http://{target.host}/cgi-bin/luci/rpc/sys?auth={token}"
        sys_payload = {"id": 1, "method": "info", "params": []}
        sys_resp = requests.post(sys_url, json=sys_payload, timeout=target.timeout, verify=True)
        sys_resp.raise_for_status()
        result = (sys_resp.json() or {}).get("result")
        if isinstance(result, dict):
            logger.debug(f"OpenWrt LuCI sysinfo retrieved")
            return result
        return {}
    except requests.Timeout:
        logger.warning(f"OpenWrt LuCI RPC timeout")
        return {}
    except requests.RequestException as e:
        logger.warning(f"OpenWrt LuCI RPC failed: {e}")
        return {}
    except Exception as e:
        logger.warning(f"OpenWrt LuCI parsing error: {e}")
        return {}


def fetch_openwrt_info(target: OpenWrtTarget) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    Fetch reachability, HTTP, and optional system info from OpenWrt host.
    
    Attempts multiple probe methods:
    1. ICMP ping for reachability and RTT
    2. HTTP probe to detect web interface
    3. LuCI RPC login and sysinfo (if credentials provided)
    
    Args:
        target: OpenWrtTarget configuration
        
    Returns:
        Tuple of (info_dict, error_string or None)
        Info dict always contains 'host' and at minimum 'icmp_reachable'
    """
    info: Dict[str, Any] = {
        "host": target.host,
        "container": target.container,
    }
    try:
        raw = collect_openwrt_raw_metrics(target.container, interfaces=[])
        info["interfaces"] = list(raw.get("interfaces", {}).keys())
        return info, None
    except Exception as exc:
        err = f"OpenWrt raw collection failed: {type(exc).__name__}: {exc}"
        logger.warning(err)
        return info, err


def _read_text(path: str) -> str:
    """
    Read text file from filesystem.
    
    Args:
        path: File path to read
        
    Returns:
        File contents as string
        
    Raises:
        FileNotFoundError: If /proc path does not exist (e.g., container environment)
        IOError: If file cannot be read
    """
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return stream.read()
    except FileNotFoundError:
        if "/proc" in path:
            logger.error(f"Linux /proc not available: {path} (container or non-Linux?)")
        raise


def _read_proc_net_dev() -> Dict[str, Dict[str, int]]:
    """
    Parse /proc/net/dev to extract interface statistics.
    
    Returns:
        Dict mapping interface names to dicts of RX/TX byte/packet/error counters
        
    Raises:
        FileNotFoundError: If /proc/net/dev not available
    """
    try:
        data = _read_text("/proc/net/dev")
    except FileNotFoundError:
        logger.error("/proc/net/dev not available")
        raise
    
    out: Dict[str, Dict[str, int]] = {}
    for line in data.splitlines()[2:]:  # Skip header lines
        if ":" not in line:
            continue
        iface, rest = line.split(":", 1)
        iface = iface.strip()
        cols = rest.split()
        if len(cols) < 16:
            logger.debug(f"Skipping malformed /proc/net/dev line for {iface}")
            continue
        
        try:
            out[iface] = {
                "rx_bytes": int(cols[0]),
                "rx_packets": int(cols[1]),
                "rx_errs": int(cols[2]),
                "rx_drop": int(cols[3]),
                "tx_bytes": int(cols[8]),
                "tx_packets": int(cols[9]),
                "tx_errs": int(cols[10]),
                "tx_drop": int(cols[11]),
            }
        except (ValueError, IndexError) as e:
            logger.warning(f"Failed to parse /proc/net/dev for {iface}: {e}")
            continue
    
    return out


def _read_cpu_usage_pct(sample_window_s: float = 0.5) -> Optional[float]:
    """
    Measure CPU usage percentage over a sample window.
    
    Reads /proc/stat twice with a delay and calculates the % of time
    spent not idle.
    
    Args:
        sample_window_s: Time to wait between samples (seconds)
        
    Returns:
        CPU usage as percentage (0-100), or None if calculation fails
    """
    def _read_cpu() -> Tuple[int, int]:
        try:
            first = _read_text("/proc/stat").splitlines()[0]
            parts = first.split()
            values = []
            for v in parts[1:]:
                try:
                    values.append(int(v))
                except ValueError:
                    logger.debug(f"Skipping non-integer CPU stat: {v}")
                    continue
            if len(values) < 4:
                raise ValueError(f"Expected at least 4 CPU values, got {len(values)}")
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            total = sum(values)
            return idle, total
        except Exception as e:
            logger.warning(f"Failed to read /proc/stat: {e}")
            raise

    try:
        idle1, total1 = _read_cpu()
        time.sleep(max(0.1, sample_window_s))
        idle2, total2 = _read_cpu()
        idle_delta = idle2 - idle1
        total_delta = total2 - total1
        if total_delta <= 0:
            logger.warning(f"CPU stat delta invalid: total_delta={total_delta}")
            return None
        return (1.0 - (idle_delta / total_delta)) * 100.0
    except Exception as e:
        logger.debug(f"CPU usage measurement failed: {e}")
        return None


def _read_memory_usage() -> Dict[str, Any]:
    """
    Extract memory statistics from /proc/meminfo.
    
    Returns:
        Dict with 'mem_total_kb', 'mem_available_kb', 'mem_used_kb', 'mem_used_pct'
        Returns empty dict if /proc/meminfo unavailable or unparseable.
    """
    info: Dict[str, int] = {}
    try:
        for line in _read_text("/proc/meminfo").splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            parts = value.strip().split()
            if not parts:
                continue
            try:
                info[key] = int(parts[0])
            except ValueError:
                logger.debug(f"Skipping non-integer meminfo value: {key}={value}")
                continue
    except FileNotFoundError:
        logger.debug("/proc/meminfo not available")
        return {}
    except Exception as e:
        logger.warning(f"Failed to read /proc/meminfo: {e}")
        return {}

    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", 0)
    used = max(0, total - available)
    pct = (used / total * 100.0) if total > 0 else None
    return {
        "mem_total_kb": total,
        "mem_available_kb": available,
        "mem_used_kb": used,
        "mem_used_pct": pct,
    }


def _read_conntrack_pressure() -> Dict[str, Any]:
    """
    Read connection tracking and TCP connection state.
    
    Returns:
        Dict with 'conntrack_count', 'conntrack_max', 'conntrack_usage_pct', 'tcp_established'
        Missing fields are omitted if data unavailable.
    """
    data: Dict[str, Any] = {}
    try:
        count = int(_read_text("/proc/sys/net/netfilter/nf_conntrack_count").strip())
        maxv = int(_read_text("/proc/sys/net/netfilter/nf_conntrack_max").strip())
        data["conntrack_count"] = count
        data["conntrack_max"] = maxv
        data["conntrack_usage_pct"] = (count / maxv * 100.0) if maxv > 0 else None
        logger.debug(f"Conntrack: {count}/{maxv}")
    except FileNotFoundError:
        logger.debug("Conntrack not available (may not be kernel module loaded)")
    except (ValueError, IOError) as e:
        logger.warning(f"Failed to read conntrack: {e}")

    try:
        result = subprocess.run(["ss", "-s"], capture_output=True, text=True, check=False, timeout=5)
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        match = re.search(r"estab\s+(\d+)", output, flags=re.IGNORECASE)
        if match:
            data["tcp_established"] = int(match.group(1))
            logger.debug(f"TCP established: {data['tcp_established']}")
    except subprocess.TimeoutExpired:
        logger.warning("ss command timeout")
    except FileNotFoundError:
        logger.debug("ss command not available")
    except Exception as e:
        logger.warning(f"Failed to read TCP state: {e}")
    
    return data


def _run_cmd(args: List[str]) -> str:
    """
    Run a shell command and return combined stdout+stderr.
    
    Args:
        args: Command and arguments as list
        
    Returns:
        Combined stdout and stderr as string
    """
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=10)
        return ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        logger.warning(f"Timeout running command: {args[0]}")
        return ""
    except FileNotFoundError:
        logger.debug(f"Command not found: {args[0]}")
        return ""
    except Exception as e:
        logger.warning(f"Failed to run command {args[0]}: {e}")
        return ""


def _parse_ip_link_detailed(iface: str) -> Dict[str, Any]:
    """
    Parse detailed interface statistics from 'ip link show' command.
    
    Extracts RX/TX error counts and queue length.
    
    Args:
        iface: Interface name
        
    Returns:
        Dict with 'tx_queue_len' and RX/TX error counters (if present in output)
    """
    raw = _run_cmd(["ip", "-s", "-s", "link", "show", "dev", iface])
    out: Dict[str, Any] = {}

    qlen_match = re.search(r"\bqlen\s+(\d+)", raw)
    if qlen_match:
        try:
            out["tx_queue_len"] = int(qlen_match.group(1))
        except (ValueError, IndexError):
            logger.debug(f"Failed to parse queue length for {iface}")

    crc_line = re.search(r"RX errors:\s+length\s+crc\s+frame\s+fifo\s+missed\s*\n\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", raw)
    if crc_line:
        try:
            out["rx_err_length"] = int(crc_line.group(1))
            out["rx_err_crc"] = int(crc_line.group(2))
            out["rx_err_frame"] = int(crc_line.group(3))
            out["rx_err_fifo"] = int(crc_line.group(4))
            out["rx_err_missed"] = int(crc_line.group(5))
        except (ValueError, IndexError) as e:
            logger.debug(f"Failed to parse RX errors for {iface}: {e}")

    tx_err_line = re.search(r"TX errors:\s+aborted\s+fifo\s+window\s+heartbeat\s+transns\s*\n\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", raw)
    if tx_err_line:
        try:
            out["tx_err_aborted"] = int(tx_err_line.group(1))
            out["tx_err_fifo"] = int(tx_err_line.group(2))
            out["tx_err_window"] = int(tx_err_line.group(3))
            out["tx_err_heartbeat"] = int(tx_err_line.group(4))
            out["tx_err_transns"] = int(tx_err_line.group(5))
        except (ValueError, IndexError) as e:
            logger.debug(f"Failed to parse TX errors for {iface}: {e}")

    return out


def _parse_tc_qdisc(iface: str) -> Dict[str, Any]:
    """
    Parse traffic control qdisc statistics from 'tc qdisc show' command.
    
    Extracts sent packets/bytes, drops, overlimits, requeues, backlog.
    
    Args:
        iface: Interface name
        
    Returns:
        Dict with qdisc statistics (if present in output)
    """
    raw = _run_cmd(["tc", "-s", "qdisc", "show", "dev", iface])
    out: Dict[str, Any] = {}

    sent_match = re.search(
        r"Sent\s+(\d+)\s+bytes\s+(\d+)\s+pkt\s+\(dropped\s+(\d+),\s+overlimits\s+(\d+)\s+requeues\s+(\d+)\)",
        raw,
    )
    if sent_match:
        try:
            out["qdisc_sent_bytes"] = int(sent_match.group(1))
            out["qdisc_sent_packets"] = int(sent_match.group(2))
            out["qdisc_dropped"] = int(sent_match.group(3))
            out["qdisc_overlimits"] = int(sent_match.group(4))
            out["qdisc_requeues"] = int(sent_match.group(5))
        except (ValueError, IndexError) as e:
            logger.debug(f"Failed to parse qdisc sent stats for {iface}: {e}")

    backlog_match = re.search(r"backlog\s+(\d+)b\s+(\d+)p", raw)
    if backlog_match:
        try:
            out["qdisc_backlog_bytes"] = int(backlog_match.group(1))
            out["qdisc_backlog_packets"] = int(backlog_match.group(2))
        except (ValueError, IndexError) as e:
            logger.debug(f"Failed to parse qdisc backlog for {iface}: {e}")

    return out


def _ping_stats(host: str, count: int, timeout_s: float) -> Dict[str, Any]:
    """
    Collect ping statistics (loss, RTT, jitter) to a target host.
    
    Args:
        host: Hostname or IP to ping
        count: Number of ping packets to send
        timeout_s: Timeout for each ping packet (seconds)
        
    Returns:
        Dict with 'ping_success', 'ping_tx_packets', 'ping_rx_packets', 'ping_loss_pct',
        'ping_rtt_min_ms', 'ping_rtt_avg_ms', 'ping_rtt_max_ms', 'ping_jitter_ms'
        (missing fields omitted if ping fails or stats unavailable)
    """
    count = max(1, count)
    timeout_s = max(1, int(timeout_s))
    try:
        result = subprocess.run(
            ["ping", "-c", str(count), "-W", str(timeout_s), host],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s + 10,
        )
        output = (result.stdout or "") + "\n" + (result.stderr or "")
    except subprocess.TimeoutExpired:
        logger.warning(f"Ping subprocess timeout for {host}")
        return {"target": host, "ping_success": False}
    except Exception as e:
        logger.warning(f"Ping failed for {host}: {e}")
        return {"target": host, "ping_success": False}
    
    data: Dict[str, Any] = {
        "target": host,
        "ping_success": result.returncode == 0,
    }

    try:
        txrx = re.search(r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets )?received", output)
        if txrx:
            tx = int(txrx.group(1))
            rx = int(txrx.group(2))
            data["ping_tx_packets"] = tx
            data["ping_rx_packets"] = rx
            data["ping_loss_pct"] = ((tx - rx) / tx * 100.0) if tx > 0 else None
    except (ValueError, IndexError) as e:
        logger.debug(f"Failed to parse ping tx/rx for {host}: {e}")

    try:
        rtt = re.search(r"rtt [^=]+= ([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+) ms", output)
        if rtt:
            data["ping_rtt_min_ms"] = float(rtt.group(1))
            data["ping_rtt_avg_ms"] = float(rtt.group(2))
            data["ping_rtt_max_ms"] = float(rtt.group(3))
            data["ping_jitter_ms"] = float(rtt.group(4))
    except (ValueError, IndexError) as e:
        logger.debug(f"Failed to parse ping RTT for {host}: {e}")

    return data


def collect_network_kpis(cfg: NetworkKpiConfig) -> Dict[str, Any]:
    """
    Collect network and system KPIs with two samples separated by a delay.
    
    Measures per-interface throughput, errors, latency, and system CPU/memory.
    
    Args:
        cfg: NetworkKpiConfig with interface list and probe targets
        
    Returns:
        Dict with 'network' (interfaces, throughput, ping stats) and 'system' / 'conntrack' sections
        Missing sections (e.g., if /proc unavailable) are gracefully omitted.
    """
    raw = collect_openwrt_raw_metrics(cfg.openwrt_container, cfg.interfaces)
    return {
        "network": {
            "source": raw.get("source", "openwrt_container"),
            "container": raw.get("container", cfg.openwrt_container),
            "interfaces": raw.get("interfaces", {}),
        },
        "system": raw.get("system", {}),
        "conntrack": raw.get("conntrack", {}),
    }


def collect_all(endpoints: Iterable[Endpoint], timeout: float) -> Tuple[Dict[str, Dict[str, float]], Dict[str, str]]:
    """
    Scrape Prometheus metrics from all discovered endpoints.
    
    Args:
        endpoints: Iterable of Endpoint objects to scrape
        timeout: HTTP request timeout in seconds
        
    Returns:
        Tuple of (per_nf_metrics, errors)
        - per_nf_metrics: Dict mapping NF name to metrics dict
        - errors: Dict mapping NF name to error string if fetch failed
    """
    per_nf: Dict[str, Dict[str, float]] = {}
    errors: Dict[str, str] = {}
    for endpoint in endpoints:
        try:
            metrics = fetch_endpoint_metrics(endpoint, timeout)
            per_nf[endpoint.nf] = metrics
            logger.info(f"Scraped {len(metrics)} metrics from {endpoint.nf}")
        except Exception as exc:
            err_msg = f"{type(exc).__name__}: {exc}"
            errors[endpoint.nf] = err_msg
            logger.warning(f"Failed to scrape {endpoint.nf}: {err_msg}")
    return per_nf, errors


def summarize_kpis(per_nf: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    """
    Extract and aggregate high-level KPIs from low-level metrics.
    
    Maps metric names (from KPI_KEYS) to human-readable aliases.
    Sums metrics across all network functions.
    Calculates derived metrics (e.g., registration success rate).
    
    Args:
        per_nf: Dict mapping NF name to dict of metrics
        
    Returns:
        Dict of aggregated KPI values with aliases as keys
    """
    merged: Dict[str, float] = {}
    for nf_metrics in per_nf.values():
        for metric_name, value in nf_metrics.items():
            merged[metric_name] = merged.get(metric_name, 0.0) + value

    summary: Dict[str, float] = {}
    for alias, metric_name in KPI_KEYS.items():
        summary[alias] = merged.get(metric_name, 0.0)

    req = summary.get("amf_reg_init_req", 0.0)
    succ = summary.get("amf_reg_init_succ", 0.0)
    summary["amf_reg_success_rate_pct"] = (succ / req * 100.0) if req > 0 else 0.0
    return summary


def print_human(
    endpoints: List[Endpoint],
    summary: Dict[str, float],
    errors: Dict[str, str],
    openwrt: Dict[str, Any],
    openwrt_error: Optional[str],
    network_kpi: Optional[Dict[str, Any]],
) -> None:
    """
    Print human-readable KPI snapshot to stdout.
    
    Args:
        endpoints: List of Endpoint objects (for reference)
        summary: Aggregated KPI dict
        errors: Per-NF error messages
        openwrt: OpenWrt probe results
        openwrt_error: OpenWrt probe error message (if any)
        network_kpi: Network and system KPI dict
    """
    print("Open5GS KPI Snapshot")
    print("=" * 60)
    print("Endpoints")
    for endpoint in endpoints:
        print(f"- {endpoint.nf:>4}: {endpoint.url}")

    print("\nKPIs")
    for key in sorted(summary.keys()):
        val = summary[key]
        if key.endswith("_pct"):
            print(f"- {key:30s}: {val:8.2f}")
        elif abs(val - int(val)) < 1e-9:
            print(f"- {key:30s}: {int(val)}")
        else:
            print(f"- {key:30s}: {val:.4f}")

    if openwrt:
        print("\nOpenWrt")
        for key in sorted(openwrt.keys()):
            value = openwrt[key]
            if isinstance(value, (dict, list)):
                value = json.dumps(value, sort_keys=True)
            print(f"- {key:30s}: {value}")

    if network_kpi:
        print("\nNetwork/System KPIs")
        print(json.dumps(network_kpi, indent=2, sort_keys=True))

    if errors:
        print("\nErrors")
        for nf, err in errors.items():
            print(f"- {nf}: {err}")
            logger.error(f"Collection error [{nf}]: {err}")
    if openwrt_error:
        print("\nOpenWrt Error")
        print(f"- {openwrt_error}")
        logger.warning(f"OpenWrt probe error: {openwrt_error}")


def parse_args() -> argparse.Namespace:
    """Parse and validate command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Fetch KPI snapshot from Open5GS metrics endpoints",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment Variables:
  OPEN5GS_CONFIG        Path to Open5GS config (overrides default)
  OPENWRT_PASSWORD      OpenWrt LuCI RPC password (more secure than --openwrt-password)

Examples:
  %(prog)s                           # One-time snapshot, human-readable
  %(prog)s --json                    # One-time snapshot, JSON output
  %(prog)s --watch 5                 # Poll metrics every 5 seconds
  %(prog)s --server 8080             # Start HTTP server on port 8080
  %(prog)s --debug --watch 2         # Poll with debug logging
        """,
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to Open5GS YAML config (default: OPEN5GS_CONFIG env or hardcoded path)",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=2.5,
        help="HTTP timeout seconds (default: 2.5)",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON instead of human-readable")
    parser.add_argument(
        "--watch",
        type=int,
        default=0,
        help="Poll interval seconds (0 = once, default: 0)",
    )
    parser.add_argument(
        "--server",
        type=int,
        default=0,
        help="Start HTTP server on PORT (e.g., 8080). Incompatible with --watch",
    )
    parser.add_argument(
        "--openwrt-host",
        type=_valid_hostname_or_ip,
        default="192.168.142.200",
        help="OpenWrt host/IP (default: 192.168.142.200)",
    )
    parser.add_argument(
        "--openwrt-timeout",
        type=_positive_float,
        default=2.0,
        help="OpenWrt probe timeout seconds (default: 2.0)",
    )
    parser.add_argument(
        "--openwrt-container",
        default="openwrt_router",
        help="OpenWrt Docker container name for raw metrics (default: openwrt_router)",
    )
    parser.add_argument(
        "--openwrt-user",
        default="",
        help="OpenWrt LuCI RPC username (optional)",
    )
    parser.add_argument(
        "--openwrt-password",
        default="",
        help="OpenWrt LuCI RPC password (DEPRECATED: use OPENWRT_PASSWORD env var instead)",
    )
    parser.add_argument(
        "--no-openwrt",
        action="store_true",
        help="Disable OpenWrt probing",
    )
    parser.add_argument(
        "--ifaces",
        default="eth0,eth1,br-lan,lo",
        help="Comma-separated OpenWrt interfaces to include (default: eth0,eth1,br-lan,lo)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging (DEBUG level)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging (alias for --verbose)",
    )

    args = parser.parse_args()

    # Validation: --server and --watch are mutually exclusive
    if args.server and args.watch:
        parser.error("--server and --watch cannot be used together")

    # Set logging level based on flags
    if args.verbose or args.debug:
        logger.setLevel(logging.DEBUG)
    
    # Read password from env var if not provided via CLI
    if not args.openwrt_password:
        args.openwrt_password = os.environ.get("OPENWRT_PASSWORD", "")
    elif os.environ.get("OPENWRT_PASSWORD"):
        logger.warning("OPENWRT_PASSWORD env var is set but --openwrt-password CLI arg takes precedence")

    return args


def create_http_server(config_path: str, args: argparse.Namespace) -> "Flask":
    """
    Create Flask HTTP server for exposing KPI endpoint.
    
    Args:
        config_path: Resolved path to Open5GS config
        args: Parsed arguments for collection settings
        
    Returns:
        Flask app instance with /health and /kpi endpoints
        
    Raises:
        RuntimeError: If Flask is not installed
    """
    if not HAS_FLASK:
        raise RuntimeError(
            "Flask is required for --server mode. Install with: pip install flask"
        )
    
    app = Flask(__name__)
    endpoints_cache: Dict[str, Any] = {}
    
    def collect_kpi_snapshot() -> Dict[str, Any]:
        """Collect fresh KPI snapshot."""
        try:
            endpoints = endpoints_cache.get("endpoints", [])
            if not endpoints:
                endpoints = discover_metrics_endpoints(config_path)
                endpoints_cache["endpoints"] = endpoints
            
            per_nf, errors = collect_all(endpoints, timeout=args.timeout)
            summary = summarize_kpis(per_nf)
            
            interfaces = [item.strip() for item in args.ifaces.split(",") if item.strip()]
            try:
                network_kpi = collect_network_kpis(
                    NetworkKpiConfig(
                        interfaces=interfaces,
                        openwrt_container=args.openwrt_container,
                    )
                )
            except Exception as e:
                logger.warning(f"Network KPI collection failed: {e}")
                network_kpi = {}
            
            openwrt: Dict[str, Any] = {}
            openwrt_error: Optional[str] = None
            if not args.no_openwrt and args.openwrt_host:
                target = OpenWrtTarget(
                    host=args.openwrt_host,
                    timeout=args.openwrt_timeout,
                    container=args.openwrt_container,
                    username=args.openwrt_user,
                    password=args.openwrt_password,
                )
                openwrt, openwrt_error = fetch_openwrt_info(target)
            
            payload = {
                "timestamp": int(time.time()),
                "kpi": summary,
                "network_kpi": network_kpi,
                "errors": errors,
                "openwrt": openwrt,
            }
            if openwrt_error:
                payload["openwrt_error"] = openwrt_error
            
            return payload
        except Exception as e:
            logger.error(f"KPI collection failed: {e}", exc_info=True)
            return {
                "timestamp": int(time.time()),
                "error": str(e),
                "kpi": {},
                "errors": {"collection": str(e)},
            }
    
    @app.route("/health")
    def health() -> Dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok"}
    
    @app.route("/kpi")
    def kpi() -> Dict[str, Any]:
        """KPI metrics endpoint (JSON)."""
        payload = collect_kpi_snapshot()
        return jsonify(payload)
    
    return app


def run_http_server(config_path: str, args: argparse.Namespace, port: int) -> int:
    """
    Run HTTP server indefinitely.
    
    Args:
        config_path: Resolved path to Open5GS config
        args: Parsed arguments
        port: Port to listen on
        
    Returns:
        Exit code (0 = clean shutdown, 1 = error)
    """
    logger.info(f"Starting HTTP server on port {port}")
    logger.info("Endpoints:")
    logger.info(f"  /health  - Health check")
    logger.info(f"  /kpi     - KPI metrics (JSON)")
    
    try:
        app = create_http_server(config_path, args)
        # Disable Flask's default logging (too verbose)
        flask_logger = logging.getLogger("werkzeug")
        flask_logger.setLevel(logging.WARNING)
        
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
        return 0
    except KeyboardInterrupt:
        logger.info("HTTP server interrupted")
        return 0
    except Exception as e:
        logger.error(f"HTTP server failed: {e}", exc_info=True)
        print(f"Error: {e}", file=sys.stderr)
        return 1


def main() -> int:
    """
    Main entry point. Orchestrates config discovery, endpoint collection, 
    metrics scraping, and output formatting.
    
    Handles graceful shutdown via SIGINT/SIGTERM in watch mode.
    
    Returns:
        Exit code (0: success, 2: config error, 3: no endpoints, 1: unhandled exception)
    """
    args = parse_args()

    logger.info("=" * 60)
    logger.info("Open5GS KPI Collection Tool")
    logger.info("=" * 60)

    # Resolve config path with fallback logic
    try:
        config_path = _resolve_config_path(args.config)
    except FileNotFoundError as e:
        logger.error(f"Config resolution failed: {e}")
        print(f"Error: {e}", file=sys.stderr)
        return 2

    # Handle HTTP server mode
    if args.server:
        try:
            endpoints = discover_metrics_endpoints(config_path)
            if not endpoints:
                logger.error("No metrics endpoints discovered in config")
                print("Error: No metrics endpoints found in config", file=sys.stderr)
                return 3
        except Exception as exc:
            logger.error(f"Failed to parse config: {exc}")
            print(f"Error: Failed to parse config: {exc}", file=sys.stderr)
            return 2
        return run_http_server(config_path, args, args.server)

    # Discover endpoints from config
    try:
        endpoints = discover_metrics_endpoints(config_path)
    except FileNotFoundError:
        logger.error(f"Config file not found: {config_path}")
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        return 2
    except Exception as exc:
        logger.error(f"Failed to parse config: {exc}")
        print(f"Error: Failed to parse config: {exc}", file=sys.stderr)
        return 2

    if not endpoints:
        logger.error("No metrics endpoints discovered in config")
        print("Error: No metrics endpoints found in config", file=sys.stderr)
        return 3

    # Setup graceful shutdown handling
    shutdown_event = False

    def signal_handler(signum: int, frame: Any) -> None:
        nonlocal shutdown_event
        sig_name = signal.Signals(signum).name
        logger.info(f"Received signal {sig_name} ({signum}), shutting down gracefully...")
        shutdown_event = True

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Main collection loop
    iteration = 0
    try:
        while True:
            iteration += 1
            logger.debug(f"Starting collection iteration {iteration}")

            try:
                per_nf, errors = collect_all(endpoints, timeout=args.timeout)
                summary = summarize_kpis(per_nf)
                
                interfaces = [item.strip() for item in args.ifaces.split(",") if item.strip()]
                try:
                    network_kpi = collect_network_kpis(
                        NetworkKpiConfig(
                            interfaces=interfaces,
                            openwrt_container=args.openwrt_container,
                        )
                    )
                except Exception as e:
                    logger.warning(f"Network KPI collection failed: {e}")
                    network_kpi = {}

                openwrt: Dict[str, Any] = {}
                openwrt_error: Optional[str] = None
                if not args.no_openwrt and args.openwrt_host:
                    target = OpenWrtTarget(
                        host=args.openwrt_host,
                        timeout=args.openwrt_timeout,
                        container=args.openwrt_container,
                        username=args.openwrt_user,
                        password=args.openwrt_password,
                    )
                    openwrt, openwrt_error = fetch_openwrt_info(target)

                payload = {
                    "timestamp": int(time.time()),
                    "kpi": summary,
                    "network_kpi": network_kpi,
                    "errors": errors,
                    "openwrt": openwrt,
                }
                if openwrt_error:
                    payload["openwrt_error"] = openwrt_error

                if args.json:
                    try:
                        print(json.dumps(payload, indent=2, sort_keys=True))
                    except (TypeError, ValueError) as e:
                        logger.error(f"Failed to serialize JSON: {e}")
                        print(f"Error: JSON serialization failed: {e}", file=sys.stderr)
                else:
                    print_human(endpoints, summary, errors, openwrt, openwrt_error, network_kpi)

                if args.watch <= 0:
                    break
                
                # Check for shutdown signal before sleeping
                if shutdown_event:
                    logger.info("Shutdown requested, exiting...")
                    break
                
                print("\n" + "-" * 60 + "\n")
                time.sleep(args.watch)

            except Exception as e:
                logger.error(f"Collection iteration {iteration} failed: {e}", exc_info=True)
                if args.watch <= 0:
                    return 1
                # In watch mode, log error but continue
                print(f"Error during collection: {e}", file=sys.stderr)
                time.sleep(args.watch)

            # Check shutdown signal after each iteration
            if shutdown_event:
                logger.info("Shutdown requested, exiting...")
                break

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Unhandled exception: {e}", exc_info=True)
        print(f"Error: Unhandled exception: {e}", file=sys.stderr)
        return 1

    logger.info("Collection complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
