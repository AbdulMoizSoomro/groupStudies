#!/usr/bin/env python3
"""
Open5GS KPI Collection Tool

Fetches KPI snapshots from Open5GS Prometheus metrics endpoints, including:
- 5GC function metrics (AMF registration, active UEs, PFCP sessions)
- Network/system statistics (throughput, latency, CPU, memory)
- OpenWrt raw container metrics (optional)

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
from services import config as config_service
from services import host as host_service
from services import network as network_service
from services import openwrt as openwrt_service
from services import output as output_service
from services import prometheus as prometheus_service
from services import runtime as runtime_service
from services import server as server_service
from services import snapshot as snapshot_service

# Setup logging
logger = logging.getLogger(__name__)
_log_handler = logging.StreamHandler(sys.stderr)
_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
if not logger.handlers:
    logger.addHandler(_log_handler)
logger.setLevel(logging.INFO)

try:
    from flask import Flask, jsonify
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

try:
    from dotenv import load_dotenv
    HAS_DOTENV = True
except ImportError:
    HAS_DOTENV = False


KPI_KEYS = prometheus_service.KPI_KEYS

ERROR_CATEGORY_CONFIG = snapshot_service.ERROR_CATEGORY_CONFIG
ERROR_CATEGORY_ENDPOINT_FETCH = snapshot_service.ERROR_CATEGORY_ENDPOINT_FETCH
ERROR_CATEGORY_OPENWRT_COLLECTION = snapshot_service.ERROR_CATEGORY_OPENWRT_COLLECTION
ERROR_CATEGORY_SERIALIZATION = snapshot_service.ERROR_CATEGORY_SERIALIZATION
ERROR_CATEGORY_RUNTIME = snapshot_service.ERROR_CATEGORY_RUNTIME

PROM_LINE = prometheus_service.PROM_LINE

_ENV_INITIALIZED = False


def initialize_environment() -> None:
    """Load environment variables exactly once."""
    global _ENV_INITIALIZED
    if _ENV_INITIALIZED or not HAS_DOTENV:
        return

    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)
    else:
        load_dotenv(override=True)
    _ENV_INITIALIZED = True


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


def _non_negative_int(value: str) -> int:
    """Validate that argument is a non-negative integer."""
    try:
        i = int(value)
        if i < 0:
            raise ValueError(f"Must be non-negative, got {i}")
        return i
    except (ValueError, TypeError) as e:
        raise argparse.ArgumentTypeError(f"Invalid non-negative integer: {e}")


def _env_non_negative_int(name: str, default: int = 0) -> int:
    """Parse non-negative integer from env with safe fallback."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning(f"Ignoring invalid {name}: expected integer, got {raw!r}")
        return default
    if value < 0:
        logger.warning(f"Ignoring invalid {name}: expected non-negative integer, got {value}")
        return default
    return value


def _env_optional_non_negative_int(name: str) -> Optional[int]:
    """Parse optional non-negative integer from env with safe fallback."""
    raw = os.environ.get(name)
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except ValueError:
        logger.warning(f"Ignoring invalid {name}: expected non-negative integer, got {raw!r}")
        return None
    if value < 0:
        logger.warning(f"Ignoring invalid {name}: expected non-negative integer, got {value}")
        return None
    return value


def _valid_hostname_or_ip(value: str) -> str:
    """Validate hostname or IP address for OpenWrt host target."""
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
    """OpenWrt host configuration for raw OpenWrt collection flow."""
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
    return openwrt_service.run_openwrt_cmd(
        container,
        cmd,
        timeout=timeout,
        run_cmd_fn=subprocess.run,
        timeout_exc_cls=subprocess.TimeoutExpired,
        log_warning_fn=logger.warning,
    )


def _read_openwrt_proc_net_dev(container: str) -> Dict[str, Dict[str, int]]:
    """Read full per-interface counters from OpenWrt /proc/net/dev."""
    return openwrt_service.read_openwrt_proc_net_dev(
        container,
        run_openwrt_cmd_fn=_run_openwrt_cmd,
    )


