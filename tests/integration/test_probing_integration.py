"""Integration tests for AppAvailabilityProbe (real DNS + HTTP)."""

import os
import unittest

from serve_event_listener.http_client import make_session
from serve_event_listener.probing import AppAvailabilityProbe
from tests.integration.base import IntegrationTestCase


class TestAppAvailabilityProbeIntegration(IntegrationTestCase):
    """Probe real endpoints to classify Running / Unknown / NotFound."""

    def setUp(self):
        """Build a real Session and a probe with short timeouts."""
        self.session = make_session(total_retries=2)
        self.probe = AppAvailabilityProbe(
            self.session,
            verify_tls=True,
            timeout=(0.5, 1.0),
            backoff_seconds=(0.2, 0.4),
        )

    def test_running_on_live_endpoint(self):
        """Returns Running on a healthy live endpoint (base or PROBE_URL)."""
        url = (
            os.getenv("PROBE_URL")
            or os.getenv("BASE_URL")
        )
        if not url:
            raise unittest.SkipTest("Set PROBE_URL or BASE_URL")
        if not url.endswith("api/are-you-there/"):
            # Hit the /api/are-you-there/ endpoint
            url = f'{url.rstrip("/")}/api/are-you-there/'
        res = self.probe.probe_url(url)
        # Most healthy bases should be 2xx/3xx → Running. If your base needs auth and
        # returns 401, adjust this assertion or provide PROBE_URL that is public.
        self.assertEqual(res.status, "Running", f"Unexpected result for {url}: {res}")

    def test_notfound_on_nonexistent_host(self):
        """Returns NotFound on a guaranteed-nonexistent domain."""
        res = self.probe.probe_url("http://no-such-host.invalid/")
        self.assertEqual(res.status, "NotFound")

    def test_unknown_on_local_refused(self):
        """Returns Unknown when host resolves but connection is refused."""
        # Port 9 is typically closed; adjust if your CI environment differs.
        res = self.probe.probe_url("http://127.0.0.1:9/")
        self.assertEqual(res.status, "Unknown")
