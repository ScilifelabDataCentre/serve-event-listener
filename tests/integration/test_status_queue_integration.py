"""Integration tests for StatusQueue against the live Serve API."""

import os
import threading
import time
from unittest.mock import patch

from serve_event_listener.event_listener import EventListener
from serve_event_listener.http_client import make_session, tls_verify_from_env
from serve_event_listener.status_queue import StatusQueue
from tests.integration.base import IntegrationTestCase
from tests.integration.utils import iso_now


class TestStatusQueueIntegration(IntegrationTestCase):
    """Smoke-level integration tests for the queue processor (HTTP only, no k8s)."""

    def setUp(self):
        """Create shared session/token and a queue instance."""
        verify = tls_verify_from_env()
        self.session = make_session(total_retries=3, verify=verify)
        self.el = EventListener()  # reuse its fetch_token()
        self.token = self.el.fetch_token()
        # Resolve endpoint (base already validated in IntegrationTestCase)
        self.endpoint = os.getenv(
            "APP_STATUS_API_ENDPOINT",
            os.environ["BASE_URL"].rstrip("/") + "/api/v1/app-status/",
        )
        self.q = StatusQueue(
            session=self.session,
            url=self.endpoint,
            token=self.token,
            token_fetcher=self.el.fetch_token,  # keeps token fresh if 401/403
        )

    def _run_worker_briefly(self, queue, seconds: float = 0.1):
        """Run queue.process() in a daemon thread for a brief period."""
        t = threading.Thread(target=queue.process, daemon=True)
        t.start()
        time.sleep(seconds)
        queue.stop_processing()
        t.join(timeout=2)
        return t

    # ---------------------- tests ----------------------

    def test_deleted_recent_is_requeued(self):
        """A 'Deleted' event <30s old is requeued (no POST attempted)."""
        # Craft a Deleted status with 'event-ts' ~ now
        item = {
            "release": "itest-q-deleted",
            "status": "Deleted",
            "event-ts": iso_now(),
        }
        self.q.add(item)

        # Patch http_post to ensure we don't hit the API for this path
        with patch("serve_event_listener.status_queue.http_post") as spy_post:
            self._run_worker_briefly(self.q, seconds=0.1)
            self.assertGreaterEqual(self.q.queue.qsize(), 1)  # got requeued
            spy_post.assert_not_called()
