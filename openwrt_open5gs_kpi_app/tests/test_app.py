"""
Unit tests for Open5GS KPI application.

Tests parsing logic, validation, and error handling without requiring
running services or actual network access.
"""

import pytest
import sys
import argparse
import json
from pathlib import Path
from unittest import mock

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import app


class TestParsePrometheusText:
    """Tests for Prometheus text format parsing."""

    def test_parse_empty_body(self):
        """Empty input should return empty dict."""
        result = app.parse_prometheus_text("")
        assert result == {}

    def test_parse_comments_only(self):
        """Comments-only input should yield no metrics."""
        body = "# HELP metric_name\n# TYPE metric_name gauge\n"
        result = app.parse_prometheus_text(body)
        assert result == {}

    def test_parse_single_metric(self):
        """Parse single metric line."""
        body = "fivegs_amffunction_rm_reginitreq 42"
        result = app.parse_prometheus_text(body)
        assert result == {"fivegs_amffunction_rm_reginitreq": 42.0}

    def test_parse_metric_with_labels(self):
        """Parse metric with Prometheus labels."""
        body = 'fivegs_amffunction_rm_reginitreq{instance="localhost"} 42'
        result = app.parse_prometheus_text(body)
        assert result == {"fivegs_amffunction_rm_reginitreq": 42.0}

    def test_parse_float_metric(self):
        """Parse floating point metric value."""
        body = "cpu_usage_pct 42.5"
        result = app.parse_prometheus_text(body)
        assert result == {"cpu_usage_pct": 42.5}

    def test_parse_scientific_notation(self):
        """Parse scientific notation metric values."""
        body = "metric_value 1.23e-4"
        result = app.parse_prometheus_text(body)
        assert abs(result["metric_value"] - 1.23e-4) < 1e-9

    def test_parse_negative_value(self):
        """Parse negative metric value."""
        body = "memory_delta -512"
        result = app.parse_prometheus_text(body)
        assert result == {"memory_delta": -512.0}

    def test_parse_aggregates_duplicate_metrics(self):
        """Duplicate metric names should be summed."""
        body = "packet_count 10\npacket_count 5"
        result = app.parse_prometheus_text(body)
        assert result == {"packet_count": 15.0}

    def test_parse_skips_invalid_lines(self):
        """Invalid lines are skipped without crashing."""
        body = "metric1 10\ninvalid line that has no value\nmetric2 20"
        result = app.parse_prometheus_text(body)
        assert result == {"metric1": 10.0, "metric2": 20.0}

    def test_parse_whitespace_handling(self):
        """Test that extra whitespace is handled."""
        body = "  metric1  42  \n  metric2   3.14  "
        result = app.parse_prometheus_text(body)
        assert "metric1" in result
        assert "metric2" in result


class TestSummarizeKpis:
    """Tests for KPI aggregation and calculation."""

    def test_empty_metrics(self):
        """Empty metrics should produce zero KPIs."""
        result = app.summarize_kpis({})
        for alias in app.KPI_KEYS:
            assert result[alias] == 0.0

    def test_single_nf_metrics(self):
        """Single NF metrics should be extracted correctly."""
        metrics = {
            "amf": {
                "fivegs_amffunction_rm_reginitreq": 100.0,
                "fivegs_amffunction_rm_reginitsucc": 95.0,
            }
        }
        result = app.summarize_kpis(metrics)
        assert result["amf_reg_init_req"] == 100.0
        assert result["amf_reg_init_succ"] == 95.0

    def test_multiple_nf_aggregation(self):
        """Metrics from multiple NFs should be summed."""
        metrics = {
            "amf": {"fivegs_amffunction_rm_registeredsubnbr": 50},
            "smf": {"fivegs_amffunction_rm_registeredsubnbr": 30},
        }
        result = app.summarize_kpis(metrics)
        # The metric lookup should find aggregated value
        assert result["amf_registered_ues"] == 80.0

    def test_registration_success_rate_calculation(self):
        """Registration success rate should be calculated correctly."""
        metrics = {
            "amf": {
                "fivegs_amffunction_rm_reginitreq": 100.0,
                "fivegs_amffunction_rm_reginitsucc": 90.0,
            }
        }
        result = app.summarize_kpis(metrics)
        assert result["amf_reg_success_rate_pct"] == 90.0

    def test_registration_success_rate_zero_requests(self):
        """When requests=0, success rate should be 0."""
        metrics = {
            "amf": {
                "fivegs_amffunction_rm_reginitreq": 0.0,
                "fivegs_amffunction_rm_reginitsucc": 0.0,
            }
        }
        result = app.summarize_kpis(metrics)
        assert result["amf_reg_success_rate_pct"] == 0.0

    def test_missing_metrics_default_to_zero(self):
        """Missing metrics should default to 0."""
        metrics = {"empty_nf": {}}
        result = app.summarize_kpis(metrics)
        assert result["amf_reg_init_req"] == 0.0


class TestArgparseValidators:
    """Tests for custom argparse validators."""

    def test_positive_float_valid(self):
        """Valid positive floats should pass."""
        assert app._positive_float("1.5") == 1.5
        assert app._positive_float("100") == 100.0
        assert app._positive_float("0.001") == 0.001

    def test_positive_float_zero_invalid(self):
        """Zero should be rejected."""
        with pytest.raises(Exception):  # ArgumentTypeError
            app._positive_float("0")

    def test_positive_float_negative_invalid(self):
        """Negative values should be rejected."""
        with pytest.raises(Exception):
            app._positive_float("-5.0")

    def test_positive_float_non_numeric_invalid(self):
        """Non-numeric strings should be rejected."""
        with pytest.raises(Exception):
            app._positive_float("abc")

    def test_positive_int_valid(self):
        """Valid positive integers should pass."""
        assert app._positive_int("5") == 5
        assert app._positive_int("100") == 100

    def test_positive_int_zero_invalid(self):
        """Zero should be rejected."""
        with pytest.raises(Exception):
            app._positive_int("0")

    def test_positive_int_float_invalid(self):
        """Floats should be rejected (int only)."""
        with pytest.raises(Exception):
            app._positive_int("3.14")

    def test_non_negative_int_accepts_zero(self):
        """Non-negative validator should allow zero and positive ints."""
        assert app._non_negative_int("0") == 0
        assert app._non_negative_int("7") == 7

    def test_non_negative_int_rejects_negative(self):
        """Non-negative validator should reject negative values."""
        with pytest.raises(Exception):
            app._non_negative_int("-1")

    def test_valid_hostname_or_ip_ipv4(self):
        """IPv4 addresses should be valid."""
        assert app._valid_hostname_or_ip("192.168.1.1") == "192.168.1.1"
        assert app._valid_hostname_or_ip("8.8.8.8") == "8.8.8.8"

    def test_valid_hostname_or_ip_hostname(self):
        """Hostnames should be valid."""
        assert app._valid_hostname_or_ip("google.com") == "google.com"
        assert app._valid_hostname_or_ip("host_name.example.com") == "host_name.example.com"

    def test_valid_hostname_or_ip_empty_invalid(self):
        """Empty string should be invalid."""
        with pytest.raises(Exception):
            app._valid_hostname_or_ip("")

    def test_valid_hostname_or_ip_special_chars_invalid(self):
        """Special characters should be invalid."""
        with pytest.raises(Exception):
            app._valid_hostname_or_ip("host!name")


