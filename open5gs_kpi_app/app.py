#!/usr/bin/env python3
import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
import yaml

DEFAULT_CONFIG = "/home/abdul-moiz-soomro/prj/group_studies/open5gs/build/configs/sample.yaml"

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


@dataclass(frozen=True)
class Endpoint:
    nf: str
    address: str
    port: int

    @property
    def url(self) -> str:
        return f"http://{self.address}:{self.port}/metrics"


@dataclass(frozen=True)
class OpenWrtTarget:
    host: str
    timeout: float
    username: str = ""
    password: str = ""


@dataclass(frozen=True)
class NetworkKpiConfig:
    interfaces: List[str]
    throughput_window_s: float
    latency_target: str
    latency_count: int
    latency_timeout_s: float


def discover_metrics_endpoints(config_path: str) -> List[Endpoint]:
    with open(config_path, "r", encoding="utf-8") as stream:
        cfg = yaml.safe_load(stream) or {}

    endpoints: List[Endpoint] = []
    for nf_name, nf_cfg in cfg.items():
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
                endpoints.append(Endpoint(nf=nf_name, address=address, port=port))
    return endpoints


def parse_prometheus_text(body: str) -> Dict[str, float]:
    metrics: Dict[str, float] = {}
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = PROM_LINE.match(line)
        if not match:
            continue
        name = match.group("name")
        value = float(match.group("value"))
        metrics[name] = metrics.get(name, 0.0) + value
    return metrics


def fetch_endpoint_metrics(endpoint: Endpoint, timeout: float) -> Dict[str, float]:
    response = requests.get(endpoint.url, timeout=timeout)
    response.raise_for_status()
    return parse_prometheus_text(response.text)


def _ping_host(host: str, timeout: float) -> Tuple[bool, Optional[float], str]:
    wait_seconds = str(max(1, int(timeout)))
    result = subprocess.run(
        ["ping", "-c", "1", "-W", wait_seconds, host],
        capture_output=True,
        text=True,
        check=False,
    )
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    match = re.search(r"time[=<]([0-9]+(?:\.[0-9]+)?)\s*ms", output)
    rtt_ms = float(match.group(1)) if match else None
    return result.returncode == 0, rtt_ms, output.strip()


def _probe_http_openwrt(target: OpenWrtTarget) -> Dict[str, Any]:
    for url in (f"http://{target.host}/cgi-bin/luci/", f"http://{target.host}/"):
        try:
            resp = requests.get(url, timeout=target.timeout, allow_redirects=True)
            return {
                "reachable": True,
                "url": url,
                "http_status": resp.status_code,
                "server": resp.headers.get("Server", ""),
            }
        except requests.RequestException:
            continue
    return {"reachable": False}


def _fetch_luci_sysinfo(target: OpenWrtTarget) -> Dict[str, Any]:
    if not target.username or not target.password:
        return {}

    auth_url = f"http://{target.host}/cgi-bin/luci/rpc/auth"
    login_payload = {"id": 1, "method": "login", "params": [target.username, target.password]}
    auth_resp = requests.post(auth_url, json=login_payload, timeout=target.timeout)
    auth_resp.raise_for_status()
    token = (auth_resp.json() or {}).get("result")
    if not isinstance(token, str) or not token:
        return {}

    sys_url = f"http://{target.host}/cgi-bin/luci/rpc/sys?auth={token}"
    sys_payload = {"id": 1, "method": "info", "params": []}
    sys_resp = requests.post(sys_url, json=sys_payload, timeout=target.timeout)
    sys_resp.raise_for_status()
    result = (sys_resp.json() or {}).get("result")
    return result if isinstance(result, dict) else {}


