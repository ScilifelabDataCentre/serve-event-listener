"""Unit tests for app type detection from K8s pod labels/images.

_shiny-proxy_ should be detected from the `app` label (e.g. 'shinyproxy-deployment').
_shiny_ should be detected from common container images when labels do not help.
"""

import unittest
from types import SimpleNamespace

from serve_event_listener.status_data import _detect_app_type


def make_pod(*, labels=None, images=None, annotations=None):
    """Build a minimal pod-like object that _detect_app_type should understand."""
    labels = labels or {}
    annotations = annotations or {}
    images = images or []
    containers = [SimpleNamespace(image=img) for img in images]
    meta = SimpleNamespace(labels=labels, annotations=annotations)
    spec = SimpleNamespace(containers=containers)
    return SimpleNamespace(metadata=meta, spec=spec)


class TestDetectAppType(unittest.TestCase):
    """_detect_app_type should recognize shiny-proxy from the app label and shiny from images."""

    def test_detects_shiny_proxy_from_app_label_exact(self):
        """Label app: shinyproxy-deployment should detect shiny-proxy."""
        pod = make_pod(labels={"app": "shinyproxy-deployment"})
        self.assertEqual(_detect_app_type(pod), "shiny-proxy")

    def test_detects_shiny_proxy_from_app_label_contains(self):
        """An app label that contains 'shinyproxy' should detect shiny-proxy."""
        pod = make_pod(labels={"app": "team-x-shinyproxy-prod"})
        self.assertEqual(_detect_app_type(pod), "shiny-proxy")

    def test_detects_shiny_proxy_case_insensitive(self):
        """Case variations in the app label should still detect shiny-proxy."""
        pod = make_pod(labels={"app": "ShinyProxy-Deployment"})
        self.assertEqual(_detect_app_type(pod), "shiny-proxy")

    def test_detects_shiny_from_common_images(self):
        """A container image like 'rocker/shiny' should detect shiny."""
        pod = make_pod(images=["docker.io/rocker/shiny:4.3"])
        self.assertEqual(_detect_app_type(pod), "shiny")

    def test_detects_shiny_from_rstudio_images(self):
        """A container image containing 'rstudio' should detect shiny."""
        pod = make_pod(images=["quay.io/example/rstudio-shiny:latest"])
        self.assertEqual(_detect_app_type(pod), "shiny")

    def test_returns_none_when_no_signal(self):
        """No useful labels or images should result in None."""
        pod = make_pod(
            labels={"app": "notebook"}, images=["ghcr.io/example/jupyter:1.0"]
        )
        self.assertIsNone(_detect_app_type(pod))


if __name__ == "__main__":
    unittest.main()