class TestParseArgs:
    """Tests for argument parsing behavior."""

    def test_server_and_watch_mutually_exclusive(self, monkeypatch):
        """--server and --watch cannot be used together."""
        monkeypatch.setattr(sys, "argv", ["app.py", "--server", "8080", "--watch", "2"])
        with pytest.raises(SystemExit) as exc:
            app.parse_args()
        assert exc.value.code == 2

    def test_parse_args_delegates_to_config_finalize(self, monkeypatch):
        """parse_args should pass parsed namespace through config finalizer."""
        monkeypatch.setattr(sys, "argv", ["app.py"])

        def _fake_finalize(args, **_kwargs):
            args._finalized = True
            return args

        monkeypatch.setattr(app.config_service, "finalize_parsed_args", _fake_finalize)
        parsed = app.parse_args()
        assert getattr(parsed, "_finalized", False) is True

    def test_parse_args_accepts_zero_steer_interval(self, monkeypatch):
        """--steer-interval 0 should be accepted as explicit steering disable."""
        monkeypatch.setattr(sys, "argv", ["app.py", "--steer-interval", "0"])

        def _passthrough_finalize(args, **_kwargs):
            return args

        monkeypatch.setattr(app.config_service, "finalize_parsed_args", _passthrough_finalize)
        parsed = app.parse_args()
        assert parsed.steer_interval == 0

    def test_parse_args_accepts_zero_steer_interval_from_env(self, monkeypatch):
        """STEER_INTERVAL=0 from environment should disable steering without CLI flag."""
        monkeypatch.setattr(sys, "argv", ["app.py"])
        monkeypatch.setattr(app, "initialize_environment", lambda: None)
        monkeypatch.setenv("STEER_INTERVAL", "0")

        def _passthrough_finalize(args, **_kwargs):
            return args

        monkeypatch.setattr(app.config_service, "finalize_parsed_args", _passthrough_finalize)
        parsed = app.parse_args()
        assert parsed.steer_interval == 0


class TestConfigFinalizeArgs:
    """Tests for post-parse config finalization logic."""

    def test_finalize_raises_parser_error_for_server_watch_conflict(self):
        """Conflicting --server/--watch should invoke parser error callback."""
        args = argparse.Namespace(server=8080, watch=5, debug=False, openwrt_password="", steer_script=None)

        def _parser_error(message):
            raise ValueError(message)

        with pytest.raises(ValueError):
            app.config_service.finalize_parsed_args(
                args,
                parser_error_fn=_parser_error,
                env_get_fn=lambda _key: None,
                set_log_level_fn=lambda _level: None,
                debug_level=10,
                log_warning_fn=lambda _msg: None,
                app_file_path="/tmp/app.py",
            )

    def test_finalize_applies_env_password_and_debug_level(self):
        """Finalizer should apply env password and debug-level callback when enabled."""
        args = argparse.Namespace(server=0, watch=0, debug=True, openwrt_password="", steer_script="/tmp/custom.sh")
        levels = []

        out = app.config_service.finalize_parsed_args(
            args,
            parser_error_fn=lambda _message: None,
            env_get_fn=lambda key: "env-secret" if key == "OPENWRT_PASSWORD" else None,
            set_log_level_fn=lambda level: levels.append(level),
            debug_level=123,
            log_warning_fn=lambda _msg: None,
            app_file_path="/tmp/app.py",
        )

        assert out.openwrt_password == "env-secret"
        assert levels == [123]

    def test_finalize_warns_when_cli_password_overrides_env(self):
        """Finalizer should emit warning when CLI password overrides env password."""
        args = argparse.Namespace(server=0, watch=0, debug=False, openwrt_password="cli-secret", steer_script="/tmp/custom.sh")
        warnings = []

        out = app.config_service.finalize_parsed_args(
            args,
            parser_error_fn=lambda _message: None,
            env_get_fn=lambda key: "env-secret" if key == "OPENWRT_PASSWORD" else None,
            set_log_level_fn=lambda _level: None,
            debug_level=10,
            log_warning_fn=lambda msg: warnings.append(msg),
            app_file_path="/tmp/app.py",
        )

        assert out.openwrt_password == "cli-secret"
        assert any("takes precedence" in msg for msg in warnings)


