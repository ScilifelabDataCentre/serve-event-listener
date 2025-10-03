"""Integration tests for app URL resolution (shiny-proxy)."""

import os
import socket
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

from serve_event_listener.app_urls import resolve_app_url
from serve_event_listener.el_types import StatusRecord
from serve_event_listener.http_client import get as http_get
from serve_event_listener.http_client import make_session
from tests.integration.base import IntegrationTestCase


class TestAppUrlResolverIntegration(IntegrationTestCase):
    """Resolve URLs for a given release/namespace and optionally smoke-check connectivity."""

    def setUp(self):
        """Build a minimal StatusRecord from env (release + namespace required)."""
        release = os.getenv("PROBE_RELEASE") or os.getenv("RELEASE_UNDER_TEST")
        namespace = os.getenv("NAMESPACE_UNDER_TEST") or "default"
        if not release:
            raise unittest.SkipTest(
                "Set PROBE_RELEASE or RELEASE_UNDER_TEST for resolver tests"
            )

        # Minimal StatusRecord; resolver only needs release/app-type/namespace
        self.rec: StatusRecord = {
            "release": release,
            "status": "Running",
            "event-ts": "2025-09-26T00:00:00.000000Z",
            "app-type": "shiny-proxy",
            "namespace": namespace,
        }

    @patch.dict(
        os.environ, {"APP_URL_DNS_MODE": "short", "APP_URL_PORT": "80"}, clear=False
    )
    def test_builds_expected_short_dns_url(self):
        """URL should follow service.namespace form with default port/path."""
        url = resolve_app_url(self.rec)
        self.assertIsNotNone(url)
        # Example: http://<release>-shinyproxyapp.<ns>:80/app/<release>/
        release = self.rec["release"]
        ns = self.rec["namespace"]  # type: ignore[index]
        expected_host = f"{release}-shinyproxyapp.{ns}"
        parsed = urlparse(url)  # type: ignore[arg-type]
        self.assertEqual(parsed.scheme, "http")
        self.assertTrue(parsed.hostname.startswith(expected_host))
        self.assertTrue(parsed.path.endswith(f"/app/{release}/"))

    def test_dns_resolution_behavior(self):
        """DNS may or may not resolve here; in either case we should not crash."""
        url = resolve_app_url(self.rec)
        self.assertIsNotNone(
            url, "Resolver returned None, should return a url for shiny-proxy"
        )

        host = urlparse(url).hostname
        self.assertIsNotNone(host, "URL lacks hostname")

        # Either it resolves or it doesn't — both are acceptable outcomes.
        try:
            socket.getaddrinfo(host, None)
            resolved = True
        except socket.gaierror:
            resolved = False

        # Assert we captured one of the two states (this is mostly a sanity check)
        self.assertIn(resolved, (True, False))

    def test_http_smoke_result(self):
        """HTTP GET should either return a Response or None (on error/timeout)."""
        url = resolve_app_url(self.rec)
        self.assertIsNotNone(
            url, "Resolver returned None, should return a url for shiny-proxy"
        )

        session = make_session(total_retries=1)
        resp = http_get(session, url, timeout=(0.5, 1.0), backoff_seconds=(0.2,))
        self.assertIn(type(resp).__name__, ("NoneType", "Response"))
