"""Unit tests for AppAvailabilityProbe (DNS + HTTP classification)."""

import socket
import unittest
from unittest.mock import MagicMock, patch

import requests

from serve_event_listener.probing import AppAvailabilityProbe, ProbeResult


class TestAppAvailabilityProbe(unittest.TestCase):
    """Classify results based on mocked DNS and HTTP outcomes."""

    def setUp(self):
        """Create a probe with a fake Session and short timeouts."""
        self.session = MagicMock(spec=requests.Session)
        self.probe = AppAvailabilityProbe(
            self.session,
            verify_tls=True,
            timeout=(0.2, 0.5),
            backoff_seconds=(0.05, 0.1),
        )

    @patch("serve_event_listener.probing.http_get")
    @patch("serve_event_listener.probing.socket.getaddrinfo")
    def test_notfound_when_dns_fails(self, mock_dns, mock_http_get):
        """Returns NotFound when DNS cannot resolve the host."""
        mock_dns.side_effect = socket.gaierror()
        res = self.probe.probe_url("http://no-such-host.invalid/")
        self.assertIsInstance(res, ProbeResult)
        self.assertEqual(res.status, "NotFound")
        mock_http_get.assert_not_called()

    @patch("serve_event_listener.probing.http_get")
    @patch("serve_event_listener.probing.socket.getaddrinfo")
    def test_running_on_200(self, mock_dns, mock_http_get):
        """Returns Running on HTTP 200."""
        mock_dns.return_value = [(None,)]
        mock_http_get.return_value = MagicMock(status_code=200)

        res = self.probe.probe_url("http://example.com/")
        self.assertEqual(res.status, "Running")
        self.assertEqual(res.port80_status, 200)

        # Arguments are forwarded correctly
        _, kwargs = mock_http_get.call_args
        self.assertTrue(kwargs.get("allow_redirects"))
        self.assertEqual(kwargs.get("timeout"), (0.2, 0.5))

    @patch("serve_event_listener.probing.http_get")
    @patch("serve_event_listener.probing.socket.getaddrinfo")
    def test_running_on_3xx(self, mock_dns, mock_http_get):
        """Returns Running on HTTP 302 redirect."""
        mock_dns.return_value = [(None,)]
        mock_http_get.return_value = MagicMock(status_code=302)
        res = self.probe.probe_url("http://example.com/")
        self.assertEqual(res.status, "Running")
        self.assertEqual(res.port80_status, 302)

    @patch("serve_event_listener.probing.http_get")
    @patch("serve_event_listener.probing.socket.getaddrinfo")
    def test_unknown_on_network_error(self, mock_dns, mock_http_get):
        """Returns Unknown when HTTP wrapper returns None (timeout/refused)."""
        mock_dns.return_value = [(None,)]
        mock_http_get.return_value = None
        res = self.probe.probe_url("http://example.com/")
        self.assertEqual(res.status, "Unknown")
        self.assertIsNone(res.port80_status)

    @patch("serve_event_listener.probing.http_get")
    @patch("serve_event_listener.probing.socket.getaddrinfo")
    def test_unknown_on_5xx(self, mock_dns, mock_http_get):
        """Returns Unknown on server 5xx."""
        mock_dns.return_value = [(None,)]
        mock_http_get.return_value = MagicMock(status_code=503)
        res = self.probe.probe_url("http://example.com/")
        self.assertEqual(res.status, "Unknown")
        self.assertEqual(res.port80_status, 503)

    @patch("serve_event_listener.probing.http_get")
    @patch("serve_event_listener.probing.socket.getaddrinfo")
    def test_unknown_on_4xx(self, mock_dns, mock_http_get):
        """Returns Unknown on client 4xx."""
        mock_dns.return_value = [(None,)]
        mock_http_get.return_value = MagicMock(status_code=404)
        res = self.probe.probe_url("http://example.com/")
        self.assertEqual(res.status, "Unknown")
        self.assertEqual(res.port80_status, 404)

    @patch("serve_event_listener.probing.http_get")
    @patch("serve_event_listener.probing.socket.getaddrinfo")
    def test_notfound_when_url_has_no_host(self, mock_dns, mock_http_get):
        """Returns NotFound when URL has no hostname."""
        # urlparse(host) -> None triggers NotFound before DNS call
        res = self.probe.probe_url("http:///nohost")
        self.assertEqual(res.status, "NotFound")
        mock_dns.assert_not_called()
        mock_http_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
