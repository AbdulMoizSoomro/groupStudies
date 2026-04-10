"""OpenWrt raw metric collection helpers."""

from typing import Any, Callable, Dict, List


def run_openwrt_cmd(
    container: str,
    cmd: List[str],
    *,
    timeout: float = 5.0,
    run_cmd_fn: Callable[..., Any],
    timeout_exc_cls: Any,
    log_warning_fn: Callable[[str], None],
) -> str:
    """Run a command in OpenWrt container and return combined output."""
    try:
        result = run_cmd_fn(
            ["docker", "exec", container, *cmd],
            capture_output=True,
            text=True,
            check=False,
            timeout=max(1.0, timeout),
        )
        return ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    except timeout_exc_cls:
        log_warning_fn(f"OpenWrt command timeout for container {container}: {' '.join(cmd)}")
        return ""
    except Exception as exc:
        log_warning_fn(f"OpenWrt command failed for container {container}: {exc}")
        return ""


def read_openwrt_proc_net_dev(
    container: str,
    *,
    run_openwrt_cmd_fn: Callable[..., str],
) -> Dict[str, Dict[str, int]]:
    """Read full per-interface counters from OpenWrt /proc/net/dev."""
    raw = run_openwrt_cmd_fn(container, ["cat", "/proc/net/dev"], timeout=5.0)
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


def read_openwrt_meminfo(
    container: str,
    *,
    run_openwrt_cmd_fn: Callable[..., str],
) -> Dict[str, int]:
    """Read all numeric meminfo fields from OpenWrt /proc/meminfo."""
    raw = run_openwrt_cmd_fn(container, ["cat", "/proc/meminfo"], timeout=5.0)
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


def read_openwrt_cpu_stat(
    container: str,
    *,
    run_openwrt_cmd_fn: Callable[..., str],
) -> Dict[str, Any]:
    """Read raw CPU stat fields from OpenWrt /proc/stat."""
    raw = run_openwrt_cmd_fn(container, ["cat", "/proc/stat"], timeout=5.0)
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


def read_openwrt_uptime(
    container: str,
    *,
    run_openwrt_cmd_fn: Callable[..., str],
) -> Dict[str, float]:
    """Read raw uptime values from OpenWrt /proc/uptime."""
    raw = run_openwrt_cmd_fn(container, ["cat", "/proc/uptime"], timeout=5.0)
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


def read_openwrt_loadavg(
    container: str,
    *,
    run_openwrt_cmd_fn: Callable[..., str],
) -> Dict[str, Any]:
    """Read raw loadavg values from OpenWrt /proc/loadavg."""
    raw = run_openwrt_cmd_fn(container, ["cat", "/proc/loadavg"], timeout=5.0)
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


def read_openwrt_conntrack(
    container: str,
    *,
    run_openwrt_cmd_fn: Callable[..., str],
) -> Dict[str, Any]:
    """Read conntrack counters from OpenWrt /proc/sys/net/netfilter."""
    out: Dict[str, Any] = {}
    count_raw = run_openwrt_cmd_fn(
        container,
        ["cat", "/proc/sys/net/netfilter/nf_conntrack_count"],
        timeout=5.0,
    )
    max_raw = run_openwrt_cmd_fn(
        container,
        ["cat", "/proc/sys/net/netfilter/nf_conntrack_max"],
        timeout=5.0,
    )
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


def collect_openwrt_raw_metrics(
    container: str,
    interfaces: List[str],
    *,
    read_openwrt_proc_net_dev_fn: Callable[[str], Dict[str, Dict[str, int]]],
    read_openwrt_cpu_stat_fn: Callable[[str], Dict[str, Any]],
    read_openwrt_meminfo_fn: Callable[[str], Dict[str, int]],
    read_openwrt_uptime_fn: Callable[[str], Dict[str, float]],
    read_openwrt_loadavg_fn: Callable[[str], Dict[str, Any]],
    read_openwrt_conntrack_fn: Callable[[str], Dict[str, Any]],
) -> Dict[str, Any]:
    """Collect raw OpenWrt metrics without local calculations or ping probes."""
    all_ifaces = read_openwrt_proc_net_dev_fn(container)
    if interfaces:
        iface_map = {iface: all_ifaces.get(iface, {}) for iface in interfaces if iface in all_ifaces}
    else:
        iface_map = all_ifaces

    return {
        "source": "openwrt_container",
        "container": container,
        "interfaces": iface_map,
        "system": {
            "cpu_stat": read_openwrt_cpu_stat_fn(container),
            "meminfo": read_openwrt_meminfo_fn(container),
            "uptime": read_openwrt_uptime_fn(container),
            "loadavg": read_openwrt_loadavg_fn(container),
        },
        "conntrack": read_openwrt_conntrack_fn(container),
    }


def collect_network_kpis(
    cfg: Any,
    *,
    collect_openwrt_raw_metrics_fn: Callable[[str, List[str]], Dict[str, Any]],
) -> Dict[str, Any]:
    """Collect network/system KPI sections from raw OpenWrt metrics."""
    raw = collect_openwrt_raw_metrics_fn(cfg.openwrt_container, cfg.interfaces)
    return {
        "network": {
            "source": raw.get("source", "openwrt_container"),
            "container": raw.get("container", cfg.openwrt_container),
            "interfaces": raw.get("interfaces", {}),
        },
        "system": raw.get("system", {}),
        "conntrack": raw.get("conntrack", {}),
    }