class TestParseManualEndpoints:
    """Tests for parsing explicit metrics endpoints."""

    def test_parse_empty_endpoints(self):
        """None or empty endpoint string should return no endpoints."""
        assert app._parse_manual_endpoints(None) == []
        assert app._parse_manual_endpoints("") == []

    def test_parse_single_endpoint_with_port(self):
        """A host:port endpoint should be parsed into one Endpoint object."""
        result = app._parse_manual_endpoints("127.0.0.1:9090")
        assert len(result) == 1
        assert result[0].address == "127.0.0.1"
        assert result[0].port == 9090
        assert result[0].url == "http://127.0.0.1:9090/metrics"

    def test_parse_endpoint_without_port_uses_default(self):
        """A host-only endpoint should use the default port 9090."""
        result = app._parse_manual_endpoints("127.0.0.2")
        assert len(result) == 1
        assert result[0].address == "127.0.0.2"
        assert result[0].port == 9090

    def test_parse_invalid_port_skips_endpoint(self):
        """Malformed host:port entries should be ignored safely."""
        result = app._parse_manual_endpoints("127.0.0.1:notaport")
        assert result == []

    def test_parse_with_errors_reports_invalid_tokens(self):
        """Detailed parser should return invalid tokens for malformed entries."""
        endpoints, invalid = app._parse_manual_endpoints_with_errors(
            "127.0.0.1:9090,127.0.0.2:notaport"
        )
        assert len(endpoints) == 1
        assert endpoints[0].address == "127.0.0.1"
        assert invalid == ["127.0.0.2:notaport"]

    def test_parse_bracketed_ipv6_with_port(self):
        """Bracketed IPv6 endpoint with explicit port should parse correctly."""
        result = app._parse_manual_endpoints("[2001:db8::1]:9091")
        assert len(result) == 1
        assert result[0].address == "2001:db8::1"
        assert result[0].port == 9091

    def test_parse_bare_ipv6_uses_default_port(self):
        """Bare IPv6 host should be treated as host-only with default port."""
        result = app._parse_manual_endpoints("2001:db8::2")
        assert len(result) == 1
        assert result[0].address == "2001:db8::2"
        assert result[0].port == 9090


class TestMainExitCodes:
    """Critical exit-code contract tests."""

    def test_main_returns_2_on_parse_error(self, monkeypatch):
        """Argparse configuration errors should map to exit code 2."""
        def _raise_parse_error():
            raise SystemExit(2)

        monkeypatch.setattr(app, "parse_args", _raise_parse_error)
        assert app.main() == 2

    def test_main_returns_3_when_no_metrics_endpoints(self, monkeypatch):
        """No discovered endpoints should return the documented exit code 3."""
        args = argparse.Namespace(
            metrics_endpoints=None,
            raw_metrics="",
            timeout=2.5,
            json=False,
            watch=0,
            server=0,
            openwrt_host="192.168.142.200",
            openwrt_timeout=2.0,
            openwrt_container="openwrt_router",
            openwrt_user="",
            openwrt_password="",
            no_openwrt=True,
            ifaces="eth0,eth1,br-lan,lo",
            steer_interval=None,
            steer_script="/tmp/toggle_route.sh",
            debug=False,
        )
        monkeypatch.setattr(app, "parse_args", lambda: args)
        assert app.main() == 3

    def test_main_returns_2_when_all_configured_endpoints_are_invalid(self, monkeypatch):
        """Malformed configured endpoint list should be treated as CONFIG_ERROR."""
        args = argparse.Namespace(
            metrics_endpoints="127.0.0.1:notaport",
            raw_metrics="",
            timeout=2.5,
            json=False,
            watch=0,
            server=0,
            openwrt_host="192.168.142.200",
            openwrt_timeout=2.0,
            openwrt_container="openwrt_router",
            openwrt_user="",
            openwrt_password="",
            no_openwrt=True,
            ifaces="eth0,eth1,br-lan,lo",
            steer_interval=None,
            steer_script="/tmp/toggle_route.sh",
            debug=False,
        )
        monkeypatch.setattr(app, "parse_args", lambda: args)
        assert app.main() == 2

    def test_main_returns_1_on_one_shot_collection_exception(self, monkeypatch):
        """Unhandled one-shot collection errors should return exit code 1."""
        args = argparse.Namespace(
            metrics_endpoints="127.0.0.1:9090",
            raw_metrics="",
            timeout=2.5,
            json=False,
            watch=0,
            server=0,
            openwrt_host="192.168.142.200",
            openwrt_timeout=2.0,
            openwrt_container="openwrt_router",
            openwrt_user="",
            openwrt_password="",
            no_openwrt=True,
            ifaces="eth0,eth1,br-lan,lo",
            steer_interval=None,
            steer_script="/tmp/toggle_route.sh",
            debug=False,
        )
        monkeypatch.setattr(app, "parse_args", lambda: args)

        endpoints = [app.Endpoint(nf="amf", address="127.0.0.1", port=9090)]
        monkeypatch.setattr(app, "_parse_manual_endpoints_with_errors", lambda _: (endpoints, []))

        def _boom(_: argparse.Namespace, __):
            raise RuntimeError("forced failure")

        monkeypatch.setattr(app, "collect_snapshot", _boom)
        assert app.main() == 1

    def test_main_returns_1_on_json_serialization_error(self, monkeypatch):
        """JSON serialization failures in one-shot mode should return exit code 1."""
        args = argparse.Namespace(
            metrics_endpoints="127.0.0.1:9090",
            raw_metrics="",
            timeout=2.5,
            json=True,
            watch=0,
            server=0,
            openwrt_host="192.168.142.200",
            openwrt_timeout=2.0,
            openwrt_container="openwrt_router",
            openwrt_user="",
            openwrt_password="",
            no_openwrt=True,
            ifaces="eth0,eth1,br-lan,lo",
            steer_interval=None,
            steer_script="/tmp/toggle_route.sh",
            debug=False,
        )
        monkeypatch.setattr(app, "parse_args", lambda: args)

        endpoints = [app.Endpoint(nf="amf", address="127.0.0.1", port=9090)]
        monkeypatch.setattr(app, "_parse_manual_endpoints_with_errors", lambda _: (endpoints, []))

        payload = {
            "timestamp": 1,
            "kpi": {"bad": {1}},
            "raw_metrics": {},
            "network_kpi": {},
            "errors": {},
            "error_categories": {},
            "openwrt": {},
        }
        monkeypatch.setattr(app, "collect_snapshot", lambda *_: payload)
        assert app.main() == 1

    def test_main_does_not_run_steering_script_when_interval_zero(self, monkeypatch):
        """Steering script must not run when steer_interval is explicitly set to 0."""
        args = argparse.Namespace(
            metrics_endpoints="127.0.0.1:9090",
            raw_metrics="",
            timeout=2.5,
            json=True,
            watch=0,
            server=0,
            openwrt_host="192.168.142.200",
            openwrt_timeout=2.0,
            openwrt_container="openwrt_router",
            openwrt_user="",
            openwrt_password="",
            no_openwrt=True,
            ifaces="eth0,eth1,br-lan,lo",
            steer_interval=0,
            steer_script="/tmp/toggle_route.sh",
            debug=False,
        )
        monkeypatch.setattr(app, "parse_args", lambda: args)

        endpoints = [app.Endpoint(nf="amf", address="127.0.0.1", port=9090)]
        monkeypatch.setattr(app, "_parse_manual_endpoints_with_errors", lambda _: (endpoints, []))
        monkeypatch.setattr(
            app,
            "collect_snapshot",
            lambda *_: {
                "timestamp": 1,
                "kpi": {},
                "raw_metrics": {},
                "network_kpi": {},
                "errors": {},
                "openwrt": {},
            },
        )

        steer_mock = mock.Mock()
        monkeypatch.setattr(app, "run_steering_script", steer_mock)

        assert app.main() == 0
        steer_mock.assert_not_called()


