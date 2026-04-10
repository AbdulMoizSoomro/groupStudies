"""Snapshot orchestration and error payload helpers."""

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

ERROR_CATEGORY_CONFIG = "CONFIG_ERROR"
ERROR_CATEGORY_ENDPOINT_FETCH = "ENDPOINT_FETCH_ERROR"
ERROR_CATEGORY_OPENWRT_COLLECTION = "OPENWRT_COLLECTION_ERROR"
ERROR_CATEGORY_SERIALIZATION = "SERIALIZATION_ERROR"
ERROR_CATEGORY_RUNTIME = "RUNTIME_ERROR"


def collect_snapshot(
    args: Any,
    endpoints: List[Any],
    *,
    collect_all_fn: Callable[..., Tuple[Dict[str, Dict[str, float]], Dict[str, str]]],
    summarize_kpis_fn: Callable[[Dict[str, Dict[str, float]]], Dict[str, float]],
    extract_raw_metrics_fn: Callable[[Dict[str, Dict[str, float]], Optional[str]], Dict[str, float]],
    collect_network_kpis_fn: Callable[[Any], Dict[str, Any]],
    network_kpi_config_cls: Any,
    fetch_openwrt_info_fn: Callable[[Any], Tuple[Dict[str, Any], Optional[str]]],
    openwrt_target_cls: Any,
    log_warning_fn: Callable[[str], None],
    now_fn: Callable[[], float] = time.time,
) -> Dict[str, Any]:
    """Collect one complete KPI snapshot for CLI and HTTP server paths."""
    per_nf, errors = collect_all_fn(endpoints, timeout=args.timeout)
    error_categories: Dict[str, str] = {
        f"endpoint:{nf}": ERROR_CATEGORY_ENDPOINT_FETCH for nf in errors
    }
    summary = summarize_kpis_fn(per_nf)
    raw_metrics = extract_raw_metrics_fn(per_nf, getattr(args, "raw_metrics", ""))

    interfaces = [item.strip() for item in args.ifaces.split(",") if item.strip()]
    try:
        network_kpi = collect_network_kpis_fn(
            network_kpi_config_cls(
                interfaces=interfaces,
                openwrt_container=args.openwrt_container,
            )
        )
    except Exception as exc:
        log_warning_fn(f"Network KPI collection failed: {exc}")
        network_kpi = {}
        errors["network_kpi"] = str(exc)
        error_categories["network_kpi"] = ERROR_CATEGORY_OPENWRT_COLLECTION

    openwrt: Dict[str, Any] = {}
    openwrt_error: Optional[str] = None
    if not args.no_openwrt and args.openwrt_host:
        target = openwrt_target_cls(
            host=args.openwrt_host,
            timeout=args.openwrt_timeout,
            container=args.openwrt_container,
            username=args.openwrt_user,
            password=args.openwrt_password,
        )
        openwrt, openwrt_error = fetch_openwrt_info_fn(target)
        if openwrt_error:
            error_categories["openwrt"] = ERROR_CATEGORY_OPENWRT_COLLECTION

    payload: Dict[str, Any] = {
        "timestamp": int(now_fn()),
        "kpi": summary,
        "raw_metrics": raw_metrics,
        "network_kpi": network_kpi,
        "errors": errors,
        "error_categories": error_categories,
        "openwrt": openwrt,
    }
    if openwrt_error:
        payload["openwrt_error"] = openwrt_error

    return payload


def build_config_error_payload(
    message: str,
    invalid_endpoints: Optional[List[str]] = None,
    *,
    now_fn: Callable[[], float] = time.time,
) -> Dict[str, Any]:
    """Build a consistent payload for configuration-related errors."""
    payload: Dict[str, Any] = {
        "timestamp": int(now_fn()),
        "error": message,
        "kpi": {},
        "raw_metrics": {},
        "network_kpi": {},
        "errors": {"config": message},
        "error_categories": {"config": ERROR_CATEGORY_CONFIG},
        "openwrt": {},
    }
    if invalid_endpoints:
        payload["invalid_endpoints"] = invalid_endpoints
    return payload


def build_runtime_error_payload(
    message: str,
    *,
    now_fn: Callable[[], float] = time.time,
) -> Dict[str, Any]:
    """Build a consistent payload for runtime collection failures."""
    return {
        "timestamp": int(now_fn()),
        "error": message,
        "kpi": {},
        "raw_metrics": {},
        "network_kpi": {},
        "errors": {"runtime": message},
        "error_categories": {"runtime": ERROR_CATEGORY_RUNTIME},
        "openwrt": {},
    }