def _read_openwrt_meminfo(container: str) -> Dict[str, int]:
    """Read all numeric meminfo fields from OpenWrt /proc/meminfo."""
    return openwrt_service.read_openwrt_meminfo(
        container,
        run_openwrt_cmd_fn=_run_openwrt_cmd,
    )


def _read_openwrt_cpu_stat(container: str) -> Dict[str, Any]:
    """Read raw CPU stat fields from OpenWrt /proc/stat without calculations."""
    return openwrt_service.read_openwrt_cpu_stat(
        container,
        run_openwrt_cmd_fn=_run_openwrt_cmd,
    )


def _read_openwrt_uptime(container: str) -> Dict[str, float]:
    """Read raw uptime values from OpenWrt /proc/uptime."""
    return openwrt_service.read_openwrt_uptime(
        container,
        run_openwrt_cmd_fn=_run_openwrt_cmd,
    )


def _read_openwrt_loadavg(container: str) -> Dict[str, Any]:
    """Read raw loadavg values from OpenWrt /proc/loadavg."""
    return openwrt_service.read_openwrt_loadavg(
        container,
        run_openwrt_cmd_fn=_run_openwrt_cmd,
    )


def _read_openwrt_conntrack(container: str) -> Dict[str, Any]:
    """Read conntrack counters from OpenWrt /proc/sys/net/netfilter."""
    return openwrt_service.read_openwrt_conntrack(
        container,
        run_openwrt_cmd_fn=_run_openwrt_cmd,
    )


def collect_openwrt_raw_metrics(container: str, interfaces: List[str]) -> Dict[str, Any]:
    """Collect raw OpenWrt metrics without local calculations or ping probes."""
    return openwrt_service.collect_openwrt_raw_metrics(
        container,
        interfaces,
        read_openwrt_proc_net_dev_fn=_read_openwrt_proc_net_dev,
        read_openwrt_cpu_stat_fn=_read_openwrt_cpu_stat,
        read_openwrt_meminfo_fn=_read_openwrt_meminfo,
        read_openwrt_uptime_fn=_read_openwrt_uptime,
        read_openwrt_loadavg_fn=_read_openwrt_loadavg,
        read_openwrt_conntrack_fn=_read_openwrt_conntrack,
    )


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
    return prometheus_service.parse_prometheus_text(
        body,
        prom_line_re=PROM_LINE,
        kpi_metric_names=set(KPI_KEYS.values()),
        log_debug_fn=logger.debug,
    )


def fetch_endpoint_metrics(
    endpoint: Endpoint,
    timeout: float,
    *,
    requests_get_fn: Optional[Any] = None,
    request_attempts: int = 3,
    backoff_base_s: float = 0.1,
) -> Dict[str, float]:
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
    if requests_get_fn is None:
        requests_get_fn = requests.get

    return prometheus_service.fetch_endpoint_metrics(
        endpoint,
        timeout,
        requests_get_fn=requests_get_fn,
        parse_prometheus_text_fn=parse_prometheus_text,
        requests_timeout_exc=requests.Timeout,
        requests_connection_exc=requests.ConnectionError,
        requests_request_exc=requests.RequestException,
        log_debug_fn=logger.debug,
        log_warning_fn=logger.warning,
        request_attempts=request_attempts,
        backoff_base_s=backoff_base_s,
        sleep_fn=time.sleep,
    )


def fetch_openwrt_info(target: OpenWrtTarget) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    Fetch OpenWrt host metadata and available raw interfaces.
    
    Args:
        target: OpenWrtTarget configuration
        
    Returns:
        Tuple of (info_dict, error_string or None)
        Info dict always contains host/container and discovered interfaces (if available)
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
    return host_service.read_text(
        path,
        open_fn=open,
        log_error_fn=logger.error,
    )


def _read_proc_net_dev() -> Dict[str, Dict[str, int]]:
    """
    Parse /proc/net/dev to extract interface statistics.
    
    Returns:
        Dict mapping interface names to dicts of RX/TX byte/packet/error counters
        
    Raises:
        FileNotFoundError: If /proc/net/dev not available
    """
    return host_service.read_proc_net_dev(
        read_text_fn=_read_text,
        log_error_fn=logger.error,
        log_warning_fn=logger.warning,
        log_debug_fn=logger.debug,
    )


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
    return host_service.read_cpu_usage_pct(
        sample_window_s,
        read_text_fn=_read_text,
        sleep_fn=time.sleep,
        log_warning_fn=logger.warning,
        log_debug_fn=logger.debug,
    )


