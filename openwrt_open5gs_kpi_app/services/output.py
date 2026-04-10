"""Output formatting helpers for CLI rendering."""

import json
from typing import Any, Callable, Dict, List, Optional


def print_human(
    endpoints: List[Any],
    summary: Dict[str, float],
    errors: Dict[str, str],
    openwrt: Dict[str, Any],
    openwrt_error: Optional[str],
    network_kpi: Optional[Dict[str, Any]],
    raw_metrics: Optional[Dict[str, float]] = None,
    *,
    printer: Callable[[str], None] = print,
    json_dumps_fn: Callable[..., str] = json.dumps,
    log_error_fn: Optional[Callable[[str], None]] = None,
    log_warning_fn: Optional[Callable[[str], None]] = None,
) -> None:
    """Print human-readable KPI snapshot to stdout."""
    if log_error_fn is None:
        log_error_fn = lambda _msg: None
    if log_warning_fn is None:
        log_warning_fn = lambda _msg: None

    printer("Open5GS KPI Snapshot")
    printer("=" * 60)
    printer("Endpoints")
    for endpoint in endpoints:
        printer(f"- {endpoint.nf:>4}: {endpoint.url}")

    printer("\nKPIs")
    for key in sorted(summary.keys()):
        val = summary[key]
        if key.endswith("_pct"):
            printer(f"- {key:30s}: {val:8.2f}")
        elif abs(val - int(val)) < 1e-9:
            printer(f"- {key:30s}: {int(val)}")
        else:
            printer(f"- {key:30s}: {val:.4f}")

    if raw_metrics:
        printer("\nRaw Metrics")
        for key in sorted(raw_metrics.keys()):
            val = raw_metrics[key]
            if abs(val - int(val)) < 1e-9:
                printer(f"- {key:40s}: {int(val)}")
            else:
                printer(f"- {key:40s}: {val:.4f}")

    if openwrt:
        printer("\nOpenWrt")
        for key in sorted(openwrt.keys()):
            value = openwrt[key]
            if isinstance(value, (dict, list)):
                value = json_dumps_fn(value, sort_keys=True)
            printer(f"- {key:30s}: {value}")

    if network_kpi:
        printer("\nNetwork/System KPIs")
        printer(json_dumps_fn(network_kpi, indent=2, sort_keys=True))

    if errors:
        printer("\nErrors")
        for nf, err in errors.items():
            printer(f"- {nf}: {err}")
            log_error_fn(f"Collection error [{nf}]: {err}")

    if openwrt_error:
        printer("\nOpenWrt Error")
        printer(f"- {openwrt_error}")
        log_warning_fn(f"OpenWrt probe error: {openwrt_error}")
