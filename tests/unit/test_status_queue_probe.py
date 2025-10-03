"""Unit tests for _maybe_probe_and_annotate"""

import unittest
from unittest.mock import MagicMock

import requests

from serve_event_listener import status_queue as sq
from serve_event_listener.el_types import StatusRecord
from serve_event_listener.status_queue import StatusQueue


def make_record(
    *,
    release: str = "r1",
    status: str = "Running",
    app_type: str | None = "shiny",
    app_url: str | None = "http://r1.example/",
) -> StatusRecord:
    """Build a minimal StatusRecord for tests."""
    rec: StatusRecord = {
        "release": release,
        "status": status,
        "event-ts": "2025-09-26T12:00:00.000000Z",
    }
    if app_type is not None:
        rec["app-type"] = app_type  # type: ignore[assignment]
    if app_url is not None:
        rec["app-url"] = app_url  # type: ignore[assignment]
    return rec


class TestStatusQueueMaybeProbeAnnotateSkips(unittest.TestCase):
    """Scenarios where probing must be skipped and record left unchanged."""

    def setUp(self):
        """Create a queue and snapshot original module flags."""
        self.session = MagicMock(spec=requests.Session)
        self.q = StatusQueue(
            session=self.session,
            url="https://api.example/app-status/",
            token="T0",
            token_fetcher=None,
            prober=None,  # default to no prober; set per-test when needed
        )
        # Snapshot globals to restore after each test
        self._orig_statuses = set(sq.APP_PROBE_STATUSES)
        self._orig_apps = set(getattr(sq, "APP_PROBE_APPS", set()))

        # Sensible defaults: enable Running/Deleted and shiny apps
        sq.APP_PROBE_STATUSES = {"running", "deleted"}
        sq.APP_PROBE_APPS = {"shiny", "shiny-proxy"}

    def tearDown(self):
        """Restore module-level flags."""
        sq.APP_PROBE_STATUSES = self._orig_statuses
        sq.APP_PROBE_APPS = self._orig_apps

    def test_skip_when_no_prober(self):
        """No prober injected should skip probing."""
        rec = make_record(
            status="Running", app_type="shiny", app_url="http://r1.example/"
        )
        out = self.q._maybe_probe_and_annotate(rec)
        self.assertIs(out, rec)  # same object, no mutation
        self.assertNotIn("curl-probe", out)  # no probe attached

    def test_skip_when_probe_statuses_empty(self):
        """APP_PROBE_STATUSES empty should skip probing."""
        sq.APP_PROBE_STATUSES = set()
        self.q.prober = MagicMock()
        rec = make_record(
            status="Running", app_type="shiny", app_url="http://r1.example/"
        )
        out = self.q._maybe_probe_and_annotate(rec)
        self.assertIs(out, rec)
        self.q.prober.probe_url.assert_not_called()

    def test_skip_when_app_type_not_allowed(self):
        """App type not in APP_PROBE_APPS should skip probing."""
        sq.APP_PROBE_APPS = {"shiny", "shiny-proxy"}  # allowed set excludes 'jupyter'
        self.q.prober = MagicMock()
        rec = make_record(
            status="Running", app_type="jupyter", app_url="http://r1.example/"
        )
        out = self.q._maybe_probe_and_annotate(rec)
        self.assertIs(out, rec)
        self.q.prober.probe_url.assert_not_called()

    def test_skip_when_status_not_configured(self):
        """status not in APP_PROBE_STATUSES should skip probing."""
        sq.APP_PROBE_STATUSES = {"deleted"}  # running not enabled
        self.q.prober = MagicMock()
        rec = make_record(
            status="Running", app_type="shiny", app_url="http://r1.example/"
        )
        out = self.q._maybe_probe_and_annotate(rec)
        self.assertIs(out, rec)
        self.q.prober.probe_url.assert_not_called()

    def test_skip_when_app_url_missing(self):
        """Missing app-url should skip probing."""
        self.q.prober = MagicMock()
        rec = make_record(status="Running", app_type="shiny", app_url=None)
        out = self.q._maybe_probe_and_annotate(rec)
        self.assertIs(out, rec)
        self.q.prober.probe_url.assert_not_called()


if __name__ == "__main__":
    unittest.main()
