"""
Integration test: during a rolling update (same release, old pod terminates),
StatusQueue must NOT post 'Deleted' within the rollout guard window.
"""

import os
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kubernetes import client

import serve_event_listener.status_queue as sq_mod
from serve_event_listener.http_client import make_session, tls_verify_from_env
from serve_event_listener.status_queue import StatusQueue
from tests.integration.base import IntegrationTestCase
from tests.integration.utils import (
    build_shiny_proxy_url,
    find_deployment_for_release,
    iso_at,
    kube_load_config,
)


class TestStatusQueueRolloutIntegration(IntegrationTestCase):
    """Force a rollout and assert we do NOT post Deleted during guard window."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.namespace = os.getenv("NAMESPACE_UNDER_TEST") or "default"
        cls.release = os.getenv("RELEASE_UNDER_TEST")
        if not cls.release:
            raise unittest.SkipTest(
                "Set RELEASE_UNDER_TEST to a shiny/shiny-proxy release label"
            )

        _ = kube_load_config()

        # find a deployment for this release (pod-template labels check)
        apps = client.AppsV1Api()
        dep = find_deployment_for_release(apps, cls.namespace, cls.release)
        if not dep:
            raise unittest.SkipTest(
                f"No Deployment found for release={cls.release!r} in {cls.namespace}"
            )

        cls.deployment_name = dep.metadata.name
        cls.apps = apps

    @patch("serve_event_listener.status_queue.http_post")
    @patch.object(sq_mod, "NXDOMAIN_CONFIRMATION_COUNT", 1)  # make Deleted confirm fast
    @patch.object(
        sq_mod, "APP_PROBE_STATUSES", {"running", "deleted"}
    )  # ensure deleted probe is ON
    def test_rollout_does_not_post_deleted(self, mock_post, *_patched):
        # 1) Force a rollout by patching the deployment template annotation
        self.apps.patch_namespaced_deployment(
            name=self.deployment_name,
            namespace=self.namespace,
            body={
                "spec": {
                    "template": {
                        "metadata": {"annotations": {"qa/retest": str(time.time())}}
                    }
                }
            },
        )

        # 2) Real session (TLS per env), but the prober will be stubbed to be deterministic.
        verify = tls_verify_from_env()
        session = make_session(total_retries=1, verify=verify)

        # 3) Build the queue; patch prober to always say NotFound (the buggy path)
        q = StatusQueue(
            session=session,
            url="https://example.invalid/app-status/",
            token="TEST",
            token_fetcher=None,
            prober=MagicMock(),
        )
        q.prober.probe_url.return_value = SimpleNamespace(
            status="NotFound",
            port80_status=None,
            note="simulated NXDOMAIN",
            url=build_shiny_proxy_url(self.release, self.namespace),
        )

        # Fake POST success; and only check which statuses were posted
        mock_post.return_value = SimpleNamespace(status_code=200, text="ok")

        # 4) Enqueue Running then Deleted for the same release (rollout pattern)
        base = datetime.now(timezone.utc)
        t_run = iso_at(base)
        t_del = iso_at(base + timedelta(seconds=1))

        q.add(
            {
                "release": self.release,
                "status": "Running",
                "event-ts": t_run,
                "app-type": "shiny-proxy",
                "app-url": build_shiny_proxy_url(self.release, self.namespace),
            }
        )
        q.add(
            {
                "release": self.release,
                "status": "Deleted",
                "event-ts": t_del,
                "app-type": "shiny-proxy",
                "app-url": build_shiny_proxy_url(self.release, self.namespace),
            }
        )

        # 5) Run worker briefly (shorter than the guard window)
        worker = threading.Thread(target=q.process, daemon=True)
        worker.start()
        time.sleep(0.5)  # allow a couple cycles; keep < guard window
        q.stop_processing()
        worker.join(timeout=5)

        # 6) Assert: NO Deleted posted (this will FAIL until the guard is implemented)
        posted_deleted = any(
            (kwargs.get("data") or {}).get("new-status") == "Deleted"
            for _, kwargs in mock_post.call_args_list
        )
        self.assertFalse(
            posted_deleted,
            "Regression: Deleted was posted during rollout window (guard not applied).",
        )
