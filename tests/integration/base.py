import os
import unittest

import requests


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

        try:
            r = requests.get(f"{base_url}/openapi/v1/are-you-there", timeout=5)
        except requests.RequestException as e:
            raise unittest.SkipTest(
                f"Live API unreachable. Is the Target API service really running? {e}"
            )
        if r.status_code != 200:
            raise unittest.SkipTest(f"Live API unhealthy: {r.status_code}")
