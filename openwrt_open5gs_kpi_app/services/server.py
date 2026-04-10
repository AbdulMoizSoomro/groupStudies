"""HTTP server app-factory helpers."""

from typing import Any, Callable, Dict, List, Optional


def create_http_server_app(
    args: Any,
    *,
    flask_cls: Any,
    jsonify_fn: Callable[[Any], Any],
    parse_manual_endpoints_with_errors_fn: Callable[[Optional[str]], Any],
    collect_snapshot_fn: Callable[[Any, List[Any]], Dict[str, Any]],
    build_config_error_payload_fn: Callable[[str, Optional[List[str]]], Dict[str, Any]],
    build_runtime_error_payload_fn: Callable[[str], Dict[str, Any]],
    log_error_fn: Callable[..., None],
) -> Any:
    """Create HTTP server app for exposing health and KPI endpoints."""
    app = flask_cls(__name__)
    endpoints, invalid_endpoints = parse_manual_endpoints_with_errors_fn(args.metrics_endpoints)

    config_error_message: Optional[str] = None
    if invalid_endpoints and not endpoints:
        config_error_message = f"Invalid metrics endpoint configuration: {', '.join(invalid_endpoints)}"
    elif not endpoints:
        config_error_message = "No metrics endpoints discovered"

    def collect_kpi_snapshot() -> Dict[str, Any]:
        if config_error_message:
            return build_config_error_payload_fn(config_error_message, invalid_endpoints or None)

        try:
            return collect_snapshot_fn(args, endpoints)
        except Exception as exc:
            log_error_fn(f"KPI collection failed: {exc}", exc_info=True)
            return build_runtime_error_payload_fn(str(exc))

    @app.route("/health")
    def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.route("/kpi")
    def kpi() -> Any:
        return jsonify_fn(collect_kpi_snapshot())

    return app


def run_http_server(
    args: Any,
    port: int,
    *,
    create_http_server_fn: Callable[[Any], Any],
    get_logger_fn: Callable[[str], Any],
    warning_level: Any,
    log_info_fn: Callable[[str], None],
    log_error_fn: Callable[..., None],
    print_error_fn: Callable[[str], None],
) -> int:
    """Run HTTP server indefinitely."""
    log_info_fn(f"Starting HTTP server on port {port}")
    log_info_fn("Endpoints:")
    log_info_fn("  /health  - Health check")
    log_info_fn("  /kpi     - KPI metrics (JSON)")

    try:
        app = create_http_server_fn(args)
        flask_logger = get_logger_fn("werkzeug")
        flask_logger.setLevel(warning_level)

        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
        return 0
    except KeyboardInterrupt:
        log_info_fn("HTTP server interrupted")
        return 0
    except Exception as exc:
        log_error_fn(f"HTTP server failed: {exc}", exc_info=True)
        print_error_fn(f"Error: {exc}")
        return 1