def fetch_openwrt_info(target: OpenWrtTarget) -> Tuple[Dict[str, Any], Optional[str]]:
    info: Dict[str, Any] = {
        "host": target.host,
    }

    try:
        reachable, rtt_ms, ping_output = _ping_host(target.host, target.timeout)
        info["icmp_reachable"] = reachable
        if rtt_ms is not None:
            info["icmp_rtt_ms"] = rtt_ms
        if not reachable:
            info["icmp_output"] = ping_output
    except Exception as exc:
        return info, f"Ping failed: {type(exc).__name__}: {exc}"

    try:
        http_info = _probe_http_openwrt(target)
        info.update({f"http_{key}": value for key, value in http_info.items()})
    except Exception as exc:
        return info, f"HTTP probe failed: {type(exc).__name__}: {exc}"

    if target.username and target.password:
        try:
            sys_info = _fetch_luci_sysinfo(target)
            if sys_info:
                info["luci_sysinfo"] = sys_info
        except Exception as exc:
            return info, f"LuCI RPC failed: {type(exc).__name__}: {exc}"

    return info, None


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as stream:
        return stream.read()


def _read_proc_net_dev() -> Dict[str, Dict[str, int]]:
    data = _read_text("/proc/net/dev")
    out: Dict[str, Dict[str, int]] = {}
    for line in data.splitlines()[2:]:
        if ":" not in line:
            continue
        iface, rest = line.split(":", 1)
        iface = iface.strip()
        cols = rest.split()
        if len(cols) < 16:
            continue
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
    return out


def _read_cpu_usage_pct(sample_window_s: float = 0.5) -> Optional[float]:
    def _read_cpu() -> Tuple[int, int]:
        first = _read_text("/proc/stat").splitlines()[0]
        parts = first.split()
        values = [int(v) for v in parts[1:]]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        total = sum(values)
        return idle, total

    try:
        idle1, total1 = _read_cpu()
        time.sleep(max(0.1, sample_window_s))
        idle2, total2 = _read_cpu()
        idle_delta = idle2 - idle1
        total_delta = total2 - total1
        if total_delta <= 0:
            return None
        return (1.0 - (idle_delta / total_delta)) * 100.0
    except Exception:
        return None


def _read_memory_usage() -> Dict[str, Any]:
    info: Dict[str, int] = {}
    try:
        for line in _read_text("/proc/meminfo").splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            parts = value.strip().split()
            if not parts:
                continue
            info[key] = int(parts[0])
    except Exception:
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
    data: Dict[str, Any] = {}
    try:
        count = int(_read_text("/proc/sys/net/netfilter/nf_conntrack_count").strip())
        maxv = int(_read_text("/proc/sys/net/netfilter/nf_conntrack_max").strip())
        data["conntrack_count"] = count
        data["conntrack_max"] = maxv
        data["conntrack_usage_pct"] = (count / maxv * 100.0) if maxv > 0 else None
    except Exception:
        pass

    try:
        result = subprocess.run(["ss", "-s"], capture_output=True, text=True, check=False)
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        match = re.search(r"estab\s+(\d+)", output, flags=re.IGNORECASE)
        if match:
            data["tcp_established"] = int(match.group(1))
    except Exception:
        pass
    return data


