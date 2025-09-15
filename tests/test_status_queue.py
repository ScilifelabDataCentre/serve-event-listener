import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

from serve_event_listener.status_queue import StatusQueue


class TestStatusQueue(unittest.TestCase):
    def setUp(self):
        """Create a queue with a shared fake Session and defaults."""
        self.session = MagicMock(spec=requests.Session)
        self.url = "https://api.fake.url/app-status"
        self.token = "T0"

    # ---------- add() ----------

    def test_add_enqueues_item(self):
        """add() places one item on the queue."""
        q = StatusQueue(self.session, self.url, self.token)
        item = {
            "release": "r1",
            "new-status": "Running",
            "event-ts": "2025-01-01T00:00:00.000000Z",
        }
        q.add(item)
        self.assertEqual(q.queue.qsize(), 1)

    # ---------- process(): happy path (exposes early-return bug) ----------

    @patch("serve_event_listener.status_queue.http_post")
    def test_process_handles_multiple_items(self, mock_post):
        """process() should send all items (will FAIL until early-return is fixed)."""
        mock_post.return_value = SimpleNamespace(status_code=200)

        q = StatusQueue(self.session, self.url, self.token)

        # Two items to process
        q.add(
            {
                "release": "r1",
                "new-status": "Running",
                "event-ts": "2025-01-01T00:00:00.000000Z",
            }
        )
        q.add(
            {
                "release": "r2",
                "new-status": "Running",
                "event-ts": "2025-01-01T00:00:00.000000Z",
            }
        )

        t = threading.Thread(target=q.process, daemon=True)
        t.start()

        # Give the worker a moment to process
        time.sleep(0.1)
        q.stop_processing()
        t.join(timeout=1)

        # Expect two posts; current code returns after first -> this test will show 1
        self.assertEqual(
            mock_post.call_count, 2, "Early return in process() prevents second item"
        )

    # ---------- process(): Deleted event requeue (<30s) ----------

    @patch("serve_event_listener.status_queue.time.sleep", return_value=None)
    @patch("serve_event_listener.status_queue.http_post")
    def test_deleted_recent_is_requeued_and_not_posted(self, mock_post, _sleep):
        """Deleted events <30s old should be requeued and not posted."""
        # Event timestamp ~now to trigger requeue path
        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        q = StatusQueue(self.session, self.url, self.token)
        q.add({"release": "r1", "new-status": "Deleted", "event-ts": now_iso})

        t = threading.Thread(target=q.process, daemon=True)
        t.start()

        # Let it cycle once (pop, requeue)
        time.sleep(0.05)
        q.stop_processing()
        t.join(timeout=1)

        # Should not have posted anything
        self.assertEqual(mock_post.call_count, 0)

        # Item was requeued at least once
        self.assertGreaterEqual(q.queue.qsize(), 1)

    # ---------- headers passed to http_post ----------

    @patch("serve_event_listener.status_queue.http_post")
    def test_headers_include_bearer_token(self, mock_post):
        """Authorization header should include current Bearer/Token token."""
        mock_post.return_value = SimpleNamespace(status_code=200)
        q = StatusQueue(self.session, self.url, "T123")

        q.add(
            {
                "release": "r1",
                "new-status": "Running",
                "event-ts": "2025-01-01T00:00:00.000000Z",
            }
        )

        t = threading.Thread(target=q.process, daemon=True)
        t.start()
        time.sleep(0.05)
        q.stop_processing()
        t.join(timeout=1)

        # Inspect headers from the first call
        _, kwargs = mock_post.call_args
        headers = kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Token T123")
        self.assertEqual(headers["Content-Type"], "application/json")

    # ---------- stop_processing() ----------

    def test_stop_processing_sets_event(self):
        """stop_processing() sets the stop event flag."""
        q = StatusQueue(self.session, self.url, self.token)
        self.assertFalse(q.stop_event.is_set())
        q.stop_processing()
        self.assertTrue(q.stop_event.is_set())


if __name__ == "__main__":
    unittest.main()
