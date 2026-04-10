"""Configuration parsing helpers for CLI and server setup."""

from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple


def _parse_endpoint_token(ep_str: str) -> Tuple[str, int]:
    """Parse one endpoint token into (host, port) with IPv6-aware rules."""
    token = ep_str.strip()
    if not token:
        raise ValueError("empty endpoint token")

    default_port = 9090

    if token.startswith("["):
        close_idx = token.find("]")
        if close_idx <= 1:
            raise ValueError("invalid bracketed IPv6 host")
        host = token[1:close_idx].strip()
        suffix = token[close_idx + 1 :].strip()
        if not suffix:
            return host, default_port
        if not suffix.startswith(":"):
            raise ValueError("invalid suffix after bracketed host")
        port = int(suffix[1:])
        if port <= 0 or port > 65535:
            raise ValueError("port out of range")
        return host, port

    if token.count(":") > 1:
        # Treat bare IPv6 literal without brackets as host-only.
        return token, default_port

    if ":" in token:
        host, port_str = token.rsplit(":", 1)
        host = host.strip()
        if not host:
            raise ValueError("missing host")
        port = int(port_str)
        if port <= 0 or port > 65535:
            raise ValueError("port out of range")
        return host, port

    return token, default_port


def parse_manual_endpoints_with_errors(
    endpoints_str: Optional[str],
    *,
    endpoint_cls: Any,
    log_warning_fn: Callable[[str], None],
) -> Tuple[List[Any], List[str]]:
    """Parse comma-separated host:port list and return (valid_endpoints, invalid_tokens)."""
    endpoints: List[Any] = []
    invalid: List[str] = []
    if not endpoints_str:
        return endpoints, invalid

    for ep_str in endpoints_str.split(","):
        ep_str = ep_str.strip()
        if not ep_str:
            continue

        try:
            host, port = _parse_endpoint_token(ep_str)
        except ValueError:
            log_warning_fn(f"Invalid endpoint format: {ep_str}")
            invalid.append(ep_str)
            continue

        nf_label = f"custom-{host}-{port}"
        endpoints.append(endpoint_cls(nf=nf_label, address=host, port=port))

    return endpoints, invalid


def parse_manual_endpoints(
    endpoints_str: Optional[str],
    *,
    endpoint_cls: Any,
    log_warning_fn: Callable[[str], None],
) -> List[Any]:
    """Parse comma-separated host:port list into endpoint objects."""
    endpoints, _ = parse_manual_endpoints_with_errors(
        endpoints_str,
        endpoint_cls=endpoint_cls,
        log_warning_fn=log_warning_fn,
    )
    return endpoints


def finalize_parsed_args(
    args: Any,
    *,
    parser_error_fn: Callable[[str], None],
    env_get_fn: Callable[[str], Optional[str]],
    set_log_level_fn: Callable[[Any], None],
    debug_level: Any,
    log_warning_fn: Callable[[str], None],
    app_file_path: str,
) -> Any:
    """Apply post-parse validation and runtime defaults for CLI args."""
    if args.server and args.watch:
        parser_error_fn("--server and --watch cannot be used together")

    if args.debug:
        set_log_level_fn(debug_level)

    env_password = env_get_fn("OPENWRT_PASSWORD")
    if not args.openwrt_password:
        args.openwrt_password = env_password or ""
    elif env_password:
        log_warning_fn("OPENWRT_PASSWORD env var is set but --openwrt-password CLI arg takes precedence")

    if not getattr(args, "steer_script", None):
        default_script = Path(app_file_path).parent.parent / "scripts" / "toggle_route.sh"
        if default_script.exists():
            args.steer_script = str(default_script.resolve())
        else:
            args.steer_script = "/home/test-bed/test-bed/groupStudies/scripts/toggle_route.sh"

    return args
