import os
import unittest

import requests

from serve_event_listener.http_client import tls_verify_from_env


class IntegrationTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if os.getenv("RUN_INTEGRATION_TESTS", "0") != "1":
            raise unittest.SkipTest(
                "Skipping integration tests (set RUN_INTEGRATION_TESTS=1 to run)."
            )

        base_url = os.getenv("BASE_URL")
        if not base_url:
            raise unittest.SkipTest("BASE_URL not set")

        ping_url = base_url.rstrip("/") + "/openapi/v1/are-you-there"
        verify = tls_verify_from_env()  # True/False/path

        try:
            r = requests.get(ping_url, timeout=5, verify=verify)
            if r.status_code != 200:
                raise unittest.SkipTest(f"Live API unreachable: {r.status_code}")
        except requests.exceptions.SSLError as e:
            raise unittest.SkipTest(
                f"TLS verify failed. Either set TLS_SSL_VERIFICATION to your cert "
                f"or run with TLS_SSL_VERIFICATION=0 (dev only). Details: {e}"
            )
        except requests.RequestException as e:
            raise unittest.SkipTest(f"Live API unreachable. {e}")
