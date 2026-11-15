"""Offline-safe tests for the checks: parsing and error paths only.

Command output is mocked, so nothing here pings, reads the live routing
table, or depends on this machine's network.
"""

import json
import unittest
from unittest import mock

from cyberdeck import diagnostics as d


class CommandOutputTests(unittest.TestCase):
    def test_missing_binary_is_reported(self):
        out, err = d._command_output(["definitely-not-a-real-binary-xyz"])
        self.assertIsNone(out)
        self.assertEqual(err, "definitely-not-a-real-binary-xyz not installed")

    def test_non_zero_exit_is_reported(self):
        out, err = d._command_output(["false"])
        self.assertIsNotNone(err)
        self.assertTrue(err.startswith("exit 1"), err)

    def test_timeout_is_reported(self):
        out, err = d._command_output(["sleep", "5"], timeout=0.1)
        self.assertIsNone(out)
        self.assertIn("timed out", err)


class InterfacesTests(unittest.TestCase):
    def _ip_output(self, *interfaces):
        return json.dumps(list(interfaces)), None

    def test_counts_only_up_interfaces_with_ipv4(self):
        payload = self._ip_output(
            {"ifname": "lo", "operstate": "UNKNOWN",
             "addr_info": [{"family": "inet", "local": "127.0.0.1"}]},
            {"ifname": "wlan0", "operstate": "UP",
             "addr_info": [{"family": "inet", "local": "192.168.1.20"},
                           {"family": "inet6", "local": "fe80::1"}]},
            {"ifname": "eth0", "operstate": "DOWN",
             "addr_info": [{"family": "inet", "local": "10.0.0.5"}]},
        )
        with mock.patch.object(d, "_command_output", return_value=payload):
            result = d.check_interfaces()
        self.assertTrue(result.ok)
        self.assertEqual(result.summary, "1 up")
        self.assertIn("wlan0 192.168.1.20", result.detail)
        self.assertNotIn("eth0", result.detail)
        self.assertNotIn("127.0.0.1", result.detail)

    def test_no_up_interface_fails(self):
        payload = self._ip_output({"ifname": "eth0", "operstate": "DOWN", "addr_info": []})
        with mock.patch.object(d, "_command_output", return_value=payload):
            result = d.check_interfaces()
        self.assertFalse(result.ok)
        self.assertEqual(result.summary, "0 up")

    def test_bad_json_does_not_raise(self):
        with mock.patch.object(d, "_command_output", return_value=("not json", None)):
            result = d.check_interfaces()
        self.assertFalse(result.ok)
        self.assertIn("parse", result.detail)


class DefaultRouteTests(unittest.TestCase):
    def test_gateway_is_returned_for_pinging(self):
        payload = json.dumps([{"gateway": "10.0.0.1", "dev": "wlan0", "protocol": "dhcp"}]), None
        with mock.patch.object(d, "_command_output", return_value=payload):
            result, gateway = d.check_default_route()
        self.assertTrue(result.ok)
        self.assertEqual(gateway, "10.0.0.1")
        self.assertEqual(result.summary, "10.0.0.1")
        self.assertIn("dev wlan0", result.detail)

    def test_no_route_returns_no_gateway(self):
        with mock.patch.object(d, "_command_output", return_value=("[]", None)):
            result, gateway = d.check_default_route()
        self.assertFalse(result.ok)
        self.assertIsNone(gateway)


class DnsConfigTests(unittest.TestCase):
    def test_nameservers_are_listed(self):
        content = "# generated\nnameserver 100.100.100.100\nnameserver fd00::53\n"
        with mock.patch("builtins.open", mock.mock_open(read_data=content)):
            result = d.check_dns_config()
        self.assertTrue(result.ok)
        self.assertEqual(result.summary, "2 server(s)")
        self.assertIn("100.100.100.100", result.detail)

    def test_no_nameserver_fails(self):
        with mock.patch("builtins.open", mock.mock_open(read_data="# empty\n")):
            result = d.check_dns_config()
        self.assertFalse(result.ok)
        self.assertEqual(result.summary, "0 servers")

    def test_unreadable_file_is_reported(self):
        with mock.patch("builtins.open", side_effect=OSError("permission denied")):
            result = d.check_dns_config()
        self.assertFalse(result.ok)
        self.assertIn("permission denied", result.detail)


class PingTests(unittest.TestCase):
    def test_rtt_is_parsed(self):
        output = "64 bytes: time=12.3 ms\n1 packets transmitted, 1 received, 0% packet loss\n"
        with mock.patch.object(d, "_command_output", return_value=(output, None)):
            result = d._ping("Ping host", "10.0.0.1")
        self.assertTrue(result.ok)
        self.assertEqual(result.summary, "12.3 ms")

    def test_non_zero_exit_means_no_reply_not_an_error(self):
        with mock.patch.object(d, "_command_output", return_value=("", "exit 1: ")):
            result = d._ping("Ping host", "10.0.0.1")
        self.assertFalse(result.ok)
        self.assertEqual(result.summary, "no reply")

    def test_missing_ping_is_reported_as_error(self):
        with mock.patch.object(d, "_command_output", return_value=(None, "ping not installed")):
            result = d._ping("Ping host", "10.0.0.1")
        self.assertFalse(result.ok)
        self.assertEqual(result.summary, "error")
        self.assertIn("not installed", result.detail)

    def test_no_gateway_fails_without_running_ping(self):
        with mock.patch.object(d, "_command_output") as run:
            result = d.check_gateway_ping(None)
        run.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.summary, "no gateway")


class ListeningPortsTests(unittest.TestCase):
    def test_listen_rows_are_counted(self):
        output = (
            "State Recv-Q Send-Q Local Address:Port Peer Address:Port\n"
            "LISTEN 0      128    0.0.0.0:22         0.0.0.0:*\n"
            "LISTEN 0      128    127.0.0.1:5006     0.0.0.0:*\n"
            "ESTAB  0      0      10.0.0.2:22        10.0.0.9:51000\n"
        )
        with mock.patch.object(d, "_command_output", return_value=(output, None)):
            result = d.check_listening_ports()
        self.assertTrue(result.ok)
        self.assertEqual(result.summary, "2 listening")
        self.assertIn("0.0.0.0:22", result.detail)
        self.assertNotIn("ESTAB", result.detail)

    def test_no_ports_is_not_a_failure(self):
        header = "State Recv-Q Send-Q Local Address:Port Peer Address:Port\n"
        with mock.patch.object(d, "_command_output", return_value=(header, None)):
            result = d.check_listening_ports()
        self.assertTrue(result.ok)
        self.assertEqual(result.summary, "none")


if __name__ == "__main__":
    unittest.main()
