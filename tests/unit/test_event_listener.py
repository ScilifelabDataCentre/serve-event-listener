import logging
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

from serve_event_listener.event_listener import EventListener


class TestEventListener(unittest.TestCase):
    """Test the EventListener logic by mocking away the server API and Kubernetes."""

    def setUp(self):
        """Create a fresh EventListener for each test."""
        self.el = EventListener()

    # ---------- check_serve_api_status ----------

    @patch("serve_event_listener.event_listener.http_get")
    def test_check_serve_api_status_true(self, mock_get):
        """Returns True on HTTP 200."""
        mock_get.return_value = MagicMock(status_code=200)
        self.assertTrue(self.el.check_serve_api_status())
        mock_get.assert_called_once()

    @patch("serve_event_listener.event_listener.http_get")
    def test_check_serve_api_status_false_on_none(self, mock_get):
        """Returns False when wrapper returns None."""
        mock_get.return_value = None
        self.assertFalse(self.el.check_serve_api_status())

    # ---------- fetch_token ----------

    @patch("serve_event_listener.event_listener.http_post")
    def test_fetch_token_success(self, mock_post):
        """Returns token and updates self.token on success."""
        r = MagicMock(status_code=200)
        r.json.return_value = {"token": "T123"}
        mock_post.return_value = r

        token = self.el.fetch_token()
        self.assertEqual(token, "T123")
        self.assertEqual(self.el.token, "T123")

    @patch("serve_event_listener.event_listener.http_post")
    def test_fetch_token_network_failure_raises(self, mock_post):
        """Raises RequestException on network failure (None)."""
        mock_post.return_value = None
        with self.assertRaises(requests.exceptions.RequestException):
            self.el.fetch_token()

    @patch("serve_event_listener.event_listener.http_post")
    def test_fetch_token_non_json_raises(self, mock_post):
        """Raises RequestException on non-JSON body."""
        r = MagicMock(status_code=200)
        r.json.side_effect = ValueError("not json")
        mock_post.return_value = r
        with self.assertRaises(requests.exceptions.RequestException):
            self.el.fetch_token()

    @patch("serve_event_listener.event_listener.http_post")
    def test_fetch_token_missing_token_raises(self, mock_post):
        """Raises KeyError when 'token' field is missing."""
        r = MagicMock(status_code=200)
        r.json.return_value = {"nope": "x"}
        mock_post.return_value = r
        with self.assertRaises(KeyError):
            self.el.fetch_token()

    @patch("serve_event_listener.event_listener.http_post")
    def test_fetch_token_non_200_raises(self, mock_post):
        """Raises RequestException on non-200 status."""
        r = MagicMock(status_code=401, text="unauthorized")
        mock_post.return_value = r
        with self.assertRaises(requests.exceptions.RequestException):
            self.el.fetch_token()

    # ---------- setup_client ----------

    @patch("serve_event_listener.event_listener.watch.Watch")
    @patch("serve_event_listener.event_listener.client.CoreV1Api")
    @patch("serve_event_listener.event_listener.client.ApiClient")
    @patch("serve_event_listener.event_listener.client.Configuration.get_default_copy")
    @patch("serve_event_listener.event_listener.os.path.exists", return_value=True)
    @patch("serve_event_listener.event_listener.config.load_kube_config")
    def test_setup_client_uses_kubeconfig(
        self, mock_load, _exists, mock_cfg, _api, _core, _watch
    ):
        """Uses kubeconfig when KUBECONFIG path exists."""
        mock_cfg.return_value = SimpleNamespace(debug=False)
        with patch(
            "serve_event_listener.event_listener.KUBECONFIG", "/app/cluster.conf"
        ):
            self.el.setup_client()
        mock_load.assert_called_once_with("/app/cluster.conf")

    @patch("serve_event_listener.event_listener.watch.Watch")
    @patch("serve_event_listener.event_listener.client.CoreV1Api")
    @patch("serve_event_listener.event_listener.client.ApiClient")
    @patch("serve_event_listener.event_listener.client.Configuration.get_default_copy")
    @patch("serve_event_listener.event_listener.os.path.exists", return_value=False)
    @patch(
        "serve_event_listener.event_listener.config.incluster_config.load_incluster_config"
    )
    def test_setup_client_uses_incluster(
        self, mock_incluster, _exists, mock_cfg, _api, _core, _watch
    ):
        """Falls back to in-cluster config when no kubeconfig."""
        mock_cfg.return_value = SimpleNamespace(debug=False)
        with patch("serve_event_listener.event_listener.KUBECONFIG", None):
            self.el.setup_client()
        mock_incluster.assert_called_once()

    # ---------- get_resource_version_from_pod_list ----------

    def test_get_resource_version_from_pod_list(self):
        """Returns resourceVersion from pod list."""
        self.el.client = MagicMock()
        self.el.client.list_namespaced_pod.return_value = SimpleNamespace(
            metadata=SimpleNamespace(resource_version="rv-123")
        )
        rv = self.el.get_resource_version_from_pod_list()
        self.assertEqual(rv, "rv-123")
        self.el.client.list_namespaced_pod.assert_called_once()

    # ---------- setup (happy path) ----------

    @patch("serve_event_listener.event_listener.StatusQueue")
    @patch.object(EventListener, "setup_client")
    @patch.object(EventListener, "check_serve_api_status", return_value=True)
    @patch.object(EventListener, "fetch_token", return_value="T1")
    def test_setup_constructs_queue(self, _fetch, _check, mock_setup_client, mock_SQ):
        """Builds StatusQueue with shared session and token."""

        # When setup_client() is called, simulate it creating the client attribute
        mock_setup_client.side_effect = lambda *a, **k: setattr(
            self.el, "client", MagicMock()
        )

        # Avoid any work inside StatusData
        self.el._status_data = MagicMock()

        self.el.setup()
        self.assertTrue(self.el.setup_complete)
        mock_SQ.assert_called_once()
        args, _ = mock_SQ.call_args
        self.assertIs(args[0], self.el.session)  # session shared
        self.assertIsInstance(args[1], str)  # endpoint
        self.assertEqual(args[2], "T1")  # token

    # ---------- listen (single event then stop) ----------

    @patch("serve_event_listener.event_listener.time.sleep", return_value=None)
    @patch("serve_event_listener.event_listener.threading.Thread")
    def test_listen_processes_event_and_stops_on_exception(self, mock_thread, _sleep):
        """Processes one event and stops on exception."""
        mock_thread.return_value.start.return_value = None

        self.el.setup_complete = True
        self.el._status_queue = MagicMock()
        self.el._status_data = MagicMock()
        self.el._status_data.get_status_record.return_value = {"p": 1}
        self.el.client = MagicMock()
        self.el.client.list_namespaced_pod.return_value = SimpleNamespace(
            metadata=SimpleNamespace(resource_version="rv0")
        )

        logging.disable(logging.CRITICAL)

        def stream_fn(*_a, **_k):
            yield {
                "object": SimpleNamespace(
                    metadata=SimpleNamespace(resource_version="rv1")
                )
            }
            raise Exception(
                "OK. Intentionally raised exception to verify stop processing queue"
            )

        self.el.watch = MagicMock()
        self.el.watch.stream.side_effect = stream_fn

        self.el.listen()

        self.el._status_data.update.assert_called_once()
        self.el._status_queue.add.assert_called_once_with({"p": 1})
        self.el._status_queue.stop_processing.assert_called_once()

        logging.disable(logging.NOTSET)


if __name__ == "__main__":
    unittest.main()
