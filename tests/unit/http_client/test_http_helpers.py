import unittest
from unittest.mock import MagicMock

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from serve_event_listener.http_client import _request, make_session


class TestMakeSession(unittest.TestCase):
    """Tests for make_session: adapter mounts and retry policy."""

    def test_make_session_mounts_and_retries(self):
        """Test that make_session mounts correctly and Retry config is set as expected."""
        s = make_session(total_retries=3)
        https_adapter = s.get_adapter("https://")

        # Assert correct adapter type
        self.assertIsInstance(https_adapter, HTTPAdapter)

        # Assert Retry policy
        r = https_adapter.max_retries
        self.assertIsInstance(r, Retry)
        self.assertEqual(r.total, 3)
        self.assertIn("GET", r.allowed_methods)
        self.assertIn(503, r.status_forcelist)


class TestRequestRetryLogic(unittest.TestCase):
    """
    Test retry behavior in _request for 5xx errors, timeouts, and token refresh.
    The tests pass in faked sleep_fn arguments rather than patching time.sleep.
    """

    def setUp(self):
        """Create a mock session for all tests."""
        self.mock_session = MagicMock()
        self.mock_session.headers = {"Accept": "application/json"}

    def test_retry_on_500_error(self):
        """Test that _request retries on 500 errors and succeeds on the second attempt."""
        # Setup a mock to track sleep calls, instead of patching sleep
        sleep_calls = []

        def track_sleep(delay):
            sleep_calls.append(delay)

        # Setup: First call returns 500, second returns 200
        mock_resp_500 = MagicMock(status_code=500)
        mock_resp_200 = MagicMock(status_code=200)
        mock_resp_200.json.return_value = {"success": True}
        self.mock_session.request.side_effect = [mock_resp_500, mock_resp_200]

        response = _request(
            session=self.mock_session,
            method="GET",
            url="http://fake.url",
            backoff_seconds=(0.1, 0.2),  # Short backoff for testing
            sleep_fn=track_sleep,  # Use our tracking function
        )

        # Assert
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"success": True})
        self.assertEqual(self.mock_session.request.call_count, 2)
        # One sleep between attempts; status 200 returns immediately
        self.assertEqual(len(sleep_calls), 1)
        self.assertEqual(sleep_calls[0], 0.1)  # First backoff value

    def test_retry_exhausted_on_500(self):
        """Test that _request stops retrying after exhausting backoff_seconds."""
        # Setup a mock to track sleep calls, instead of patching sleep
        sleep_calls = []

        def track_sleep(delay):
            sleep_calls.append(delay)

        # Setup: All 3 calls return 500 (no success)
        mock_resp_500 = MagicMock(status_code=500)
        self.mock_session.request.side_effect = [mock_resp_500] * 2  # 2 failures

        response = _request(
            session=self.mock_session,
            method="GET",
            url="http://fake.url",
            backoff_seconds=(0.1, 0.2),
            sleep_fn=track_sleep,  # Use our tracking function
        )

        # Assert
        self.assertEqual(
            response.status_code, 500
        )  # The final status code should be 500
        self.assertEqual(self.mock_session.request.call_count, 2)  # Max 2 attempts
        self.assertEqual(len(sleep_calls), 1)  # One sleep between attempts
        self.assertEqual(sleep_calls[0], 0.1)  # First backoff value

    def test_no_retry_on_400_or_404(self):
        """Test that _request does NOT retry on 400 or 404 errors."""
        mock_resp_400 = MagicMock(status_code=400)
        self.mock_session.request.return_value = mock_resp_400

        response = _request(
            session=self.mock_session,
            method="GET",
            url="http://fake.url",
            backoff_seconds=(0.1, 0.2),
            sleep_fn=lambda x: None,
        )

        # Assert
        self.assertEqual(response.status_code, 400)
        self.mock_session.request.assert_called_once()  # No retry

    def test_token_refresh_on_401(self):
        """Test that _request refreshes the token on 401 and retries successfully."""
        # Setup: First call returns 401, second succeeds after refresh
        mock_resp_401 = MagicMock(status_code=401)
        mock_resp_200 = MagicMock(status_code=200)
        mock_resp_200.json.return_value = {"data": "refreshed"}
        self.mock_session.request.side_effect = [mock_resp_401, mock_resp_200]

        # Mock token fetcher
        def mock_token_fetcher():
            return "new_token"

        response = _request(
            session=self.mock_session,
            method="POST",
            url="http://fake.url",
            json={"key": "value"},
            token_fetcher=mock_token_fetcher,
            backoff_seconds=(0.1, 0.2),
            sleep_fn=lambda x: None,
        )

        # Assert
        self.assertEqual(self.mock_session.request.call_count, 2)
        self.assertEqual(response.json(), {"data": "refreshed"})

        # Verify the second call uses the new token
        second_call_headers = self.mock_session.request.call_args_list[1][1]["headers"]
        self.assertEqual(second_call_headers["Authorization"], "Token new_token")

    def test_timeout_handling(self):
        """Test that _request handles timeouts gracefully and returns None."""
        self.mock_session.request.side_effect = requests.exceptions.Timeout(
            "Fake timeout"
        )

        response = _request(
            session=self.mock_session,
            method="GET",
            url="http://fake.url",
            backoff_seconds=(0.1,),
            sleep_fn=lambda x: None,
        )

        # Assert
        self.assertIsNone(response)
        self.mock_session.request.assert_called_once()

    def test_connection_error_handling(self):
        """Test that _request handles connection errors and returns None."""
        self.mock_session.request.side_effect = requests.exceptions.ConnectionError(
            "Fake error"
        )

        response = _request(
            session=self.mock_session,
            method="GET",
            url="http://fake.url",
            backoff_seconds=(0.1,),
            sleep_fn=lambda x: None,
        )

        # Assert
        self.assertIsNone(response)
        self.mock_session.request.assert_called_once()

    def test_empty_backoff_raises(self):
        """_request should raise if backoff_seconds is empty"""
        with self.assertRaises(ValueError) as ctx:
            _request(
                session=self.mock_session,
                method="GET",
                url="http://fake.url",
                backoff_seconds=(),
                sleep_fn=lambda x: None,
            )
        self.assertIn("backoff_seconds", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