class TestErrorCategories:
    """Validate structured error categories in snapshot payloads."""

    def test_collect_snapshot_categorizes_endpoint_errors(self, monkeypatch):
        """Endpoint scrape failures should be labeled as ENDPOINT_FETCH_ERROR."""
        args = argparse.Namespace(
            timeout=2.5,
            raw_metrics="",
            ifaces="eth0,eth1",
            openwrt_container="openwrt_router",
            no_openwrt=True,
            openwrt_host="192.168.142.200",
            openwrt_timeout=2.0,
            openwrt_user="",
            openwrt_password="",
        )

        monkeypatch.setattr(app, "collect_all", lambda *_, **__: ({}, {"amf": "timeout"}))
        monkeypatch.setattr(app, "collect_network_kpis", lambda *_: {})

        payload = app.collect_snapshot(args, [app.Endpoint(nf="amf", address="127.0.0.1", port=9090)])
        assert payload["error_categories"]["endpoint:amf"] == app.ERROR_CATEGORY_ENDPOINT_FETCH

    def test_collect_snapshot_categorizes_openwrt_errors(self, monkeypatch):
        """OpenWrt probe failures should be labeled as OPENWRT_COLLECTION_ERROR."""
        args = argparse.Namespace(
            timeout=2.5,
            raw_metrics="",
            ifaces="eth0,eth1",
            openwrt_container="openwrt_router",
            no_openwrt=False,
            openwrt_host="192.168.142.200",
            openwrt_timeout=2.0,
            openwrt_user="",
            openwrt_password="",
        )

        monkeypatch.setattr(app, "collect_all", lambda *_, **__: ({}, {}))
        monkeypatch.setattr(app, "collect_network_kpis", lambda *_: {})
        monkeypatch.setattr(app, "fetch_openwrt_info", lambda *_: ({}, "openwrt failed"))

        payload = app.collect_snapshot(args, [app.Endpoint(nf="amf", address="127.0.0.1", port=9090)])
        assert payload["error_categories"]["openwrt"] == app.ERROR_CATEGORY_OPENWRT_COLLECTION
        assert payload["openwrt_error"] == "openwrt failed"

    def test_collect_snapshot_categorizes_network_kpi_errors(self, monkeypatch):
        """Network KPI collection failures should be categorized and surfaced in errors."""
        args = argparse.Namespace(
            timeout=2.5,
            raw_metrics="",
            ifaces="eth0,eth1",
            openwrt_container="openwrt_router",
            no_openwrt=True,
            openwrt_host="192.168.142.200",
            openwrt_timeout=2.0,
            openwrt_user="",
            openwrt_password="",
        )

        monkeypatch.setattr(app, "collect_all", lambda *_, **__: ({}, {}))

        def _raise_network_error(*_):
            raise RuntimeError("network collection failed")

        monkeypatch.setattr(app, "collect_network_kpis", _raise_network_error)

        payload = app.collect_snapshot(args, [app.Endpoint(nf="amf", address="127.0.0.1", port=9090)])
        assert payload["error_categories"]["network_kpi"] == app.ERROR_CATEGORY_OPENWRT_COLLECTION
        assert "network collection failed" in payload["errors"]["network_kpi"]


