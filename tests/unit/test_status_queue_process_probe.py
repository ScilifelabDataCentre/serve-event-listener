"""Unit tests for StatusQueue.process probing behavior (non-blocking, gated by config)."""

import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

import serve_event_listener.status_queue as sq_mod
from serve_event_listener.el_types import StatusRecord
from serve_event_listener.status_queue import StatusQueue


def iso_now(offset_sec: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_sec)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )


class TestStatusQueueProcessProbe(unittest.TestCase):
    def setUp(self):
        # Patch module-level config so tests are deterministic
        self.orig_statuses = set(sq_mod.APP_PROBE_STATUSES)
        self.orig_apps = set(sq_mod.APP_PROBE_APPS)
        self.orig_running_window = sq_mod.RUNNING_PROBE_WINDOW
        self.orig_running_interval = sq_mod.RUNNING_PROBE_INTERVAL
        self.orig_deleted_window = sq_mod.DELETED_PROBE_WINDOW
        self.orig_deleted_interval = sq_mod.DELETED_PROBE_INTERVAL
        self.orig_nx_confirm = getattr(sq_mod, "NXDOMAIN_CONFIRMATION_COUNT", 2)

        # deterministic defaults for each test
        sq_mod.APP_PROBE_STATUSES = {"running", "deleted"}
        sq_mod.APP_PROBE_APPS = {"shiny", "shiny-proxy"}
        sq_mod.NXDOMAIN_CONFIRMATION_COUNT = 2

        # speed up tests
        self.sleep_patcher = patch(
            "serve_event_listener.status_queue.time.sleep", return_value=None
        )
        self.sleep_patcher.start()
        self.session = MagicMock(spec=requests.Session)
        self.url = "https://api.example/app-status/"
        self.token = "T0"

    def tearDown(self):
        self.sleep_patcher.stop()
        sq_mod.APP_PROBE_STATUSES = self.orig_statuses
        sq_mod.APP_PROBE_APPS = self.orig_apps
        sq_mod.RUNNING_PROBE_WINDOW = self.orig_running_window
        sq_mod.RUNNING_PROBE_INTERVAL = self.orig_running_interval
        sq_mod.DELETED_PROBE_WINDOW = self.orig_deleted_window
        sq_mod.DELETED_PROBE_INTERVAL = self.orig_deleted_interval
        sq_mod.NXDOMAIN_CONFIRMATION_COUNT = self.orig_nx_confirm

    def _run_worker_briefly(self, q: StatusQueue, dt: float = 0.05):
        t = threading.Thread(target=q.process, daemon=True)
        t.start()
        time.sleep(dt)
        q.stop_processing()
        t.join(timeout=1)

    @patch("serve_event_listener.status_queue.http_post")
    def test_running_not_probed_when_config_excludes_running(self, mock_post):
        """Running should post immediately when 'running' not in APP_PROBE_STATUSES."""
        sq_mod.APP_PROBE_STATUSES = {"deleted"}  # disable running probes
        prober = MagicMock()
        prober.probe_url.return_value = SimpleNamespace(
            status="Unknown", port80_status=None, note=""
        )
        q = StatusQueue(self.session, self.url, self.token, prober=prober)

        rec: StatusRecord = {
            "release": "r1",
            "new-status": "Running",
            "event-ts": iso_now(),
            "app-type": "shiny",
            "app-url": "http://r1-app/",
        }
        q.add(rec)
        mock_post.return_value = SimpleNamespace(status_code=200)

        self._run_worker_briefly(q)

        prober.probe_url.assert_not_called()
        self.assertEqual(mock_post.call_count, 1)

    @patch("serve_event_listener.status_queue.http_post")
    def test_running_shiny_probes_until_confirmed_then_posts(self, mock_post):
        """Running shiny should probe; post once probe returns Running."""
        prober = MagicMock()
        prober.probe_url.side_effect = [
            SimpleNamespace(status="Unknown", port80_status=None, note=""),
            SimpleNamespace(status="Running", port80_status=200, note="ok"),
        ]
        sq_mod.RUNNING_PROBE_INTERVAL = 0.0  # fast retry

        q = StatusQueue(self.session, self.url, self.token, prober=prober)
        rec: StatusRecord = {
            "release": "r1",
            "new-status": "Running",
            "event-ts": iso_now(),
            "app-type": "shiny",
            "app-url": "http://r1-app/",
        }
        q.add(rec)
        mock_post.return_value = SimpleNamespace(status_code=200)

        self._run_worker_briefly(q, dt=0.12)

        self.assertEqual(mock_post.call_count, 1)  # posted once
        self.assertGreaterEqual(prober.probe_url.call_count, 2)

    @patch("serve_event_listener.status_queue.http_post")
    def test_deleted_requires_two_consecutive_nxdomain(self, mock_post):
        """Deleted confirmation requires 2x NotFound by default."""
        prober = MagicMock()
        prober.probe_url.side_effect = [
            SimpleNamespace(status="NotFound", port80_status=None, note=""),
            SimpleNamespace(status="NotFound", port80_status=None, note=""),
        ]
        sq_mod.DELETED_PROBE_INTERVAL = 0.0  # fast retry
        sq_mod.NXDOMAIN_CONFIRMATION_COUNT = 2

        q = StatusQueue(self.session, self.url, self.token, prober=prober)
        rec: StatusRecord = {
            "release": "rX",
            "new-status": "Deleted",
            "event-ts": iso_now(),
            "app-type": "shiny",
            "app-url": "http://rX-app/",
        }
        q.add(rec)
        mock_post.return_value = SimpleNamespace(status_code=200)

        self._run_worker_briefly(q, dt=0.12)

        self.assertEqual(mock_post.call_count, 1)  # posted after 2x NotFound
        self.assertGreaterEqual(prober.probe_url.call_count, 2)

    @patch("serve_event_listener.status_queue.http_post")
    def test_deleted_probing_disabled_uses_legacy_grace(self, mock_post):
        """When 'deleted' not enabled, legacy <30s grace requeue is used."""
        sq_mod.APP_PROBE_STATUSES = {"running"}  # disable 'deleted' probing
        prober = MagicMock()
        q = StatusQueue(self.session, self.url, self.token, prober=prober)
        rec: StatusRecord = {
            "release": "rY",
            "new-status": "Deleted",
            "event-ts": iso_now(),
            "app-type": "shiny",
            "app-url": "http://rY-app/",
        }
        q.add(rec)

        self._run_worker_briefly(q, dt=0.06)

        prober.probe_url.assert_not_called()
        self.assertEqual(mock_post.call_count, 0)  # requeued due to grace
        self.assertGreaterEqual(q.queue.qsize(), 1)

    @patch("serve_event_listener.status_queue.http_post")
    def test_deleted_immediate_post_when_confirm_threshold_is_one(self, mock_post):
        """If confirmation threshold is 1, a single NotFound posts immediately."""
        prober = MagicMock()
        prober.probe_url.return_value = SimpleNamespace(
            status="NotFound", port80_status=None, note="dns"
        )
        sq_mod.DELETED_PROBE_INTERVAL = 0.0
        with patch.dict(
            sq_mod.os.environ, {"APP_PROBE_NXDOMAIN_CONFIRM": "1"}, clear=False
        ):
            # reassign
            sq_mod.NXDOMAIN_CONFIRMATION_COUNT = 1

            q = StatusQueue(self.session, self.url, self.token, prober=prober)
            rec: StatusRecord = {
                "release": "r2",
                "new-status": "Deleted",
                "event-ts": iso_now(),
                "app-type": "shiny",
                "app-url": "http://r2-app/",
            }
            q.add(rec)
            mock_post.return_value = SimpleNamespace(status_code=200)

            self._run_worker_briefly(q)

        prober.probe_url.assert_called_once()
        self.assertEqual(mock_post.call_count, 1)

    @patch("serve_event_listener.status_queue.http_post")
    def test_deleted_times_out_then_posts(self, mock_post):
        """If Deleted never confirms NotFound before window, post after timeout."""
        prober = MagicMock()
        prober.probe_url.return_value = SimpleNamespace(
            status="Unknown", port80_status=None, note=""
        )
        # Set event-ts in the past so we are already past the 30s window
        old_iso = iso_now(offset_sec=-(sq_mod.DELETED_PROBE_WINDOW + 5))

        q = StatusQueue(self.session, self.url, self.token, prober=prober)
        rec: StatusRecord = {
            "release": "r3",
            "new-status": "Deleted",
            "event-ts": old_iso,
            "app-type": "shiny",
            "app-url": "http://r3-app/",
        }
        q.add(rec)
        mock_post.return_value = SimpleNamespace(status_code=200)

        self._run_worker_briefly(q)

        self.assertEqual(mock_post.call_count, 1)
        self.assertGreaterEqual(prober.probe_url.call_count, 0)