def _run_cmd(args: List[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    return ((result.stdout or "") + "\n" + (result.stderr or "")).strip()


def _parse_ip_link_detailed(iface: str) -> Dict[str, Any]:
    raw = _run_cmd(["ip", "-s", "-s", "link", "show", "dev", iface])
    out: Dict[str, Any] = {}

    qlen_match = re.search(r"\bqlen\s+(\d+)", raw)
    if qlen_match:
        out["tx_queue_len"] = int(qlen_match.group(1))

    crc_line = re.search(r"RX errors:\s+length\s+crc\s+frame\s+fifo\s+missed\s*\n\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", raw)
    if crc_line:
        out["rx_err_length"] = int(crc_line.group(1))
        out["rx_err_crc"] = int(crc_line.group(2))
        out["rx_err_frame"] = int(crc_line.group(3))
        out["rx_err_fifo"] = int(crc_line.group(4))
        out["rx_err_missed"] = int(crc_line.group(5))

    tx_err_line = re.search(r"TX errors:\s+aborted\s+fifo\s+window\s+heartbeat\s+transns\s*\n\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", raw)
    if tx_err_line:
        out["tx_err_aborted"] = int(tx_err_line.group(1))
        out["tx_err_fifo"] = int(tx_err_line.group(2))
        out["tx_err_window"] = int(tx_err_line.group(3))
        out["tx_err_heartbeat"] = int(tx_err_line.group(4))
        out["tx_err_transns"] = int(tx_err_line.group(5))

    return out


def _parse_tc_qdisc(iface: str) -> Dict[str, Any]:
    raw = _run_cmd(["tc", "-s", "qdisc", "show", "dev", iface])
    out: Dict[str, Any] = {}

    sent_match = re.search(
        r"Sent\s+(\d+)\s+bytes\s+(\d+)\s+pkt\s+\(dropped\s+(\d+),\s+overlimits\s+(\d+)\s+requeues\s+(\d+)\)",
        raw,
    )
    if sent_match:
        out["qdisc_sent_bytes"] = int(sent_match.group(1))
        out["qdisc_sent_packets"] = int(sent_match.group(2))
        out["qdisc_dropped"] = int(sent_match.group(3))
        out["qdisc_overlimits"] = int(sent_match.group(4))
        out["qdisc_requeues"] = int(sent_match.group(5))

    backlog_match = re.search(r"backlog\s+(\d+)b\s+(\d+)p", raw)
    if backlog_match:
        out["qdisc_backlog_bytes"] = int(backlog_match.group(1))
        out["qdisc_backlog_packets"] = int(backlog_match.group(2))

    return out


def _ping_stats(host: str, count: int, timeout_s: float) -> Dict[str, Any]:
    count = max(1, count)
    timeout_s = max(1, int(timeout_s))
    result = subprocess.run(
        ["ping", "-c", str(count), "-W", str(timeout_s), host],
        capture_output=True,
        text=True,
        check=False,
    )
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    data: Dict[str, Any] = {
        "target": host,
        "ping_success": result.returncode == 0,
    }

    txrx = re.search(r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets )?received", output)
    if txrx:
        tx = int(txrx.group(1))
        rx = int(txrx.group(2))
        data["ping_tx_packets"] = tx
        data["ping_rx_packets"] = rx
        data["ping_loss_pct"] = ((tx - rx) / tx * 100.0) if tx > 0 else None

    rtt = re.search(r"rtt [^=]+= ([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+) ms", output)
    if rtt:
        data["ping_rtt_min_ms"] = float(rtt.group(1))
        data["ping_rtt_avg_ms"] = float(rtt.group(2))
        data["ping_rtt_max_ms"] = float(rtt.group(3))
        data["ping_jitter_ms"] = float(rtt.group(4))

    return data


def collect_network_kpis(cfg: NetworkKpiConfig) -> Dict[str, Any]:
    start = _read_proc_net_dev()
    t1 = time.time()
    time.sleep(max(0.2, cfg.throughput_window_s))
    end = _read_proc_net_dev()
    t2 = time.time()
    delta_t = max(1e-6, t2 - t1)

    interfaces: Dict[str, Any] = {}
    total_rx_mbps = 0.0
    total_tx_mbps = 0.0

    for iface in cfg.interfaces:
        before = start.get(iface)
        after = end.get(iface)
        if not before or not after:
            continue

        rx_bytes_delta = max(0, after["rx_bytes"] - before["rx_bytes"])
        tx_bytes_delta = max(0, after["tx_bytes"] - before["tx_bytes"])
        rx_mbps = (rx_bytes_delta * 8.0) / (delta_t * 1_000_000.0)
        tx_mbps = (tx_bytes_delta * 8.0) / (delta_t * 1_000_000.0)
        total_rx_mbps += rx_mbps
        total_tx_mbps += tx_mbps

        iface_data: Dict[str, Any] = {
            "rx_mbps": rx_mbps,
            "tx_mbps": tx_mbps,
            "rx_packets": after["rx_packets"],
            "tx_packets": after["tx_packets"],
            "rx_errs": after["rx_errs"],
            "tx_errs": after["tx_errs"],
            "rx_drop": after["rx_drop"],
            "tx_drop": after["tx_drop"],
        }
        iface_data.update(_parse_ip_link_detailed(iface))
        iface_data.update(_parse_tc_qdisc(iface))
        interfaces[iface] = iface_data

    network = {
        "throughput_window_s": cfg.throughput_window_s,
        "wan_lan_total_rx_mbps": total_rx_mbps,
        "wan_lan_total_tx_mbps": total_tx_mbps,
        "interfaces": interfaces,
    }
    network.update(_ping_stats(cfg.latency_target, cfg.latency_count, cfg.latency_timeout_s))

    system = {
        "cpu_usage_pct": _read_cpu_usage_pct(),
    }
    system.update(_read_memory_usage())

    out: Dict[str, Any] = {
        "network": network,
        "system": system,
        "conntrack": _read_conntrack_pressure(),
    }
    return out


def collect_all(endpoints: Iterable[Endpoint], timeout: float) -> Tuple[Dict[str, Dict[str, float]], Dict[str, str]]:
    per_nf: Dict[str, Dict[str, float]] = {}
    errors: Dict[str, str] = {}
    for endpoint in endpoints:
        try:
            per_nf[endpoint.nf] = fetch_endpoint_metrics(endpoint, timeout)
        except Exception as exc:
            errors[endpoint.nf] = f"{type(exc).__name__}: {exc}"
    return per_nf, errors


def summarize_kpis(per_nf: Dict[str, Dict[str, float]]) -> Dict[str, float]:
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
    if openwrt_error:
        print("\nOpenWrt Error")
        print(f"- {openwrt_error}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch KPI snapshot from Open5GS metrics endpoints")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Path to Open5GS YAML config")
    parser.add_argument("--timeout", type=float, default=2.5, help="HTTP timeout seconds")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--watch", type=int, default=0, help="Poll interval seconds (0 = once)")
    parser.add_argument("--openwrt-host", default="192.168.142.200", help="OpenWrt host/IP")
    parser.add_argument("--openwrt-timeout", type=float, default=2.0, help="OpenWrt probe timeout seconds")
    parser.add_argument("--openwrt-user", default="", help="OpenWrt LuCI RPC username (optional)")
    parser.add_argument("--openwrt-password", default="", help="OpenWrt LuCI RPC password (optional)")
    parser.add_argument("--no-openwrt", action="store_true", help="Disable OpenWrt probing")
    parser.add_argument(
        "--ifaces",
        default="ens33,ens37",
        help="Comma-separated interfaces for throughput/error/queue KPIs",
    )
    parser.add_argument("--throughput-window", type=float, default=1.0, help="Seconds between byte samples")
    parser.add_argument("--latency-target", default="8.8.8.8", help="Ping target for latency/jitter/loss")
    parser.add_argument("--latency-count", type=int, default=5, help="Ping count for latency sample")
    parser.add_argument("--latency-timeout", type=float, default=2.0, help="Ping per-packet timeout seconds")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        endpoints = discover_metrics_endpoints(args.config)
    except FileNotFoundError:
        print(f"Config not found: {args.config}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Failed to parse config: {exc}", file=sys.stderr)
        return 2

    if not endpoints:
        print("No metrics endpoints found in config", file=sys.stderr)
        return 3

    while True:
        per_nf, errors = collect_all(endpoints, timeout=args.timeout)
        summary = summarize_kpis(per_nf)
        interfaces = [item.strip() for item in args.ifaces.split(",") if item.strip()]
        network_kpi = collect_network_kpis(
            NetworkKpiConfig(
                interfaces=interfaces,
                throughput_window_s=args.throughput_window,
                latency_target=args.latency_target,
                latency_count=args.latency_count,
                latency_timeout_s=args.latency_timeout,
            )
        )
        openwrt: Dict[str, Any] = {}
        openwrt_error: Optional[str] = None
        if not args.no_openwrt and args.openwrt_host:
            target = OpenWrtTarget(
                host=args.openwrt_host,
                timeout=args.openwrt_timeout,
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
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print_human(endpoints, summary, errors, openwrt, openwrt_error, network_kpi)

        if args.watch <= 0:
            break
        print("\n" + "-" * 60 + "\n")
        time.sleep(args.watch)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
