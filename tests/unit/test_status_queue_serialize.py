"""Unit tests for converting StatusRecord to PostPayload."""

import copy
import unittest

from serve_event_listener.el_types import PostPayload, StatusRecord
from serve_event_listener.status_queue import StatusQueue


class TestStatusQueueToPostPayload(unittest.TestCase):
    """Verify StatusRecord to PostPayload mapping."""

    def test_minimal_record_maps_with_null_event_msg(self):
        """Minimal record produces payload with event-msg keys set to None."""
        rec: StatusRecord = {
            "release": "r1",
            "status": "Running",
            "event-ts": "2025-09-26T12:00:00.000000Z",
            # no pod-msg/container-msg/app-type/app-url/curl-probe
        }

        payload: PostPayload = StatusQueue._to_post_payload(rec)

        self.assertEqual(payload["release"], "r1")
        self.assertEqual(payload["new-status"], "Running")
        self.assertEqual(payload["event-ts"], "2025-09-26T12:00:00.000000Z")
        self.assertIn("event-msg", payload)
        self.assertIn("pod-msg", payload["event-msg"])
        self.assertIn("container-msg", payload["event-msg"])
        self.assertIsNone(payload["event-msg"]["pod-msg"])
        self.assertIsNone(payload["event-msg"]["container-msg"])

    def test_full_record_maps_all_optional_fields(self):
        """Optional fields (messages/app-type/app-url/curl-probe) are forwarded."""
        rec: StatusRecord = {
            "release": "r2",
            "status": "Deleted",
            "event-ts": "2025-09-26T12:34:56.000000Z",
            "pod-msg": "pod deleted",
            "container-msg": "N/A",
            "app-type": "shiny",
            "app-url": "http://r2.example/",
            "curl-probe": {
                "status": "NotFound",
                "port80_status": None,
                "note": "DNS resolution failed",
                "url": "http://r2.example/",
            },
        }

        payload: PostPayload = StatusQueue._to_post_payload(rec)

        self.assertEqual(payload["release"], "r2")
        self.assertEqual(payload["new-status"], "Deleted")
        self.assertEqual(payload["event-ts"], "2025-09-26T12:34:56.000000Z")
        self.assertEqual(payload["event-msg"]["pod-msg"], "pod deleted")
        self.assertEqual(payload["event-msg"]["container-msg"], "N/A")

        # Not yet in the payload:
        # self.assertEqual(payload["app-type"], "shiny")
        # self.assertEqual(payload["app-url"], "http://r2.example/")
        # self.assertEqual(payload["curl-probe"]["status"], "NotFound")

    def test_conversion_does_not_mutate_input_record(self):
        """_to_post_payload must not mutate the original record dict."""
        rec: StatusRecord = {
            "release": "r3",
            "status": "Unknown",
            "event-ts": "2025-09-26T12:00:00.000000Z",
            "pod-msg": None,
        }
        before = copy.deepcopy(rec)

        _ = StatusQueue._to_post_payload(rec)

        self.assertEqual(rec, before, "Input StatusRecord was mutated by conversion")