class TestSnapshotOrchestration:
    """Ensure CLI and server paths reuse the shared snapshot collector."""

    def test_main_one_shot_calls_collect_snapshot_once(self, monkeypatch):
        """One-shot collection should call the shared snapshot function exactly once."""
        args = argparse.Namespace(
            metrics_endpoints="127.0.0.1:9090",
            raw_metrics="",
            timeout=2.5,
            json=True,
            watch=0,
            server=0,
            openwrt_host="192.168.142.200",
            openwrt_timeout=2.0,
            openwrt_container="openwrt_router",
            openwrt_user="",
            openwrt_password="",
            no_openwrt=True,
            ifaces="eth0,eth1,br-lan,lo",
            steer_interval=None,
            steer_script="/tmp/toggle_route.sh",
            debug=False,
        )
        monkeypatch.setattr(app, "parse_args", lambda: args)

        endpoints = [app.Endpoint(nf="amf", address="127.0.0.1", port=9090)]
        monkeypatch.setattr(app, "_parse_manual_endpoints_with_errors", lambda _: (endpoints, []))

        payload = {
            "timestamp": 1,
            "kpi": {},
            "raw_metrics": {},
            "network_kpi": {},
            "errors": {},
            "openwrt": {},
        }
        collect_mock = mock.Mock(return_value=payload)
        monkeypatch.setattr(app, "collect_snapshot", collect_mock)

        assert app.main() == 0
        collect_mock.assert_called_once_with(args, endpoints)

    def test_http_server_kpi_route_uses_collect_snapshot(self):
        """HTTP /kpi route should delegate payload building to collect_snapshot."""
        if not app.HAS_FLASK:
            pytest.skip("Flask is not installed")

        args = argparse.Namespace(
            metrics_endpoints="127.0.0.1:9090",
            raw_metrics="",
            timeout=2.5,
            json=True,
            watch=0,
            server=8080,
            openwrt_host="192.168.142.200",
            openwrt_timeout=2.0,
            openwrt_container="openwrt_router",
            openwrt_user="",
            openwrt_password="",
            no_openwrt=True,
            ifaces="eth0,eth1,br-lan,lo",
            steer_interval=None,
            steer_script="/tmp/toggle_route.sh",
            debug=False,
        )

        payload = {
            "timestamp": 1,
            "kpi": {"amf_reg_init_req": 1.0},
            "raw_metrics": {},
            "network_kpi": {},
            "errors": {},
            "openwrt": {},
        }

        with mock.patch("app.collect_snapshot", return_value=payload) as collect_mock:
            flask_app = app.create_http_server(args)
            client = flask_app.test_client()
            response = client.get("/kpi")

            assert response.status_code == 200
            assert response.get_json()["kpi"]["amf_reg_init_req"] == 1.0
            collect_mock.assert_called_once()

    def test_http_server_error_payload_has_category(self):
        """Server fallback response should use a runtime error category."""
        if not app.HAS_FLASK:
            pytest.skip("Flask is not installed")

        args = argparse.Namespace(
            metrics_endpoints="127.0.0.1:9090",
            raw_metrics="",
            timeout=2.5,
            json=True,
            watch=0,
            server=8080,
            openwrt_host="192.168.142.200",
            openwrt_timeout=2.0,
            openwrt_container="openwrt_router",
            openwrt_user="",
            openwrt_password="",
            no_openwrt=True,
            ifaces="eth0,eth1,br-lan,lo",
            steer_interval=None,
            steer_script="/tmp/toggle_route.sh",
            debug=False,
        )

        with mock.patch("app.collect_snapshot", side_effect=RuntimeError("boom")):
            flask_app = app.create_http_server(args)
            client = flask_app.test_client()
            response = client.get("/kpi")

            assert response.status_code == 200
            body = response.get_json()
            assert body["error_categories"]["runtime"] == app.ERROR_CATEGORY_RUNTIME

    def test_http_server_invalid_endpoint_config_returns_config_error(self):
        """Invalid-only endpoint configuration should return categorized config error payload."""
        if not app.HAS_FLASK:
            pytest.skip("Flask is not installed")

        args = argparse.Namespace(
            metrics_endpoints="127.0.0.1:notaport",
            raw_metrics="",
            timeout=2.5,
            json=True,
            watch=0,
            server=8080,
            openwrt_host="192.168.142.200",
            openwrt_timeout=2.0,
            openwrt_container="openwrt_router",
            openwrt_user="",
            openwrt_password="",
            no_openwrt=True,
            ifaces="eth0,eth1,br-lan,lo",
            steer_interval=None,
            steer_script="/tmp/toggle_route.sh",
            debug=False,
        )

        with mock.patch("app.collect_snapshot") as collect_mock:
            flask_app = app.create_http_server(args)
            client = flask_app.test_client()
            response = client.get("/kpi")

            assert response.status_code == 200
            body = response.get_json()
            assert body["error_categories"]["config"] == app.ERROR_CATEGORY_CONFIG
            assert "invalid" in body["error"].lower()
            assert body["invalid_endpoints"] == ["127.0.0.1:notaport"]
            collect_mock.assert_not_called()

    def test_http_server_no_endpoint_config_returns_config_error(self):
        """Missing endpoint configuration should return categorized config error payload."""
        if not app.HAS_FLASK:
            pytest.skip("Flask is not installed")

        args = argparse.Namespace(
            metrics_endpoints=None,
            raw_metrics="",
            timeout=2.5,
            json=True,
            watch=0,
            server=8080,
            openwrt_host="192.168.142.200",
            openwrt_timeout=2.0,
            openwrt_container="openwrt_router",
            openwrt_user="",
            openwrt_password="",
            no_openwrt=True,
            ifaces="eth0,eth1,br-lan,lo",
            steer_interval=None,
            steer_script="/tmp/toggle_route.sh",
            debug=False,
        )

        with mock.patch("app.collect_snapshot") as collect_mock:
            flask_app = app.create_http_server(args)
            client = flask_app.test_client()
            response = client.get("/kpi")

            assert response.status_code == 200
            body = response.get_json()
            assert body["error_categories"]["config"] == app.ERROR_CATEGORY_CONFIG
            assert body["error"] == "No metrics endpoints discovered"
            collect_mock.assert_not_called()


class TestOutputParity:
    """Validate payload parity between CLI JSON mode and HTTP /kpi route."""

    def test_cli_json_and_http_kpi_payload_match(self, monkeypatch, capsys):
        """The shared snapshot payload should serialize identically in both surfaces."""
        if not app.HAS_FLASK:
            pytest.skip("Flask is not installed")

        args = argparse.Namespace(
            metrics_endpoints="127.0.0.1:9090",
            raw_metrics="",
            timeout=2.5,
            json=True,
            watch=0,
            server=0,
            openwrt_host="192.168.142.200",
            openwrt_timeout=2.0,
            openwrt_container="openwrt_router",
            openwrt_user="",
            openwrt_password="",
            no_openwrt=True,
            ifaces="eth0,eth1,br-lan,lo",
            steer_interval=None,
            steer_script="/tmp/toggle_route.sh",
            debug=False,
        )
        monkeypatch.setattr(app, "parse_args", lambda: args)

        endpoints = [app.Endpoint(nf="amf", address="127.0.0.1", port=9090)]
        monkeypatch.setattr(app, "_parse_manual_endpoints_with_errors", lambda _: (endpoints, []))

        payload = {
            "timestamp": 123,
            "kpi": {"amf_reg_init_req": 2.0, "amf_reg_init_succ": 2.0},
            "raw_metrics": {"custom_metric": 10.0},
            "network_kpi": {"network": {"interfaces": {}}},
            "errors": {},
            "openwrt": {},
            "openwrt_error": "test-openwrt-error",
        }
        monkeypatch.setattr(app, "collect_snapshot", lambda *_: payload)

        assert app.main() == 0
        cli_output = capsys.readouterr().out.strip()
        cli_payload = json.loads(cli_output)

        server_args = argparse.Namespace(**vars(args))
        server_args.server = 8080
        flask_app = app.create_http_server(server_args)
        client = flask_app.test_client()
        response = client.get("/kpi")

        assert response.status_code == 200
        http_payload = response.get_json()
        assert cli_payload == http_payload


