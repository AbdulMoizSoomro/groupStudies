"""Runtime execution helpers for script and process operations."""

from typing import Any, Callable


def run_steering_script(
    script_path: str,
    *,
    path_exists_fn: Callable[[str], bool],
    run_cmd_fn: Callable[..., Any],
    printer: Callable[[str], None],
    log_info_fn: Callable[[str], None],
    log_error_fn: Callable[[str], None],
    timeout_exception_cls: Any,
    timeout_s: int = 15,
) -> None:
    """Execute the traffic steering script and emit logs/output."""
    log_info_fn(f"Triggering traffic steering: {script_path}")
    try:
        if not path_exists_fn(script_path):
            log_error_fn(f"Steering script not found: {script_path}")
            return

        result = run_cmd_fn(
            ["bash", script_path],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )

        output = (result.stdout or "").strip()
        error = (result.stderr or "").strip()

        if output:
            printer("\n" + "=" * 20 + " STEERING OUTPUT " + "=" * 20)
            printer(output)
            printer("=" * 57 + "\n")

        if result.returncode != 0:
            log_error_fn(f"Steering script failed (exit {result.returncode}): {error}")
        else:
            log_info_fn("Traffic steering switch completed successfully")

    except timeout_exception_cls:
        log_error_fn(f"Traffic steering script timed out after {timeout_s}s")
    except Exception as exc:
        log_error_fn(f"Failed to execute steering script: {exc}")
