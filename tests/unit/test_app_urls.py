import os
import unittest
from unittest.mock import patch

from serve_event_listener.app_urls import resolve_app_url
from serve_event_listener.el_types import StatusRecord


class TestResolveAppUrl(unittest.TestCase):
    def setUp(self):
        self.rec: StatusRecord = {
            "release": "sp-status",
            "new-status": "Running",
            "event-ts": "2025-09-26T12:00:00.000000Z",
            "app-type": "shiny-proxy",
            "namespace": "default",
        }

    @patch.dict(os.environ, {}, clear=True)
    def test_default_short_dns(self):
        url = resolve_app_url(self.rec)
        self.assertEqual(
            url, "http://sp-status-shinyproxyapp.default:80/app/sp-status/"
        )

    @patch.dict(os.environ, {"APP_URL_DNS_MODE": "fqdn"}, clear=True)
    def test_fqdn_dns(self):
        url = resolve_app_url(self.rec)
        self.assertEqual(
            url,
            "http://sp-status-shinyproxyapp.default.svc.cluster.local:80/app/sp-status/",
        )

    @patch.dict(
        os.environ, {"APP_URL_DNS_SUFFIX": "serve-dev.svc.cluster.local"}, clear=True
    )
    def test_custom_suffix(self):
        url = resolve_app_url(self.rec)
        self.assertEqual(
            url,
            "http://sp-status-shinyproxyapp.default.serve-dev.svc.cluster.local:80/app/sp-status/",
        )

    @patch.dict(
        os.environ,
        {"SHINYPROXY_SERVICE_SUFFIX": "shinyproxyapp", "APP_URL_PORT": "8080"},
        clear=True,
    )
    def test_custom_port(self):
        url = resolve_app_url(self.rec)
        self.assertEqual(
            url, "http://sp-status-shinyproxyapp.default:8080/app/sp-status/"
        )

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_app_type_returns_none(self):
        bad = dict(self.rec)
        bad.pop("app-type", None)
        self.assertIsNone(resolve_app_url(bad))  # type: ignore[arg-type]
