"""Integration tests for EventListener against the live Serve API."""

from unittest.mock import MagicMock, patch

from serve_event_listener.event_listener import EventListener
from tests.integration.base import IntegrationTestCase


class TestEventListenerIntegration(IntegrationTestCase):  # type: ignore[name-defined]
    """Smoke-level integration tests using a live API."""

    def setUp(self):
        """Create a fresh EventListener for each test."""
        self.el = EventListener()

    def test_check_serve_api_status(self):
        """Returns True when /openapi/v1/are-you-there is healthy."""
        self.assertTrue(self.el.check_serve_api_status())

    def test_fetch_token_success(self):
        """fetch_token returns a non-empty token and updates state."""
        token = self.el.fetch_token()
        self.assertIsInstance(token, str)
        self.assertTrue(len(token) > 0)
        self.assertEqual(self.el.token, token)

    @patch.object(EventListener, "setup_client")
    def test_setup_builds_status_queue_without_k8s(self, mock_setup_client):
        """setup() builds StatusQueue using live API but skips k8s client."""
        # Simulate setup_client creating a client attribute
        mock_setup_client.side_effect = lambda *a, **k: setattr(
            self.el, "client", MagicMock()
        )

        # Avoid touching real k8s client wiring
        with patch.object(
            self.el._status_data, "set_k8s_api_client", return_value=None
        ):
            self.el.setup()

        self.assertTrue(self.el.setup_complete)
        self.assertIsNotNone(self.el._status_queue)