@patch("serve_event_listener.status_queue.http_post")
@patch("serve_event_listener.status_queue.logger")
def test_post_404_object_not_found_logged_as_debug(mock_logger, mock_post):
    """404 with 'OK. OBJECT_NOT_FOUND.' body should log debug, not a POST-failed warning."""
    mock_post.return_value = SimpleNamespace(
        status_code=404, text="OK. OBJECT_NOT_FOUND."
    )

    # disable probing so we post immediately
    orig_statuses = set(sq_mod.APP_PROBE_STATUSES)
    sq_mod.APP_PROBE_STATUSES = set()

    try:
        session = MagicMock(spec=requests.Session)
        q = StatusQueue(session, "https://api.example/app-status/", "T0")

        rec: StatusRecord = {
            "release": "r404",
            "new-status": "Running",  # any non-probed status
            "event-ts": "2025-01-01T00:00:00.000000Z",
        }
        q.add(rec)

        t = threading.Thread(target=q.process, daemon=True)
        t.start()
        time.sleep(0.05)
        q.stop_processing()
        t.join(timeout=1)

        # We should have logged a debug line mentioning the 404 handling
        debug_msgs = " | ".join(
            str(call.args[0]) for call in mock_logger.debug.call_args_list
        )
        assert "404 treated as OK" in debug_msgs

        # We should NOT have logged a warning that says "POST failed" (other warnings may exist, e.g., stop message)
        warn_msgs = " | ".join(
            str(call.args[0]) for call in mock_logger.warning.call_args_list
        )
        assert "POST failed" not in warn_msgs
    finally:
        sq_mod.APP_PROBE_STATUSES = orig_statuses
