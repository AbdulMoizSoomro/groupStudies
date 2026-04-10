"""Host system metric helpers for /proc and conntrack reads."""

import re
from typing import Any, Callable, Dict, Optional, Tuple


def read_text(
    path: str,
    *,
    open_fn: Callable[..., Any],
    log_error_fn: Callable[[str], None],
) -> str:
    """Read text file contents with /proc-specific error logging."""
    try:
        with open_fn(path, "r", encoding="utf-8") as stream:
            return stream.read()
    except FileNotFoundError:
        if "/proc" in path:
            log_error_fn(f"Linux /proc not available: {path} (container or non-Linux?)")
        raise


def read_proc_net_dev(
    *,
    read_text_fn: Callable[[str], str],
    log_error_fn: Callable[[str], None],
    log_warning_fn: Callable[[str], None],
    log_debug_fn: Callable[[str], None],
) -> Dict[str, Dict[str, int]]:
    """Parse /proc/net/dev to extract interface counters."""
    try:
        data = read_text_fn("/proc/net/dev")
    except FileNotFoundError:
        log_error_fn("/proc/net/dev not available")
        raise

    out: Dict[str, Dict[str, int]] = {}
    for line in data.splitlines()[2:]:
        if ":" not in line:
            continue
        iface, rest = line.split(":", 1)
        iface = iface.strip()
        cols = rest.split()
        if len(cols) < 16:
            log_debug_fn(f"Skipping malformed /proc/net/dev line for {iface}")
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
        except (ValueError, IndexError) as exc:
            log_warning_fn(f"Failed to parse /proc/net/dev for {iface}: {exc}")
            continue

    return out


def read_cpu_usage_pct(
    sample_window_s: float = 0.5,
    *,
    read_text_fn: Callable[[str], str],
    sleep_fn: Callable[[float], None],
    log_warning_fn: Callable[[str], None],
    log_debug_fn: Callable[[str], None],
) -> Optional[float]:
    """Measure CPU usage percentage over a sample window."""

    def _read_cpu() -> Tuple[int, int]:
        try:
            first = read_text_fn("/proc/stat").splitlines()[0]
            parts = first.split()
            values = []
            for value in parts[1:]:
                try:
                    values.append(int(value))
                except ValueError:
                    log_debug_fn(f"Skipping non-integer CPU stat: {value}")
            if len(values) < 4:
                raise ValueError(f"Expected at least 4 CPU values, got {len(values)}")
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            total = sum(values)
            return idle, total
        except Exception as exc:
            log_warning_fn(f"Failed to read /proc/stat: {exc}")
            raise

    try:
        idle1, total1 = _read_cpu()
        sleep_fn(max(0.1, sample_window_s))
        idle2, total2 = _read_cpu()
        idle_delta = idle2 - idle1
        total_delta = total2 - total1
        if total_delta <= 0:
            log_warning_fn(f"CPU stat delta invalid: total_delta={total_delta}")
            return None
        return (1.0 - (idle_delta / total_delta)) * 100.0
    except Exception as exc:
        log_debug_fn(f"CPU usage measurement failed: {exc}")
        return None


def read_memory_usage(
    *,
    read_text_fn: Callable[[str], str],
    log_warning_fn: Callable[[str], None],
    log_debug_fn: Callable[[str], None],
) -> Dict[str, Any]:
    """Extract memory statistics from /proc/meminfo."""
    info: Dict[str, int] = {}
    try:
        for line in read_text_fn("/proc/meminfo").splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            parts = value.strip().split()
            if not parts:
                continue
            try:
                info[key] = int(parts[0])
            except ValueError:
                log_debug_fn(f"Skipping non-integer meminfo value: {key}={value}")
                continue
    except FileNotFoundError:
        log_debug_fn("/proc/meminfo not available")
        return {}
    except Exception as exc:
        log_warning_fn(f"Failed to read /proc/meminfo: {exc}")
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


def read_conntrack_pressure(
    *,
    read_text_fn: Callable[[str], str],
    run_cmd_fn: Callable[..., Any],
    timeout_exc_cls: Any,
    file_not_found_exc_cls: Any,
    log_warning_fn: Callable[[str], None],
    log_debug_fn: Callable[[str], None],
) -> Dict[str, Any]:
    """Read conntrack and TCP established counters."""
    data: Dict[str, Any] = {}
    try:
        count = int(read_text_fn("/proc/sys/net/netfilter/nf_conntrack_count").strip())
        maxv = int(read_text_fn("/proc/sys/net/netfilter/nf_conntrack_max").strip())
        data["conntrack_count"] = count
        data["conntrack_max"] = maxv
        data["conntrack_usage_pct"] = (count / maxv * 100.0) if maxv > 0 else None
        log_debug_fn(f"Conntrack: {count}/{maxv}")
    except FileNotFoundError:
        log_debug_fn("Conntrack not available (may not be kernel module loaded)")
    except (ValueError, IOError) as exc:
        log_warning_fn(f"Failed to read conntrack: {exc}")

    try:
        result = run_cmd_fn(["ss", "-s"], capture_output=True, text=True, check=False, timeout=5)
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        match = re.search(r"estab\s+(\d+)", output, flags=re.IGNORECASE)
        if match:
            data["tcp_established"] = int(match.group(1))
            log_debug_fn(f"TCP established: {data['tcp_established']}")
    except timeout_exc_cls:
        log_warning_fn("ss command timeout")
    except file_not_found_exc_cls:
        log_debug_fn("ss command not available")
    except Exception as exc:
        log_warning_fn(f"Failed to read TCP state: {exc}")

    return data