class TestHttpServerRunner:
    """Tests for HTTP server runner wrapper and service behavior."""

    def test_run_http_server_wrapper_delegates_to_server_service(self, monkeypatch):
        """app.run_http_server should delegate to server_service.run_http_server."""
        args = argparse.Namespace(server=8080)
        captured: Dict[str, Any] = {}

        def _fake_runner(*runner_args, **runner_kwargs):
            captured["runner_args"] = runner_args
            captured["runner_kwargs"] = runner_kwargs
            return 0

        monkeypatch.setattr(app.server_service, "run_http_server", _fake_runner)

        result = app.run_http_server(args, 8080)
        assert result == 0
        assert captured["runner_args"][0] is args
        assert captured["runner_args"][1] == 8080
        assert callable(captured["runner_kwargs"]["create_http_server_fn"])

    def test_server_service_run_http_server_returns_1_on_exception(self):
        """Service runner should return 1 when app creation fails."""
        args = argparse.Namespace(server=8080)
        error_lines = []

        def _raise_create(_):
            raise RuntimeError("boom")

        class _FakeLogger:
            def setLevel(self, _):
                return None

        result = app.server_service.run_http_server(
            args,
            8080,
            create_http_server_fn=_raise_create,
            get_logger_fn=lambda _: _FakeLogger(),
            warning_level=30,
            log_info_fn=lambda _: None,
            log_error_fn=lambda *_args, **_kwargs: None,
            print_error_fn=lambda message: error_lines.append(message),
        )

        assert result == 1
        assert any("boom" in line for line in error_lines)


