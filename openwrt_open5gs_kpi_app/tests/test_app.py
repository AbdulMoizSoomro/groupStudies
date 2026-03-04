"""
Unit tests for Open5GS KPI application.

Tests parsing logic, validation, and error handling without requiring
running services or actual network access.
"""

import pytest
import sys
from pathlib import Path
from unittest import mock
from io import StringIO

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


class TestOptionalDependencies:
    """Verify module-level flags for optional packages."""

    def test_has_uv_flag(self):
        """HAS_UV should reflect import capability."""
        # we can't guarantee uv is installed in CI/runtime, but the flag should be set
        # to either True or False without raising.
        assert isinstance(app.HAS_UV, bool)


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


class TestDiscoverMetricsEndpoints:
    """Tests for YAML config parsing and endpoint discovery."""

    def test_discover_valid_endpoints(self):
        """Discover endpoints from valid config."""
        config = """
amf:
  metrics:
    server:
      - address: 127.0.0.1
        port: 9090

smf:
  metrics:
    server:
      - address: 127.0.0.2
        port: 9091
"""
        with mock.patch("builtins.open", mock.mock_open(read_data=config)):
            with mock.patch("yaml.safe_load") as mock_yaml:
                mock_yaml.return_value = {
                    "amf": {
                        "metrics": {
                            "server": [{"address": "127.0.0.1", "port": 9090}]
                        }
                    },
                    "smf": {
                        "metrics": {
                            "server": [{"address": "127.0.0.2", "port": 9091}]
                        }
                    },
                }
                result = app.discover_metrics_endpoints("/fake/config.yaml")
                assert len(result) == 2
                assert result[0].nf == "amf"
                assert result[0].url == "http://127.0.0.1:9090/metrics"
                assert result[1].nf == "smf"
                assert result[1].url == "http://127.0.0.2:9091/metrics"

    def test_discover_no_metrics_section(self):
        """NFs without metrics section should be skipped."""
        config = {
            "amf": {"notmetrics": {}},
            "smf": {"metrics": None},
        }
        # patch file open since the function reads the config file first
        with mock.patch("builtins.open", mock.mock_open(read_data="")):
            with mock.patch("yaml.safe_load", return_value=config):
                result = app.discover_metrics_endpoints("/fake/config.yaml")
                assert len(result) == 0

    def test_discover_malformed_config_raises_error(self):
        """Malformed YAML should raise YAMLError."""
        with mock.patch("yaml.safe_load", side_effect=Exception("Bad YAML")):
            with pytest.raises(Exception):
                app.discover_metrics_endpoints("/fake/config.yaml")

    def test_discover_skips_mme(self):
        """Entries named 'mme' should not create endpoints."""
        # even if metrics server is configured, the code ignores MME
        with mock.patch("builtins.open", mock.mock_open(read_data="")):
            with mock.patch("yaml.safe_load", return_value={
                "amf": {"metrics": {"server": [{"address": "127.0.0.1"}] }},
                "mme": {"metrics": {"server": [{"address": "127.0.0.2"}] }},
                "smf": {"metrics": {"server": [{"address": "127.0.0.3"}] }},
            }):
                result = app.discover_metrics_endpoints("/fake/config.yaml")
                # should only include amf and smf
                nfs = {ep.nf for ep in result}
                assert "amf" in nfs and "smf" in nfs
                assert "mme" not in nfs


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

            assert "amf" in per_nf
            assert "smf" in errors
            assert len(errors["smf"]) > 0

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
