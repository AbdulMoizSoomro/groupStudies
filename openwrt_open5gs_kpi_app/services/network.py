"""Network diagnostics helpers (ip/tc/ping command parsing)."""

import re
from typing import Any, Callable, Dict, List


def run_cmd(
    args: List[str],
    *,
    run_cmd_fn: Callable[..., Any],
    timeout_exc_cls: Any,
    file_not_found_exc_cls: Any,
    log_warning_fn: Callable[[str], None],
    log_debug_fn: Callable[[str], None],
    timeout_s: int = 10,
) -> str:
    """Run a shell command and return combined stdout+stderr."""
    try:
        result = run_cmd_fn(args, capture_output=True, text=True, check=False, timeout=timeout_s)
        return ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    except timeout_exc_cls:
        log_warning_fn(f"Timeout running command: {args[0]}")
        return ""
    except file_not_found_exc_cls:
        log_debug_fn(f"Command not found: {args[0]}")
        return ""
    except Exception as exc:
        log_warning_fn(f"Failed to run command {args[0]}: {exc}")
        return ""


def parse_ip_link_detailed(
    iface: str,
    *,
    run_cmd_fn: Callable[[List[str]], str],
    log_debug_fn: Callable[[str], None],
) -> Dict[str, Any]:
    """Parse detailed interface stats from ip link output."""
    raw = run_cmd_fn(["ip", "-s", "-s", "link", "show", "dev", iface])
    out: Dict[str, Any] = {}

    qlen_match = re.search(r"\bqlen\s+(\d+)", raw)
    if qlen_match:
        try:
            out["tx_queue_len"] = int(qlen_match.group(1))
        except (ValueError, IndexError):
            log_debug_fn(f"Failed to parse queue length for {iface}")

    crc_line = re.search(
        r"RX errors:\s+length\s+crc\s+frame\s+fifo\s+missed\s*\n\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)",
        raw,
    )
    if crc_line:
        try:
            out["rx_err_length"] = int(crc_line.group(1))
            out["rx_err_crc"] = int(crc_line.group(2))
            out["rx_err_frame"] = int(crc_line.group(3))
            out["rx_err_fifo"] = int(crc_line.group(4))
            out["rx_err_missed"] = int(crc_line.group(5))
        except (ValueError, IndexError) as exc:
            log_debug_fn(f"Failed to parse RX errors for {iface}: {exc}")

    tx_err_line = re.search(
        r"TX errors:\s+aborted\s+fifo\s+window\s+heartbeat\s+transns\s*\n\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)",
        raw,
    )
    if tx_err_line:
        try:
            out["tx_err_aborted"] = int(tx_err_line.group(1))
            out["tx_err_fifo"] = int(tx_err_line.group(2))
            out["tx_err_window"] = int(tx_err_line.group(3))
            out["tx_err_heartbeat"] = int(tx_err_line.group(4))
            out["tx_err_transns"] = int(tx_err_line.group(5))
        except (ValueError, IndexError) as exc:
            log_debug_fn(f"Failed to parse TX errors for {iface}: {exc}")

    return out


def parse_tc_qdisc(
    iface: str,
    *,
    run_cmd_fn: Callable[[List[str]], str],
    log_debug_fn: Callable[[str], None],
) -> Dict[str, Any]:
    """Parse qdisc stats from tc output."""
    raw = run_cmd_fn(["tc", "-s", "qdisc", "show", "dev", iface])
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
        except (ValueError, IndexError) as exc:
            log_debug_fn(f"Failed to parse qdisc sent stats for {iface}: {exc}")

    backlog_match = re.search(r"backlog\s+(\d+)b\s+(\d+)p", raw)
    if backlog_match:
        try:
            out["qdisc_backlog_bytes"] = int(backlog_match.group(1))
            out["qdisc_backlog_packets"] = int(backlog_match.group(2))
        except (ValueError, IndexError) as exc:
            log_debug_fn(f"Failed to parse qdisc backlog for {iface}: {exc}")

    return out


def ping_stats(
    host: str,
    count: int,
    timeout_s: float,
    *,
    run_cmd_fn: Callable[..., Any],
    timeout_exc_cls: Any,
    log_warning_fn: Callable[[str], None],
    log_debug_fn: Callable[[str], None],
) -> Dict[str, Any]:
    """Collect ping loss/RTT/jitter stats for a host."""
    count = max(1, count)
    timeout_s_int = max(1, int(timeout_s))
    try:
        result = run_cmd_fn(
            ["ping", "-c", str(count), "-W", str(timeout_s_int), host],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s_int + 10,
        )
        output = (result.stdout or "") + "\n" + (result.stderr or "")
    except timeout_exc_cls:
        log_warning_fn(f"Ping subprocess timeout for {host}")
        return {"target": host, "ping_success": False}
    except Exception as exc:
        log_warning_fn(f"Ping failed for {host}: {exc}")
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
    except (ValueError, IndexError) as exc:
        log_debug_fn(f"Failed to parse ping tx/rx for {host}: {exc}")

    try:
        rtt = re.search(r"rtt [^=]+= ([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+) ms", output)
        if rtt:
            data["ping_rtt_min_ms"] = float(rtt.group(1))
            data["ping_rtt_avg_ms"] = float(rtt.group(2))
            data["ping_rtt_max_ms"] = float(rtt.group(3))
            data["ping_jitter_ms"] = float(rtt.group(4))
    except (ValueError, IndexError) as exc:
        log_debug_fn(f"Failed to parse ping RTT for {host}: {exc}")

    return data