class TestHumanOutput:
    """Tests for human-readable output wrapper and service rendering."""

    def test_print_human_wrapper_delegates_to_output_service(self, monkeypatch):
        """app.print_human should delegate to output_service.print_human."""
        captured = {}

        def _fake_print_human(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

        monkeypatch.setattr(app.output_service, "print_human", _fake_print_human)
        endpoints = [app.Endpoint(nf="amf", address="127.0.0.1", port=9090)]

        app.print_human(
            endpoints=endpoints,
            summary={"amf_reg_init_req": 1.0},
            errors={},
            openwrt={},
            openwrt_error=None,
            network_kpi={},
            raw_metrics={},
        )

        assert captured["args"][0] == endpoints
        assert callable(captured["kwargs"]["printer"])
        assert callable(captured["kwargs"]["json_dumps_fn"])

    def test_output_service_print_human_renders_and_logs(self):
        """Service formatter should print key sections and trigger log callbacks for errors."""
        lines = []
        logged_errors = []
        logged_warnings = []
        endpoints = [app.Endpoint(nf="amf", address="127.0.0.1", port=9090)]

        app.output_service.print_human(
            endpoints=endpoints,
            summary={"amf_reg_init_req": 1.0, "amf_reg_success_rate_pct": 100.0},
            errors={"amf": "timeout"},
            openwrt={"interfaces": ["eth0"]},
            openwrt_error="probe-failed",
            network_kpi={"network": {"interfaces": {}}},
            raw_metrics={"custom_metric": 3.0},
            printer=lambda line: lines.append(line),
            json_dumps_fn=json.dumps,
            log_error_fn=lambda msg: logged_errors.append(msg),
            log_warning_fn=lambda msg: logged_warnings.append(msg),
        )

        text = "\n".join(lines)
        assert "Open5GS KPI Snapshot" in text
        assert "Raw Metrics" in text
        assert "Network/System KPIs" in text
        assert "OpenWrt Error" in text
        assert any("Collection error [amf]" in msg for msg in logged_errors)
        assert any("OpenWrt probe error" in msg for msg in logged_warnings)


class TestSteeringRuntime:
    """Tests for steering runtime wrapper and service behavior."""

    def test_run_steering_script_wrapper_delegates(self, monkeypatch):
        """app.run_steering_script should delegate to runtime_service.run_steering_script."""
        captured = {}

        def _fake_runner(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

        monkeypatch.setattr(app.runtime_service, "run_steering_script", _fake_runner)
        app.run_steering_script("/tmp/toggle_route.sh")

        assert captured["args"][0] == "/tmp/toggle_route.sh"
        assert callable(captured["kwargs"]["path_exists_fn"])
        assert callable(captured["kwargs"]["run_cmd_fn"])
        assert captured["kwargs"]["timeout_s"] == 15

    def test_runtime_service_logs_missing_script(self):
        """Runtime service should log a clear error when script path does not exist."""
        errors = []

        app.runtime_service.run_steering_script(
            "/tmp/missing.sh",
            path_exists_fn=lambda _path: False,
            run_cmd_fn=lambda *_args, **_kwargs: None,
            printer=lambda _line: None,
            log_info_fn=lambda _msg: None,
            log_error_fn=lambda msg: errors.append(msg),
            timeout_exception_cls=TimeoutError,
            timeout_s=15,
        )

        assert any("Steering script not found" in msg for msg in errors)

    def test_runtime_service_handles_timeout(self):
        """Runtime service should log timeout errors from command execution."""
        errors = []

        def _raise_timeout(*_args, **_kwargs):
            raise TimeoutError()

        app.runtime_service.run_steering_script(
            "/tmp/toggle_route.sh",
            path_exists_fn=lambda _path: True,
            run_cmd_fn=_raise_timeout,
            printer=lambda _line: None,
            log_info_fn=lambda _msg: None,
            log_error_fn=lambda msg: errors.append(msg),
            timeout_exception_cls=TimeoutError,
            timeout_s=15,
        )

        assert any("timed out" in msg for msg in errors)


class TestOpenWrtServiceExtraction:
    """Tests for OpenWrt wrapper delegation and service shaping."""

    def test_collect_openwrt_raw_metrics_wrapper_delegates(self, monkeypatch):
        """app.collect_openwrt_raw_metrics should delegate to openwrt_service."""
        captured = {}

        def _fake_collect(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return {"source": "openwrt_container"}

        monkeypatch.setattr(app.openwrt_service, "collect_openwrt_raw_metrics", _fake_collect)

        out = app.collect_openwrt_raw_metrics("openwrt_router", ["eth0"])
        assert out["source"] == "openwrt_container"
        assert captured["args"][0] == "openwrt_router"
        assert captured["args"][1] == ["eth0"]
        assert callable(captured["kwargs"]["read_openwrt_proc_net_dev_fn"])

    def test_collect_network_kpis_wrapper_delegates(self, monkeypatch):
        """app.collect_network_kpis should delegate to openwrt_service."""
        captured = {}

        def _fake_collect(cfg, **kwargs):
            captured["cfg"] = cfg
            captured["kwargs"] = kwargs
            return {"network": {"source": "openwrt_container"}, "system": {}, "conntrack": {}}

        monkeypatch.setattr(app.openwrt_service, "collect_network_kpis", _fake_collect)
        cfg = app.NetworkKpiConfig(interfaces=["eth0"], openwrt_container="openwrt_router")

        out = app.collect_network_kpis(cfg)
        assert out["network"]["source"] == "openwrt_container"
        assert captured["cfg"] == cfg
        assert callable(captured["kwargs"]["collect_openwrt_raw_metrics_fn"])

    def test_openwrt_service_collect_network_kpis_shapes_payload(self):
        """Service collect_network_kpis should map raw sections into output shape."""
        cfg = app.NetworkKpiConfig(interfaces=["eth0"], openwrt_container="openwrt_router")

        def _fake_raw(container, interfaces):
            assert container == "openwrt_router"
            assert interfaces == ["eth0"]
            return {
                "source": "openwrt_container",
                "container": "openwrt_router",
                "interfaces": {"eth0": {"rx_bytes": 10}},
                "system": {"cpu_stat": {}},
                "conntrack": {"conntrack_count": 1},
            }

        out = app.openwrt_service.collect_network_kpis(
            cfg,
            collect_openwrt_raw_metrics_fn=_fake_raw,
        )

        assert out["network"]["container"] == "openwrt_router"
        assert "eth0" in out["network"]["interfaces"]
        assert out["conntrack"]["conntrack_count"] == 1


class TestNetworkServiceExtraction:
    """Tests for network diagnostics wrapper extraction."""

    def test_run_cmd_wrapper_delegates(self, monkeypatch):
        """app._run_cmd should delegate to network_service.run_cmd."""
        captured = {}

        def _fake_run_cmd(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return "ok"

        monkeypatch.setattr(app.network_service, "run_cmd", _fake_run_cmd)
        out = app._run_cmd(["echo", "hi"])

        assert out == "ok"
        assert captured["args"][0] == ["echo", "hi"]
        assert callable(captured["kwargs"]["run_cmd_fn"])
        assert captured["kwargs"]["timeout_s"] == 10

    def test_ping_stats_wrapper_delegates(self, monkeypatch):
        """app._ping_stats should delegate to network_service.ping_stats."""
        captured = {}

        def _fake_ping(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return {"target": "8.8.8.8", "ping_success": True}

        monkeypatch.setattr(app.network_service, "ping_stats", _fake_ping)
        out = app._ping_stats("8.8.8.8", 3, 5.0)

        assert out["ping_success"] is True
        assert captured["args"][0] == "8.8.8.8"
        assert captured["args"][1] == 3
        assert callable(captured["kwargs"]["run_cmd_fn"])

    def test_network_service_parse_tc_qdisc_parses_sent_and_backlog(self):
        """network_service.parse_tc_qdisc should parse sent/backlog fields from tc output."""
        tc_output = (
            "qdisc fq_codel 0: root refcnt 2\n"
            " Sent 1234 bytes 56 pkt (dropped 7, overlimits 8 requeues 9)\n"
            " backlog 10b 11p requeues 0\n"
        )

        out = app.network_service.parse_tc_qdisc(
            "eth0",
            run_cmd_fn=lambda _args: tc_output,
            log_debug_fn=lambda _msg: None,
        )

        assert out["qdisc_sent_bytes"] == 1234
        assert out["qdisc_sent_packets"] == 56
        assert out["qdisc_backlog_bytes"] == 10
        assert out["qdisc_backlog_packets"] == 11


class TestHostServiceExtraction:
    """Tests for host metrics wrapper extraction."""

    def test_read_proc_net_dev_wrapper_delegates(self, monkeypatch):
        """app._read_proc_net_dev should delegate to host_service.read_proc_net_dev."""
        captured = {}

        def _fake_read_proc(**kwargs):
            captured["kwargs"] = kwargs
            return {"lo": {"rx_bytes": 0}}

        monkeypatch.setattr(app.host_service, "read_proc_net_dev", _fake_read_proc)
        out = app._read_proc_net_dev()

        assert "lo" in out
        assert callable(captured["kwargs"]["read_text_fn"])
        assert callable(captured["kwargs"]["log_error_fn"])

    def test_read_memory_usage_wrapper_delegates(self, monkeypatch):
        """app._read_memory_usage should delegate to host_service.read_memory_usage."""
        captured = {}

        def _fake_read_memory(**kwargs):
            captured["kwargs"] = kwargs
            return {"mem_used_kb": 123}

        monkeypatch.setattr(app.host_service, "read_memory_usage", _fake_read_memory)
        out = app._read_memory_usage()

        assert out["mem_used_kb"] == 123
        assert callable(captured["kwargs"]["read_text_fn"])

    def test_host_service_read_memory_usage_parses_values(self):
        """host_service.read_memory_usage should compute used memory percentage."""
        meminfo = "MemTotal:       1000 kB\nMemAvailable:    250 kB\n"

        out = app.host_service.read_memory_usage(
            read_text_fn=lambda _path: meminfo,
            log_warning_fn=lambda _msg: None,
            log_debug_fn=lambda _msg: None,
        )

        assert out["mem_total_kb"] == 1000
        assert out["mem_available_kb"] == 250
        assert out["mem_used_kb"] == 750
        assert abs(out["mem_used_pct"] - 75.0) < 1e-9


class TestReadProcNetDev:
    """Tests for /proc/net/dev parsing."""

    def test_parse_valid_proc_net_dev(self):
        """Parse valid /proc/net/dev output."""
        mock_data = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:       0       0    0    0    0     0          0         0        0       0    0    0    0     0       0          0
  ens33: 1000000  100000    0    5    0     0          0         0  2000000  150000    0    3    0     0       0          0
"""
        with mock.patch("app._read_text", return_value=mock_data):
            result = app._read_proc_net_dev()
            assert "lo" in result
            assert "ens33" in result
            assert result["ens33"]["rx_bytes"] == 1000000
            assert result["ens33"]["tx_packets"] == 150000
            assert result["ens33"]["rx_drop"] == 5
            assert result["ens33"]["tx_errs"] == 0

    def test_parse_malformed_proc_net_dev_skips_bad_lines(self):
        """Malformed lines should be skipped."""
        mock_data = """Inter-|   Receive                                                |  Transmit
  ens33: not_a_number bad data
  ens34: 1000 100
"""
        with mock.patch("app._read_text", return_value=mock_data):
            result = app._read_proc_net_dev()
            # ens33 should be skipped due to bad data
            # ens34 should also be skipped (insufficient columns)
            assert result == {}


class TestPingStats:
    """Tests for ping output parsing."""

    def test_parse_successful_ping(self):
        """Parse output from successful ping."""
        ping_output = """PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.
64 bytes from 8.8.8.8: icmp_seq=1 ttl=119 time=12.3 ms
64 bytes from 8.8.8.8: icmp_seq=2 ttl=119 time=11.8 ms
64 bytes from 8.8.8.8: icmp_seq=3 ttl=119 time=12.5 ms

--- 8.8.8.8 statistics ---
3 packets transmitted, 3 received, 0% packet loss, time 2024ms
rtt min/avg/max/stddev = 11.8/12.2/12.5/0.3 ms
"""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(
                returncode=0, stdout=ping_output, stderr=""
            )
            result = app._ping_stats("8.8.8.8", 3, 5.0)
            assert result["ping_success"] is True
            assert result["ping_tx_packets"] == 3
            assert result["ping_rx_packets"] == 3
            assert result["ping_loss_pct"] == 0.0
            assert result["ping_rtt_min_ms"] == 11.8
            assert result["ping_rtt_avg_ms"] == 12.2
            assert result["ping_rtt_max_ms"] == 12.5
            assert result["ping_jitter_ms"] == 0.3

    def test_parse_ping_with_loss(self):
        """Parse ping output with packet loss."""
        ping_output = """3 packets transmitted, 2 received, 33% packet loss"""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(
                returncode=1, stdout=ping_output, stderr=""
            )
            result = app._ping_stats("host.com", 3, 5.0)
            assert result["ping_tx_packets"] == 3
            assert result["ping_rx_packets"] == 2
            assert abs(result["ping_loss_pct"] - 33.333) < 0.1  # 1/3 = 33.33%


class TestIntegration:
    """Integration tests combining multiple functions."""

    def test_collect_all_handles_individual_endpoint_failures(self):
        """collect_all should continue if one endpoint fails."""
        endpoints = [
            app.Endpoint(nf="amf", address="127.0.0.1", port=9090),
            app.Endpoint(nf="smf", address="127.0.0.2", port=9091),
        ]

        with mock.patch("app.fetch_endpoint_metrics") as mock_fetch:
            # First succeeds, second fails
            mock_fetch.side_effect = [
                {"metric1": 10.0},
                Exception("Connection refused"),
            ]
            per_nf, errors = app.collect_all(endpoints, timeout=5.0)

            assert len(per_nf) == 1
            assert len(errors) == 1
            assert set(per_nf.keys()) | set(errors.keys()) == {"amf", "smf"}
            failing_nf = next(iter(errors))
            assert "Exception: Connection refused" in errors[failing_nf]

    def test_collect_all_uses_shared_session_and_bounded_workers(self, monkeypatch):
        """collect_all should use a shared retrying session and bounded worker count."""
        endpoints = [
            app.Endpoint(nf="amf", address="127.0.0.1", port=9090),
            app.Endpoint(nf="smf", address="127.0.0.2", port=9091),
            app.Endpoint(nf="upf", address="127.0.0.3", port=9092),
        ]

        class _FakeSession:
            def __init__(self):
                self.closed = False

            def get(self, *_args, **_kwargs):
                return mock.MagicMock(text="", raise_for_status=lambda: None)

            def close(self):
                self.closed = True

        fake_session = _FakeSession()
        captured = {}

        monkeypatch.setattr(
            app.prometheus_service,
            "build_retrying_session",
            lambda **_kwargs: fake_session,
        )

        def _fake_fetch_endpoint_metrics(_endpoint, _timeout, **kwargs):
            captured["requests_get_fn"] = kwargs["requests_get_fn"]
            return {"metric": 1.0}

        monkeypatch.setattr(
            app.prometheus_service,
            "fetch_endpoint_metrics",
            _fake_fetch_endpoint_metrics,
        )

        def _fake_collect_all(_endpoints, _timeout, **kwargs):
            captured["max_workers"] = kwargs["max_workers"]
            first = list(_endpoints)[0]
            kwargs["fetch_endpoint_metrics_fn"](first, _timeout)
            return {first.nf: {"metric": 1.0}}, {}

        monkeypatch.setattr(app.prometheus_service, "collect_all", _fake_collect_all)

        per_nf, errors = app.collect_all(endpoints, timeout=2.5)
        assert errors == {}
        assert len(per_nf) == 1
        assert captured["max_workers"] == 3
        assert captured["requests_get_fn"] == fake_session.get
        assert fake_session.closed is True

    def test_round_trip_metrics_to_output(self):
        """Test end-to-end metric collection and formatting."""
        per_nf = {
            "amf": {
                "fivegs_amffunction_rm_reginitreq": 100.0,
                "fivegs_amffunction_rm_reginitsucc": 95.0,
            }
        }
        summary = app.summarize_kpis(per_nf)

        assert summary["amf_reg_init_req"] == 100.0
        assert summary["amf_reg_init_succ"] == 95.0
        assert summary["amf_reg_success_rate_pct"] == 95.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
