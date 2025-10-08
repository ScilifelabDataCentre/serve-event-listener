"""Integration test: StatusData.update attaches app-url for shiny-proxy pods."""

import os
import time
import unittest
from unittest.mock import patch

from kubernetes import client, config

from serve_event_listener.el_types import StatusRecord
from serve_event_listener.status_data import StatusData
from tests.integration.base import IntegrationTestCase


class TestStatusDataUrlAttachmentIntegration(IntegrationTestCase):
    """Fetch a real shiny-proxy pod, feed it to StatusData.update, and verify app-url is set."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()  # ensures RUN_INTEGRATION_TESTS gate is applied

        # Try to load cluster config; skip if not available
        kubeconfig = os.getenv("KUBECONFIG")
        print(f"Attempting to use KUBECONFIG = {kubeconfig}")
        try:
            if kubeconfig and os.path.exists(kubeconfig):
                config.load_kube_config(kubeconfig)
            else:
                # try in-cluster; skip if not applicable
                config.incluster_config.load_incluster_config()
        except Exception:
            raise unittest.SkipTest(
                "No K8s config available for StatusData integration"
            )

    def _find_shinyproxy_pod(self, namespace: str):
        v1 = client.CoreV1Api()
        # No 'contains' selector; list and filter in Python
        # Server-side filter: adjust to match your labels
        label_selector = "app in (shinyproxy, shinyproxy-deployment)"

        pods = v1.list_namespaced_pod(
            namespace=namespace,
            label_selector=label_selector,
            limit=200,
            watch=False,
            # client-side timeout: (connect_timeout, read_timeout)
            _request_timeout=(3, 7),
            # server-side timeout (may not be honored)
            timeout_seconds=10,
        )

        for pod in pods.items:
            labels = getattr(pod.metadata, "labels", {}) or {}
            app_label = str(labels.get("app", "")).lower()
            if "shinyproxy" in app_label and labels.get("release"):
                return pod
        return None

    @patch.dict(
        os.environ, {"APP_URL_DNS_MODE": "short", "APP_URL_PORT": "80"}, clear=False
    )
    def test_update_sets_app_url_for_shinyproxy(self):
        """StatusData.update should set app-type=shiny-proxy and app-url for a shiny-proxy pod."""
        namespace = os.getenv("NAMESPACE_UNDER_TEST") or "default"
        print(
            f"Start test. Searching for an existing shiny proxy pod in namespace {namespace}"
        )
        pod = self._find_shinyproxy_pod(namespace)
        if pod is None:
            raise unittest.SkipTest(
                f"No shiny-proxy pod with a 'release' label found in namespace {namespace}"
            )

        print("\nFound a shiny pod: ", pod.metadata.name)
        sd = StatusData(namespace=namespace)
        # Simulate a k8s watch event shape
        t0 = time.time()
        sd.update({"object": pod})
        dt = time.time() - t0
        self.assertLess(
            dt, 1.0, f"StatusData.update() took {dt:.2f}s; it should be pure and fast"
        )

        # Verifying that app-type is set using two approaches
        release = pod.metadata.labels.get("release")
        self.assertIn(release, sd.status_data, f"Release {release!r} not in StatusData")
        raw_rec = sd.status_data[release]
        self.assertIsNotNone(raw_rec)
        self.assertEqual(raw_rec.get("app-type"), "shiny-proxy")

        rec: StatusRecord = sd.get_status_record()
        self.assertEqual(
            rec.get("app-type"),
            "shiny-proxy",
            "app-type should be detected as shiny-proxy",
        )
        self.assertTrue(
            rec.get("app-url"), "app-url should be set for shiny-proxy pods"
        )

        # Sanity-check the host/path shape
        release = rec["release"]
        url = rec["app-url"]  # type: ignore[index]
        self.assertIn(f"{release}-shinyproxyapp.{namespace}", url)
        self.assertTrue(url.endswith(f"/app/{release}/"))