def _read_memory_usage() -> Dict[str, Any]:
    """
    Extract memory statistics from /proc/meminfo.
    
    Returns:
        Dict with 'mem_total_kb', 'mem_available_kb', 'mem_used_kb', 'mem_used_pct'
        Returns empty dict if /proc/meminfo unavailable or unparseable.
    """
    return host_service.read_memory_usage(
        read_text_fn=_read_text,
        log_warning_fn=logger.warning,
        log_debug_fn=logger.debug,
    )


def _read_conntrack_pressure() -> Dict[str, Any]:
    """
    Read connection tracking and TCP connection state.
    
    Returns:
        Dict with 'conntrack_count', 'conntrack_max', 'conntrack_usage_pct', 'tcp_established'
        Missing fields are omitted if data unavailable.
    """
    return host_service.read_conntrack_pressure(
        read_text_fn=_read_text,
        run_cmd_fn=subprocess.run,
        timeout_exc_cls=subprocess.TimeoutExpired,
        file_not_found_exc_cls=FileNotFoundError,
        log_warning_fn=logger.warning,
        log_debug_fn=logger.debug,
    )


def _run_cmd(args: List[str]) -> str:
    """
    Run a shell command and return combined stdout+stderr.
    
    Args:
        args: Command and arguments as list
        
    Returns:
        Combined stdout and stderr as string
    """
    return network_service.run_cmd(
        args,
        run_cmd_fn=subprocess.run,
        timeout_exc_cls=subprocess.TimeoutExpired,
        file_not_found_exc_cls=FileNotFoundError,
        log_warning_fn=logger.warning,
        log_debug_fn=logger.debug,
        timeout_s=10,
    )


def _parse_ip_link_detailed(iface: str) -> Dict[str, Any]:
    """
    Parse detailed interface statistics from 'ip link show' command.
    
    Extracts RX/TX error counts and queue length.
    
    Args:
        iface: Interface name
        
    Returns:
        Dict with 'tx_queue_len' and RX/TX error counters (if present in output)
    """
    return network_service.parse_ip_link_detailed(
        iface,
        run_cmd_fn=_run_cmd,
        log_debug_fn=logger.debug,
    )


def _parse_tc_qdisc(iface: str) -> Dict[str, Any]:
    """
    Parse traffic control qdisc statistics from 'tc qdisc show' command.
    
    Extracts sent packets/bytes, drops, overlimits, requeues, backlog.
    
    Args:
        iface: Interface name
        
    Returns:
        Dict with qdisc statistics (if present in output)
    """
    return network_service.parse_tc_qdisc(
        iface,
        run_cmd_fn=_run_cmd,
        log_debug_fn=logger.debug,
    )


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
    return network_service.ping_stats(
        host,
        count,
        timeout_s,
        run_cmd_fn=subprocess.run,
        timeout_exc_cls=subprocess.TimeoutExpired,
        log_warning_fn=logger.warning,
        log_debug_fn=logger.debug,
    )


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
    return openwrt_service.collect_network_kpis(
        cfg,
        collect_openwrt_raw_metrics_fn=collect_openwrt_raw_metrics,
    )


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
    endpoint_list = list(endpoints)
    if not endpoint_list:
        return {}, {}

    max_workers = min(8, max(1, len(endpoint_list)))
    session = prometheus_service.build_retrying_session(pool_maxsize=max_workers)

    try:
        def _fetch_with_shared_session(endpoint: Endpoint, req_timeout: float) -> Dict[str, float]:
            return fetch_endpoint_metrics(
                endpoint,
                req_timeout,
                requests_get_fn=session.get,
            )

        return prometheus_service.collect_all(
            endpoint_list,
            timeout,
            fetch_endpoint_metrics_fn=_fetch_with_shared_session,
            log_info_fn=logger.info,
            log_warning_fn=logger.warning,
            max_workers=max_workers,
        )
    finally:
        _close_session_if_possible(session)


