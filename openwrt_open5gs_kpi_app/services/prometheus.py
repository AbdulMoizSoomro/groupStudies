"""Prometheus scraping and KPI aggregation helpers."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import time
from typing import Any, Callable, Dict, Iterable, Optional, Sequence

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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


def build_retrying_session(
    *,
    total_retries: int = 2,
    backoff_factor: float = 0.2,
    status_forcelist: Sequence[int] = (429, 500, 502, 503, 504),
    pool_maxsize: int = 8,
    session_factory: Callable[[], Any] = requests.Session,
    http_adapter_cls: Any = HTTPAdapter,
    retry_cls: Any = Retry,
) -> Any:
    """Build a requests Session with retry/backoff and pooled connections."""
    session = session_factory()
    retry_cfg = retry_cls(
        total=total_retries,
        connect=total_retries,
        read=total_retries,
        status=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=tuple(status_forcelist),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = http_adapter_cls(
        max_retries=retry_cfg,
        pool_connections=pool_maxsize,
        pool_maxsize=pool_maxsize,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def parse_prometheus_text(
    body: str,
    *,
    prom_line_re: Any = PROM_LINE,
    kpi_metric_names: Optional[set] = None,
    log_debug_fn: Optional[Callable[[str], None]] = None,
) -> Dict[str, float]:
    """Parse Prometheus text-format body to metric/value mapping."""
    if log_debug_fn is None:
        log_debug_fn = lambda _msg: None
    if kpi_metric_names is None:
        kpi_metric_names = set(KPI_KEYS.values())

    metrics: Dict[str, float] = {}
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = prom_line_re.match(line)
        if not match:
            continue
        name = match.group("name")
        try:
            value = float(match.group("value"))
            metrics[name] = metrics.get(name, 0.0) + value
            if name in kpi_metric_names:
                log_debug_fn(f"KPI Metric Found: {name}={value}")
        except (ValueError, AttributeError) as exc:
            log_debug_fn(f"Failed to parse metric value: {exc}")
    return metrics


def fetch_endpoint_metrics(
    endpoint: Any,
    timeout: float,
    *,
    requests_get_fn: Callable[..., Any],
    parse_prometheus_text_fn: Callable[[str], Dict[str, float]],
    requests_timeout_exc: Any,
    requests_connection_exc: Any,
    requests_request_exc: Any,
    log_debug_fn: Callable[[str], None],
    log_warning_fn: Callable[[str], None],
    request_attempts: int = 3,
    backoff_base_s: float = 0.1,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Dict[str, float]:
    """Fetch and parse Prometheus metrics from an endpoint."""
    log_debug_fn(f"Fetching metrics from {endpoint.nf} at {endpoint.url}")
    attempts = max(1, request_attempts)
    for attempt in range(1, attempts + 1):
        try:
            response = requests_get_fn(endpoint.url, timeout=timeout, verify=True)
            response.raise_for_status()
            return parse_prometheus_text_fn(response.text)
        except requests_timeout_exc:
            log_warning_fn(
                f"Timeout fetching metrics from {endpoint.nf} (attempt {attempt}/{attempts})"
            )
            if attempt >= attempts:
                raise
        except requests_connection_exc as exc:
            log_warning_fn(
                f"Connection failed for {endpoint.nf} (attempt {attempt}/{attempts}): {exc}"
            )
            if attempt >= attempts:
                raise
        except requests_request_exc as exc:
            log_warning_fn(
                f"HTTP error fetching {endpoint.nf} (attempt {attempt}/{attempts}): {exc}"
            )
            if attempt >= attempts:
                raise

        if backoff_base_s > 0:
            sleep_fn(backoff_base_s * (2 ** (attempt - 1)))

    raise RuntimeError(f"Exhausted retries for {endpoint.nf}")


def collect_all(
    endpoints: Iterable[Any],
    timeout: float,
    *,
    fetch_endpoint_metrics_fn: Callable[[Any, float], Dict[str, float]],
    log_info_fn: Callable[[str], None],
    log_warning_fn: Callable[[str], None],
    max_workers: Optional[int] = None,
) -> tuple[Dict[str, Dict[str, float]], Dict[str, str]]:
    """Scrape metrics from all endpoints and return (per_nf_metrics, errors)."""
    endpoint_list = list(endpoints)
    per_nf: Dict[str, Dict[str, float]] = {}
    errors: Dict[str, str] = {}

    worker_count = max_workers if isinstance(max_workers, int) and max_workers > 1 else 1
    worker_count = min(worker_count, max(1, len(endpoint_list)))

    if worker_count == 1:
        for endpoint in endpoint_list:
            try:
                metrics = fetch_endpoint_metrics_fn(endpoint, timeout)
                per_nf[endpoint.nf] = metrics
                log_info_fn(f"Scraped {len(metrics)} metrics from {endpoint.nf}")
            except Exception as exc:
                err_msg = f"{type(exc).__name__}: {exc}"
                errors[endpoint.nf] = err_msg
                log_warning_fn(f"Failed to scrape {endpoint.nf}: {err_msg}")
        return per_nf, errors

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_endpoint = {
            executor.submit(fetch_endpoint_metrics_fn, endpoint, timeout): endpoint
            for endpoint in endpoint_list
        }
        for future in as_completed(future_to_endpoint):
            endpoint = future_to_endpoint[future]
            try:
                metrics = future.result()
                per_nf[endpoint.nf] = metrics
                log_info_fn(f"Scraped {len(metrics)} metrics from {endpoint.nf}")
            except Exception as exc:
                err_msg = f"{type(exc).__name__}: {exc}"
                errors[endpoint.nf] = err_msg
                log_warning_fn(f"Failed to scrape {endpoint.nf}: {err_msg}")

    return per_nf, errors


def summarize_kpis(per_nf: Dict[str, Dict[str, float]], *, kpi_keys: Dict[str, str] = KPI_KEYS) -> Dict[str, float]:
    """Extract and aggregate high-level KPIs from low-level metrics."""
    merged: Dict[str, float] = {}
    for nf_metrics in per_nf.values():
        for metric_name, value in nf_metrics.items():
            merged[metric_name] = merged.get(metric_name, 0.0) + value

    summary: Dict[str, float] = {}
    for alias, metric_name in kpi_keys.items():
        summary[alias] = merged.get(metric_name, 0.0)

    req = summary.get("amf_reg_init_req", 0.0)
    succ = summary.get("amf_reg_init_succ", 0.0)
    summary["amf_reg_success_rate_pct"] = (succ / req * 100.0) if req > 0 else 0.0
    return summary


def extract_raw_metrics(per_nf: Dict[str, Dict[str, float]], metric_names: Optional[str]) -> Dict[str, float]:
    """Extract arbitrary metrics from per-NF map and optionally filter by name list."""
    merged: Dict[str, float] = {}
    for nf_metrics in per_nf.values():
        for metric_name, value in nf_metrics.items():
            merged[metric_name] = merged.get(metric_name, 0.0) + value

    if not metric_names or not metric_names.strip():
        return merged

    requested = set(name.strip() for name in metric_names.split(",") if name.strip())
    return {k: v for k, v in merged.items() if k in requested}
