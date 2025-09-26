"""Integration test: StatusData.update attaches app-url for shiny-proxy pods."""

import os
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
        # Try to load cluster config; skip if not available
        kubeconfig = os.getenv("KUBECONFIG")
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
        pods = v1.list_namespaced_pod(namespace=namespace, limit=200, watch=False)
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
        pod = self._find_shinyproxy_pod(namespace)
        if pod is None:
            raise unittest.SkipTest(
                f"No shiny-proxy pod with a 'release' label found in namespace {namespace}"
            )

        print("/nFound a shiny pod: ", pod.metadata.name)
        sd = StatusData(namespace=namespace)
        # Simulate a k8s watch event shape
        sd.update({"object": pod})
        # TODO: This test fails. Add a robust check here:
        self.assertEqual(sd.status_data.get("app-type"), "shiny-proxy")

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