def _close_session_if_possible(session: Any) -> None:
    """Best-effort close for request sessions."""
    close_fn = getattr(session, "close", None)
    if callable(close_fn):
        close_fn()


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
    return prometheus_service.summarize_kpis(per_nf, kpi_keys=KPI_KEYS)


def extract_raw_metrics(per_nf: Dict[str, Dict[str, float]], metric_names: Optional[str]) -> Dict[str, float]:
    """
    Extract arbitrary metrics from per-NF dict and merge them.
    
    If metric_names is empty or None, returns all metrics from all NFs.
    Otherwise, filters to only requested metric names.
    """
    return prometheus_service.extract_raw_metrics(per_nf, metric_names)


def print_human(
    endpoints: List[Endpoint],
    summary: Dict[str, float],
    errors: Dict[str, str],
    openwrt: Dict[str, Any],
    openwrt_error: Optional[str],
    network_kpi: Optional[Dict[str, Any]],
    raw_metrics: Optional[Dict[str, float]] = None,
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
    output_service.print_human(
        endpoints,
        summary,
        errors,
        openwrt,
        openwrt_error,
        network_kpi,
        raw_metrics,
        printer=print,
        json_dumps_fn=json.dumps,
        log_error_fn=logger.error,
        log_warning_fn=logger.warning,
    )


def parse_args() -> argparse.Namespace:
    """Parse and validate command-line arguments."""
    initialize_environment()

    parser = argparse.ArgumentParser(
        description="Fetch KPI snapshot from Open5GS metrics endpoints",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment Variables:
    OPENWRT_PASSWORD      Reserved compatibility option (currently unused in raw-only mode)
  METRICS_ENDPOINTS     Comma-separated list of metrics endpoints config

Examples:
  %(prog)s                           # One-time snapshot, human-readable
  %(prog)s --json                    # One-time snapshot, JSON output
  %(prog)s --watch 5                 # Poll metrics every 5 seconds
  %(prog)s --server 8080             # Start HTTP server on port 8080
  %(prog)s --debug --watch 2         # Poll with debug logging
        """,
    )
    parser.add_argument(
        "--metrics-endpoints",
        default=os.environ.get("METRICS_ENDPOINTS"),
        help="Comma-separated host:port list (e.g. 127.0.0.2:9090,127.0.0.4:9090)",
    )
    parser.add_argument(
        "--raw-metrics",
        default=os.environ.get("RAW_METRICS", ""),
        help="Comma-separated metric names to include in raw_metrics output (empty=all)",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=float(os.environ.get("TIMEOUT", 2.5)),
        help="HTTP timeout seconds (default: 2.5)",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON instead of human-readable")
    parser.add_argument(
        "--watch",
        type=_non_negative_int,
        default=_env_non_negative_int("WATCH_INTERVAL", default=0),
        help="Poll interval seconds (0 = once, default: 0)",
    )
    parser.add_argument(
        "--server",
        type=_non_negative_int,
        default=0,
        help="Start HTTP server on PORT (e.g., 8080). Incompatible with --watch",
    )
    parser.add_argument(
        "--openwrt-host",
        type=_valid_hostname_or_ip,
        default=os.environ.get("OPENWRT_HOST", "192.168.142.200"),
        help="OpenWrt host/IP (default: 192.168.142.200)",
    )
    parser.add_argument(
        "--openwrt-timeout",
        type=_positive_float,
        default=float(os.environ.get("OPENWRT_TIMEOUT", 2.0)),
        help="OpenWrt collection timeout seconds (default: 2.0)",
    )
    parser.add_argument(
        "--openwrt-container",
        default=os.environ.get("OPENWRT_CONTAINER", "openwrt_router"),
        help="OpenWrt Docker container name for raw metrics (default: openwrt_router)",
    )
    parser.add_argument(
        "--openwrt-user",
        default=os.environ.get("OPENWRT_USER", ""),
        help="Reserved for future OpenWrt auth integrations (currently unused)",
    )
    parser.add_argument(
        "--openwrt-password",
        default=os.environ.get("OPENWRT_PASSWORD", ""),
        help="Reserved for future OpenWrt auth integrations (currently unused)",
    )
    parser.add_argument(
        "--no-openwrt",
        action="store_true",
        help="Disable OpenWrt probing",
    )
    parser.add_argument(
        "--ifaces",
        default=os.environ.get("OPENWRT_IFACES", "eth0,eth1,br-lan,lo"),
        help="Comma-separated OpenWrt interfaces to include (default: eth0,eth1,br-lan,lo)",
    )
    parser.add_argument(
        "--steer-interval",
        type=_non_negative_int,
        default=_env_optional_non_negative_int("STEER_INTERVAL"),
        help="Trigger automated traffic steering every N seconds (0 disables, default: None)",
    )
    parser.add_argument(
        "--steer-script",
        default=os.environ.get("STEER_SCRIPT"),
        help="Path to traffic steering script (default: scripts/toggle_route.sh)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()
    return config_service.finalize_parsed_args(
        args,
        parser_error_fn=parser.error,
        env_get_fn=os.environ.get,
        set_log_level_fn=logger.setLevel,
        debug_level=logging.DEBUG,
        log_warning_fn=logger.warning,
        app_file_path=__file__,
    )


def run_steering_script(script_path: str) -> None:
    """
    Execute the traffic steering script and print its output.
    
    Args:
        script_path: Absolute path to toggle_route.sh
    """
    runtime_service.run_steering_script(
        script_path,
        path_exists_fn=os.path.exists,
        run_cmd_fn=subprocess.run,
        printer=print,
        log_info_fn=logger.info,
        log_error_fn=logger.error,
        timeout_exception_cls=subprocess.TimeoutExpired,
        timeout_s=15,
    )


def collect_snapshot(args: argparse.Namespace, endpoints: List[Endpoint]) -> Dict[str, Any]:
    """Collect one complete KPI snapshot for CLI and HTTP server paths."""
    return snapshot_service.collect_snapshot(
        args,
        endpoints,
        collect_all_fn=collect_all,
        summarize_kpis_fn=summarize_kpis,
        extract_raw_metrics_fn=extract_raw_metrics,
        collect_network_kpis_fn=collect_network_kpis,
        network_kpi_config_cls=NetworkKpiConfig,
        fetch_openwrt_info_fn=fetch_openwrt_info,
        openwrt_target_cls=OpenWrtTarget,
        log_warning_fn=logger.warning,
        now_fn=time.time,
    )


def _build_config_error_payload(message: str, invalid_endpoints: Optional[List[str]] = None) -> Dict[str, Any]:
    """Build a consistent payload for configuration-related errors."""
    return snapshot_service.build_config_error_payload(
        message,
        invalid_endpoints=invalid_endpoints,
        now_fn=time.time,
    )


def _build_runtime_error_payload(message: str) -> Dict[str, Any]:
    """Build a consistent payload for runtime collection failures."""
    return snapshot_service.build_runtime_error_payload(message, now_fn=time.time)


def create_http_server(args: argparse.Namespace) -> "Flask":
    """
    Create Flask HTTP server for exposing KPI endpoint.
    
    Args:
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

    return server_service.create_http_server_app(
        args,
        flask_cls=Flask,
        jsonify_fn=jsonify,
        parse_manual_endpoints_with_errors_fn=_parse_manual_endpoints_with_errors,
        collect_snapshot_fn=collect_snapshot,
        build_config_error_payload_fn=_build_config_error_payload,
        build_runtime_error_payload_fn=_build_runtime_error_payload,
        log_error_fn=logger.error,
    )


def run_http_server(args: argparse.Namespace, port: int) -> int:
    """
    Run HTTP server indefinitely.
    
    Args:
        args: Parsed arguments
        port: Port to listen on
        
    Returns:
        Exit code (0 = clean shutdown, 1 = error)
    """
    return server_service.run_http_server(
        args,
        port,
        create_http_server_fn=create_http_server,
        get_logger_fn=logging.getLogger,
        warning_level=logging.WARNING,
        log_info_fn=logger.info,
        log_error_fn=logger.error,
        print_error_fn=lambda message: print(message, file=sys.stderr),
    )


def _parse_manual_endpoints_with_errors(endpoints_str: Optional[str]) -> Tuple[List[Endpoint], List[str]]:
    """Parse comma-separated host:port list and return (valid_endpoints, invalid_tokens)."""
    endpoints, invalid = config_service.parse_manual_endpoints_with_errors(
        endpoints_str,
        endpoint_cls=Endpoint,
        log_warning_fn=logger.warning,
    )
    return endpoints, invalid


def _parse_manual_endpoints(endpoints_str: Optional[str]) -> List[Endpoint]:
    """Parse comma-separated host:port list into Endpoint objects."""
    endpoints = config_service.parse_manual_endpoints(
        endpoints_str,
        endpoint_cls=Endpoint,
        log_warning_fn=logger.warning,
    )
    return endpoints


def main() -> int:
    """
    Main entry point. Orchestrates endpoint collection, 
    metrics scraping, and output formatting.
    
    Handles graceful shutdown via SIGINT/SIGTERM in watch mode.
    
    Returns:
        Exit code (0: success, 3: no endpoints, 1: unhandled exception)
    """
    try:
        args = parse_args()
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else 1

    logger.info("=" * 60)
    logger.info("Open5GS KPI Collection Tool")
    logger.info("=" * 60)

    # 0. Discover OpenWrt Interfaces automatically if not provided
    if not args.no_openwrt and not os.environ.get("OPENWRT_IFACES") and args.ifaces == "eth0,eth1,br-lan,lo":
        try:
            raw_dev = _run_openwrt_cmd(args.openwrt_container, ["cat", "/proc/net/dev"])
            discovered_ifaces = []
            for line in raw_dev.splitlines():
                if ":" in line:
                    iface = line.split(":")[0].strip()
                    if iface:
                        discovered_ifaces.append(iface)
            if discovered_ifaces:
                args.ifaces = ",".join(discovered_ifaces)
                logger.info(f"Auto-discovered OpenWrt interfaces: {args.ifaces}")
        except Exception as e:
            logger.warning(f"Failed to auto-discover OpenWrt interfaces: {e}")

    # 1. Manual Endpoints (e.g. from .env or CLI)
    endpoints, invalid_endpoints = _parse_manual_endpoints_with_errors(args.metrics_endpoints)
    if invalid_endpoints and not endpoints:
        logger.error(
            f"Invalid metrics endpoint configuration: {', '.join(invalid_endpoints)}"
        )
        return 2
    if not endpoints:
        logger.error("No metrics endpoints discovered")
        return 3
    logger.info(f"Using {len(endpoints)} metrics endpoints")

    # Handle HTTP server mode
    if args.server:
        return run_http_server(args, args.server)

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
    start_time = time.time()
    last_steer_time = start_time
    
    try:
        while True:
            iteration += 1
            now = time.time()
            logger.debug(f"Starting collection iteration {iteration}")

            # Automated steering based on timer
            if args.steer_interval is not None and args.steer_interval > 0:
                elapsed = now - last_steer_time
                if elapsed >= args.steer_interval:
                    run_steering_script(args.steer_script)
                    last_steer_time = now

            try:
                payload = collect_snapshot(args, endpoints)

                if args.json:
                    try:
                        print(json.dumps(payload, indent=2, sort_keys=True))
                    except (TypeError, ValueError) as e:
                        logger.error(f"[{ERROR_CATEGORY_SERIALIZATION}] Failed to serialize JSON: {e}")
                        print(f"Error: JSON serialization failed: {e}", file=sys.stderr)
                        if args.watch <= 0:
                            return 1
                else:
                    print_human(
                        endpoints,
                        payload.get("kpi", {}),
                        payload.get("errors", {}),
                        payload.get("openwrt", {}),
                        payload.get("openwrt_error"),
                        payload.get("network_kpi", {}),
                        payload.get("raw_metrics", {}),
                    )

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
    initialize_environment()
    raise SystemExit(main())
